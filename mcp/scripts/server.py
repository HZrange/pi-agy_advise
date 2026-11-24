"""Expose the Antigravity CLI streaming protocol as a local Codex strategic commander tool."""

import asyncio
import hashlib
import inspect
import json
import os
import re
import shlex
import shutil
import sys
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Any, Literal
from uuid import uuid4

import anyio
from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from project import ProjectEvent, ProjectStore, default_db, handle_project
from telemetry import record_feedback, record_invocation


IDLE_SECONDS = 600
Effort = Literal["low", "medium", "high", "xhigh", "max"]
Stance = Literal["global", "red_team", "tactical"]

MAX_READONLY_FILES = 12
MAX_READONLY_FILE_BYTES = 512 * 1024
MAX_READONLY_TOTAL_BYTES = 2 * 1024 * 1024
INLINE_READONLY_MAX_FILE_BYTES = 24 * 1024
INLINE_READONLY_MAX_TOTAL_BYTES = 36 * 1024

_SENSITIVE_DIR_NAMES = frozenset({".ssh", ".aws", ".gnupg", ".kube", ".docker"})
_SENSITIVE_EXACT_NAMES = frozenset(
    {
        ".env",
        "auth.json",
        "credentials.json",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        "id_dsa",
        "shadow",
        "gshadow",
        "projects.sqlite3",
        "invocations.jsonl",
        "feedback.jsonl",
    }
)
_SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".kdbx", ".token")
_SENSITIVE_PREFIXES = (".env.", "id_rsa.", "id_ed25519.", "id_ecdsa.", "id_dsa.")
_SENSITIVE_SUBSTRINGS = ("secret", "credential", "private_key", "api_key", "apikey", "password", "passwd")

SESSION_SETTINGS = {
    "toolPermission": "request-review",
    "allowNonWorkspaceAccess": False,
    "permissions": {
        "deny": [
            "read_file(*)",
            "write_file(*)",
            "command(*)",
            "mcp(*)",
            "read_url(*)",
            "execute_url(*)",
            "download_url(*)",
            "search_web(*)",
            "browser(*)",
            "invoke_subagent(*)",
            "run_workflow(*)",
            "swarm(*)",
        ],
    },
}

# Used ONLY when explicit whitelisted readonly_files are staged into an isolated temp workspace
# alongside a fail-closed PreToolUse hook restricting read_file/view_file to exact staged paths.
READONLY_SESSION_SETTINGS = {
    "toolPermission": "always-proceed",
    "allowNonWorkspaceAccess": False,
    "permissions": {
        "deny": [
            "write_file(*)",
            "command(*)",
            "mcp(*)",
            "read_url(*)",
            "execute_url(*)",
            "download_url(*)",
            "search_web(*)",
            "browser(*)",
            "invoke_subagent(*)",
            "run_workflow(*)",
            "swarm(*)",
        ],
    },
}


@dataclass(frozen=True)
class StagedFileInfo:
    source_path: str
    source_name: str
    staged_path: str
    staged_rel: str
    size_bytes: int
    line_count: int
    sha256: str


class StagedReadonlyWorkspace:
    """Isolated temp workspace holding 0o444 snapshots of whitelisted files + a fail-closed PreToolUse hook."""

    def __init__(self, root: Path, files: list[StagedFileInfo], manifest_path: Path):
        self.root = root
        self.files = files
        self.manifest_path = manifest_path
        self.total_bytes = sum(item.size_bytes for item in files)
        self._staged_to_label: dict[str, str] = {
            str(Path(item.staged_path).resolve()): item.source_name for item in files
        }
        self._staged_to_label[str(manifest_path.resolve())] = "MANIFEST.json"

    def label_for_path(self, raw_path: str) -> str:
        try:
            resolved = str(Path(raw_path).expanduser().resolve())
            if resolved in self._staged_to_label:
                return self._staged_to_label[resolved]
        except OSError:
            pass
        return Path(raw_path).name or raw_path

    def format_prompt_block(self) -> str:
        lines = [
            "Whitelisted Read-Only Evidence Files (STRICTLY READ-ONLY; all writes, commands, network access, and out-of-whitelist reads are blocked by sandbox + PreToolUse hook):",
        ]
        for item in self.files:
            kb = round(item.size_bytes / 1024.0, 1)
            lines.append(
                f"- `{item.staged_path}` (source: `{item.source_name}`, {item.line_count} lines, {kb} KB, sha256={item.sha256})"
            )
        lines.append(
            "Efficiency & safety rules: batch ALL needed `read_file` calls in a SINGLE parallel turn; do NOT read "
            "`MANIFEST.json` and do NOT emit long intermediate narration before your final answer. Cite exact `filename:line` or JSON keys."
        )
        return "\n".join(lines)

    def cleanup(self) -> None:
        if not self.root.exists():
            return
        with suppress(OSError):
            for current_root, dirnames, filenames in os.walk(self.root, topdown=True):
                with suppress(OSError):
                    os.chmod(current_root, 0o700)
                for dname in dirnames:
                    with suppress(OSError):
                        os.chmod(os.path.join(current_root, dname), 0o700)
                for fname in filenames:
                    with suppress(OSError):
                        os.chmod(os.path.join(current_root, fname), 0o600)
        shutil.rmtree(self.root, ignore_errors=True)


def _is_sensitive_path(raw: str, resolved: Path) -> bool:
    home = Path.home().resolve()
    gemini_dir = home / ".gemini"
    codex_auth = home / ".codex" / "auth.json"
    if resolved == codex_auth or gemini_dir in resolved.parents or resolved == gemini_dir:
        return True

    for candidate in (Path(raw).expanduser(), resolved):
        parts_lower = [part.lower() for part in candidate.parts]
        if any(part in _SENSITIVE_DIR_NAMES for part in parts_lower):
            return True
        name = candidate.name.lower()
        if name in _SENSITIVE_EXACT_NAMES:
            return True
        if name.startswith(_SENSITIVE_PREFIXES) or name.endswith(_SENSITIVE_SUFFIXES):
            return True
        if any(token in name for token in _SENSITIVE_SUBSTRINGS):
            return True
    return False


def validate_readonly_files(readonly_files: list[str] | None) -> list[Path]:
    """Validate and resolve an explicit read-only file whitelist before staging or LLM invocation."""
    if not readonly_files:
        return []
    if len(readonly_files) > MAX_READONLY_FILES:
        raise ValueError(f"At most {MAX_READONLY_FILES} readonly_files may be attached per call.")

    resolved_list: list[Path] = []
    seen: set[Path] = set()
    total_bytes = 0

    for raw in readonly_files:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("Each entry in readonly_files must be a non-empty file path.")
        cleaned = raw.strip()
        try:
            resolved = Path(cleaned).expanduser().resolve(strict=True)
        except OSError as error:
            raise ValueError(f"Whitelisted readonly file does not exist or is unreadable: {cleaned}") from error

        if not resolved.is_file():
            raise ValueError(f"Whitelisted readonly path must be a regular file (directories not allowed): {cleaned}")
        if _is_sensitive_path(cleaned, resolved):
            raise ValueError(f"Refusing to expose sensitive/credential path in readonly_files: {cleaned}")

        if resolved in seen:
            continue

        try:
            size = resolved.stat().st_size
        except OSError as error:
            raise ValueError(f"Unable to stat readonly file: {cleaned}") from error
        if size > MAX_READONLY_FILE_BYTES:
            raise ValueError(
                f"Readonly file exceeds per-file limit ({size} > {MAX_READONLY_FILE_BYTES} bytes): {cleaned}"
            )
        total_bytes += size
        if total_bytes > MAX_READONLY_TOTAL_BYTES:
            raise ValueError(
                f"Combined readonly_files size exceeds total limit ({total_bytes} > {MAX_READONLY_TOTAL_BYTES} bytes)."
            )

        try:
            with resolved.open("rb") as handle:
                head = handle.read(8192)
        except OSError as error:
            raise ValueError(f"Unable to read readonly file: {cleaned}") from error
        if b"\x00" in head:
            raise ValueError(f"Binary files are not permitted in readonly_files: {cleaned}")

        seen.add(resolved)
        resolved_list.append(resolved)

    return resolved_list


def _inline_readonly_enabled() -> bool:
    return os.getenv("AGY_ADVISE_INLINE_READONLY", "on").strip().lower() not in ("off", "0", "false", "no")


def try_build_inline_readonly_bundle(resolved_files: list[Path]) -> tuple[str, list[str], int] | None:
    """Inline small whitelisted UTF-8 files with 1-based line numbers to eliminate multi-turn read_file latency.

    Returns (prompt_block, file_names, total_bytes) when eligible, or None to fall back to staged_hook mode.
    """
    if not resolved_files or not _inline_readonly_enabled():
        return None
    total_bytes = 0
    payloads: list[tuple[Path, bytes, str]] = []
    for src in resolved_files:
        try:
            raw = src.read_bytes()
        except OSError:
            return None
        if len(raw) > INLINE_READONLY_MAX_FILE_BYTES:
            return None
        total_bytes += len(raw)
        if total_bytes > INLINE_READONLY_MAX_TOTAL_BYTES:
            return None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
        payloads.append((src, raw, text))

    sections = [
        "Whitelisted Read-Only Evidence Files (INLINED DIRECTLY with 1-based line numbers; zero tool calls needed — verify directly and cite `filename:line` or JSON keys):"
    ]
    names: list[str] = []
    for idx, (src, raw, text) in enumerate(payloads, 1):
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", src.name) or f"file_{idx}"
        label = f"{idx:02d}_{safe_name}"
        sha = hashlib.sha256(raw).hexdigest()[:12]
        lines = text.splitlines()
        kb = round(len(raw) / 1024.0, 1)
        numbered = "\n".join(f"{line_no}: {line}" for line_no, line in enumerate(lines, 1))
        sections.append(
            f"=== FILE: {label} (source: {src.name}, {len(lines)} lines, {kb} KB, sha256={sha}) ===\n"
            f"{numbered}\n"
            f"=== END FILE: {label} ==="
        )
        names.append(src.name)
    return "\n\n".join(sections), names, total_bytes


def prepare_readonly_workspace(base_dir: str | Path, resolved_files: list[Path]) -> StagedReadonlyWorkspace:
    """Stage 0o444 snapshots of validated files into an isolated directory with a fail-closed PreToolUse hook."""
    root = Path(base_dir) / f"ro-{uuid4().hex[:12]}"
    root.mkdir(parents=True, mode=0o700)
    ev_dir = root / "readonly_evidence"
    ev_dir.mkdir(mode=0o755)

    staged_infos: list[StagedFileInfo] = []
    allowed_paths: list[str] = []

    try:
        for idx, src in enumerate(resolved_files, 1):
            safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", src.name) or f"file_{idx}"
            staged_name = f"{idx:02d}_{safe_name}"
            dst = ev_dir / staged_name
            raw_bytes = src.read_bytes()
            dst.write_bytes(raw_bytes)
            dst.chmod(0o444)

            resolved_dst = str(dst.resolve())
            allowed_paths.append(resolved_dst)
            line_count = raw_bytes.count(b"\n") + (1 if raw_bytes and not raw_bytes.endswith(b"\n") else 0)
            staged_infos.append(
                StagedFileInfo(
                    source_path=str(src),
                    source_name=src.name,
                    staged_path=resolved_dst,
                    staged_rel=f"readonly_evidence/{staged_name}",
                    size_bytes=len(raw_bytes),
                    line_count=line_count,
                    sha256=hashlib.sha256(raw_bytes).hexdigest()[:12],
                )
            )

        manifest_path = ev_dir / "MANIFEST.json"
        manifest_payload = {
            "mode": "readonly_whitelist",
            "files": [
                {
                    "staged_path": item.staged_path,
                    "staged_rel": item.staged_rel,
                    "source_path": item.source_path,
                    "source_name": item.source_name,
                    "bytes": item.size_bytes,
                    "lines": item.line_count,
                    "sha256": item.sha256,
                }
                for item in staged_infos
            ],
        }
        manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest_path.chmod(0o444)
        allowed_paths.append(str(manifest_path.resolve()))
        ev_dir.chmod(0o555)

        # Install fail-closed PreToolUse hook restricting ALL tool calls strictly to read_file/view_file on ALLOWED.
        agents_dir = root / ".agents"
        agents_dir.mkdir(mode=0o700)
        guard_path = agents_dir / "readonly_guard.py"
        guard_source = (
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "from pathlib import Path\n"
            f"ALLOWED = frozenset({sorted(allowed_paths)!r})\n"
            f"BASE_CWD = {str(root.resolve())!r}\n"
            "def deny(reason: str) -> None:\n"
            "    print(json.dumps({'decision': 'deny', 'reason': reason}))\n"
            "    raise SystemExit(0)\n"
            "try:\n"
            "    data = json.load(sys.stdin)\n"
            "    tc = data.get('toolCall') or {}\n"
            "    name = str(tc.get('name') or '').strip().lower()\n"
            "    args = tc.get('args') if isinstance(tc.get('args'), dict) else {}\n"
            "    if name not in ('read_file', 'view_file'):\n"
            "        deny(f'Tool {name!r} is blocked: only read_file/view_file on whitelisted files is allowed.')\n"
            "    raw = args.get('Path') or args.get('path') or args.get('AbsolutePath') or args.get('file_path') or ''\n"
            "    if not isinstance(raw, str) or not raw.strip():\n"
            "        deny('Missing file path for read-only tool call.')\n"
            "    candidate = Path(raw.strip()).expanduser()\n"
            "    if not candidate.is_absolute():\n"
            "        cwd = data.get('cwd') if isinstance(data.get('cwd'), str) and data.get('cwd') else BASE_CWD\n"
            "        candidate = Path(cwd) / candidate\n"
            "    resolved = str(candidate.resolve())\n"
            "    if resolved in ALLOWED:\n"
            "        print(json.dumps({'decision': 'allow'}))\n"
            "    else:\n"
            "        deny(f'Path {raw!r} is outside the explicit readonly_files whitelist.')\n"
            "except Exception as exc:\n"
            "    deny(f'Read-only whitelist guard error: {exc}')\n"
        )
        guard_path.write_text(guard_source, encoding="utf-8")
        guard_path.chmod(0o700)

        hooks_payload = {
            "readonly-whitelist": {
                "PreToolUse": [
                    {
                        "matcher": "*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(guard_path))}",
                                "timeout": 5,
                            }
                        ],
                    }
                ]
            }
        }
        hooks_path = agents_dir / "hooks.json"
        hooks_path.write_text(json.dumps(hooks_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        hooks_path.chmod(0o600)
        return StagedReadonlyWorkspace(root=root, files=staged_infos, manifest_path=manifest_path)
    except BaseException:
        StagedReadonlyWorkspace(root=root, files=staged_infos, manifest_path=ev_dir / "MANIFEST.json").cleanup()
        raise


def _build_child_env(
    base_env: dict[str, str] | None = None,
    *,
    platform: str | None = None,
    uid: int | None = None,
    bus_path_override: Path | None = None,
) -> dict[str, str]:
    """Build subprocess env for agy so SSH sessions reuse an unlocked local Linux D-Bus/GNOME keyring.

    When SSH_CONNECTION/SSH_CLIENT/SSH_TTY are inherited, agy >= 1.5.16 switches to file-based token
    storage and may miss an already-unlocked local GNOME keyring. On Linux, when the local session bus
    socket `/run/user/<uid>/bus` exists (unless `AGY_ADVISE_KEEP_SSH_ENV=1`), strip SSH markers and
    point `DBUS_SESSION_BUS_ADDRESS` at that bus so agy reuses the unlocked keyring.
    """
    env = dict(os.environ if base_env is None else base_env)
    if env.get("AGY_ADVISE_KEEP_SSH_ENV", "").strip().lower() in ("1", "true", "yes", "on"):
        return env
    effective_platform = sys.platform if platform is None else platform
    if not effective_platform.startswith("linux"):
        return env
    effective_uid = os.getuid() if (uid is None and hasattr(os, "getuid")) else uid
    if effective_uid is None:
        return env
    bus_path = bus_path_override if bus_path_override is not None else Path(f"/run/user/{effective_uid}/bus")
    try:
        bus_available = bus_path.exists()
    except OSError:
        bus_available = False
    if bus_available:
        for var in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"):
            env.pop(var, None)
        if not env.get("DBUS_SESSION_BUS_ADDRESS"):
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus_path}"
    return env


def _read_cli_default_model() -> str | None:
    """Best-effort read of the local agy CLI default model setting for audit visibility."""
    candidates = (
        Path.home() / ".gemini" / "antigravity-cli" / "settings.json",
        Path.home() / ".gemini" / "antigravity" / "settings.json",
    )
    for path in candidates:
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                model = data.get("model")
                if isinstance(model, str) and model.strip():
                    return model.strip()
        except (OSError, ValueError, TypeError):
            continue
    return None


def build_advice_prompt(
    question: str,
    context: str | None,
    stance: Stance,
    *,
    is_followup: bool,
    has_readonly_files: bool = False,
) -> str:
    """Build a domain-agnostic, first-principles, anti-local-optimum advisor prompt."""
    sections = []
    if is_followup:
        sections.append(
            "Continue this independent review from the new evidence supplied below. "
            "Revise earlier conclusions if the new facts contradict them, challenge any emerging "
            "local-optimum tunnel vision, and keep separating confirmed facts, inferences, and missing facts."
        )
    elif has_readonly_files:
        sections.append(
            "Ground your judgment in the material supplied below AND the whitelisted read-only files attached at the "
            "bottom (either inlined with 1-based line numbers or staged in `readonly_evidence/`). Verify key code/JSON "
            "facts directly before concluding — never trust a summary over the actual file, and cite exact `filename:line` "
            "or JSON keys. If files are staged for `read_file`, batch all reads in one parallel turn without reading `MANIFEST.json`."
        )
    else:
        sections.append(
            "Answer in text strictly from the material supplied in this conversation. "
            "Do not invent unseen files or unstated facts; if critical evidence is missing, name it explicitly."
        )

    sections.append(f"Question:\n{question}")
    if context:
        sections.append(f"Context:\n{context}")

    if stance == "red_team":
        sections.append(
            "Stance (Red-Team Audit): Construct the strongest evidence-based case for why the proposed "
            "approach, architecture, or hypothesis will fail to achieve the real end goal — even if the "
            "caller or project owner already favors it. Classify the dominant bottleneck "
            "(implementation_bug vs data_coverage vs optimization_mismatch vs constraint_or_capacity), "
            "expose hidden assumptions, local-optimum traps, proxy-vs-real-goal drift, and efficiency traps. "
            "Name the cheapest falsifying check, offer a structurally different alternative if warranted, and "
            "state clearly whether this branch should stop or pivot."
        )
    elif stance == "tactical":
        sections.append(
            "Stance (Focused Tactical): First check in 1-2 sentences whether this tactical question "
            "reflects over-constrained micromanagement or optimizing a dead-end proxy; if so, voice clear "
            "dissent before answering. Then answer directly from first principles for the stated scope, "
            "distinguish confirmed facts from assumptions, and state concrete pass/stop criteria without "
            "over-prescribing brittle local accounting details."
        )
    elif not is_followup:
        sections.append(
            "Stance (Global-First Independent Commander):\n"
            "1. Zoom out before zooming in: first check whether the question frames a local symptom, "
            "local optimum, unverified proxy, or sunk-cost patch rather than the real main bottleneck "
            "against the overarching goal.\n"
            "2. Anti-sycophancy & fearless dissent: do NOT rubber-stamp proposed options or defer merely "
            "because the caller or owner already leans one way. You are explicitly authorized to challenge the premise, "
            "lead with your **Dissent / Counter-Case** when verified evidence exposes a flaw or better macro path, "
            "and derive the right direction from first principles.\n"
            "3. Classify the dominant root cause (implementation_bug / data_coverage / optimization_mismatch / "
            "constraint_or_capacity) and optimize for real end-to-end progress per unit compute + wall-clock time.\n"
            "4. Separate confirmed facts, inferences, and critical unknowns that could overturn your judgment.\n"
            "5. Give a decisive strategic recommendation, the highest-leverage next step (plus any "
            "independent parallel tracks), and a concrete falsification or stop condition. Leave local "
            "implementation arithmetic/flags to the executor. Match the language of the question."
        )
    return "\n\n".join(sections)


class Advisor:
    def __init__(self, executable: str, directory: str):
        self.executable = executable
        self.directory = directory
        self.lock = asyncio.Lock()
        self.conversations: dict[str, str] = {}
        self.session_efforts: dict[str, Effort] = {}
        self.session_models: dict[str, str | None] = {}
        self.session_readonly_files: dict[str, tuple[str, ...]] = {}
        self.session_workspaces: dict[str, StagedReadonlyWorkspace] = {}
        self.active_session: str | None = None
        self.active_schema: str | None = None
        self.active_cwd: str | None = None
        self.active_readonly: bool = False
        self.process: asyncio.subprocess.Process | None = None
        self.idle: asyncio.Task[None] | None = None
        self.stderr_task: asyncio.Task | None = None
        self.stderr_tail = b""
        self.stderr_fatal: asyncio.Event | None = None
        self.stderr_error: RuntimeError | None = None

    def cleanup_workspaces(self) -> None:
        for workspace in list(self.session_workspaces.values()):
            workspace.cleanup()
        self.session_workspaces.clear()
        self.session_readonly_files.clear()

    async def drain_stderr(self, stream: asyncio.StreamReader) -> None:
        while chunk := await stream.read(4096):
            self.stderr_tail = (self.stderr_tail + chunk)[-4096:]
            lower = self.stderr_tail.decode(errors="replace").lower()
            if any(
                term in lower
                for term in ("authentication required", "please visit the url to log in", "not logged in")
            ):
                self.stderr_error = RuntimeError(
                    "Antigravity CLI needs an authenticated session. Sign in with agy on this host."
                )
                if self.stderr_fatal is not None:
                    self.stderr_fatal.set()

    def cancel_idle(self) -> None:
        if self.idle is not None:
            self.idle.cancel()
            self.idle = None

    async def expire(self) -> None:
        await asyncio.sleep(IDLE_SECONDS)
        async with self.lock:
            self.idle = None
            await self.stop(graceful=True)

    @staticmethod
    async def drain_and_wait(process: asyncio.subprocess.Process) -> None:
        while await process.stdout.read(65536):
            pass
        await process.wait()

    async def stop(self, *, graceful: bool = False) -> None:
        self.cancel_idle()
        process = self.process
        if process is None:
            return
        process.stdin.close()
        if graceful and process.returncode is None:
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.drain_and_wait(process), 1)
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(self.drain_and_wait(process), 2)
            except asyncio.TimeoutError:
                with suppress(ProcessLookupError):
                    process.kill()
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self.drain_and_wait(process), 2)
        if self.stderr_task is not None:
            self.stderr_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.stderr_task
            self.stderr_task = None
        self.process = None
        self.active_session = None
        self.active_schema = None
        self.active_cwd = None
        self.active_readonly = False
        self.stderr_fatal = None
        self.stderr_error = None

    async def start(
        self,
        session_id: str,
        effort: Effort | None = None,
        model: str | None = None,
        schema_json: str | None = None,
        *,
        cwd: str | None = None,
        readonly_mode: bool = False,
    ) -> None:
        await self.stop()
        # The CLI's headless default avoids Plan mode's artifact approval step.
        settings_payload = READONLY_SESSION_SETTINGS if readonly_mode else SESSION_SETTINGS
        effective_cwd = cwd or self.directory
        arguments = [
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--sandbox",
            "--disable-slash-commands",
            "--settings", json.dumps(settings_payload),
        ]
        if effort is not None:
            arguments.extend(["--effort", effort])
        if model is not None:
            arguments.extend(["--model", model])
        if schema_json is not None:
            arguments.extend(["--json-schema", schema_json])
        if conversation := self.conversations.get(session_id):
            arguments.extend(["--conversation", conversation])
        self.stderr_tail = b""
        self.stderr_fatal = asyncio.Event()
        self.stderr_error = None
        self.process = await asyncio.create_subprocess_exec(
            self.executable,
            *arguments,
            cwd=effective_cwd,
            env=_build_child_env(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=4 * 1024 * 1024,
        )
        self.active_session = session_id
        self.active_schema = schema_json
        self.active_cwd = effective_cwd
        self.active_readonly = readonly_mode
        self.stderr_task = asyncio.create_task(self.drain_stderr(self.process.stderr))

    def failure(self, message: str, detail: object = "") -> RuntimeError:
        diagnostic = f"{detail} {self.stderr_tail.decode(errors='replace')}".lower()
        if any(term in diagnostic for term in ("sign in", "sign-in", "not logged in", "authenticat")):
            return RuntimeError("Antigravity CLI needs an authenticated session. Sign in with agy on this host.")
        if any(term in diagnostic for term in ("quota", "resource_exhausted", "rate limit")):
            return RuntimeError("Antigravity reported a quota or rate limit.")
        return RuntimeError(message)

    async def _readline_or_fatal(self, process: asyncio.subprocess.Process) -> bytes:
        if self.stderr_error is not None:
            raise self.stderr_error
        read_task = asyncio.create_task(process.stdout.readline())
        fatal_event = self.stderr_fatal
        if fatal_event is None:
            return await read_task
        fatal_task = asyncio.create_task(fatal_event.wait())
        try:
            done, pending = await asyncio.wait({read_task, fatal_task}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            if fatal_task in done and self.stderr_error is not None:
                raise self.stderr_error
            return read_task.result()
        except BaseException:
            for task in (read_task, fatal_task):
                if not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
            raise

    async def exchange(
        self,
        prompt: str,
        session_id: str,
        effort: Effort | None,
        model: str | None,
        schema_json: str | None = None,
        single_turn: bool = False,
        workspace: StagedReadonlyWorkspace | None = None,
    ) -> dict[str, object]:
        effective_cwd = str(workspace.root) if workspace is not None else self.directory
        readonly_mode = workspace is not None
        process = self.process
        if (
            process is None
            or self.active_session != session_id
            or self.active_schema != schema_json
            or self.active_cwd != effective_cwd
            or self.active_readonly != readonly_mode
            or process.returncode is not None
            or process.stdin.is_closing()
        ):
            await self.start(
                session_id,
                effort,
                model,
                schema_json,
                cwd=effective_cwd,
                readonly_mode=readonly_mode,
            )
            process = self.process
        else:
            self.stderr_tail = b""
            if self.stderr_fatal is not None:
                self.stderr_fatal.clear()
            self.stderr_error = None
        message = {"event": "user", "message": {"content": prompt}}
        try:
            process.stdin.write(json.dumps(message, ensure_ascii=False).encode() + b"\n")
            await process.stdin.drain()
            if single_turn:
                # Signal EOF so single-turn --json-schema stream-json knows this is the final turn.
                process.stdin.close()
        except (BrokenPipeError, ConnectionResetError) as error:
            process.stdin.close()
            with suppress(BrokenPipeError, ConnectionResetError, asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(process.stdin.wait_closed()), 0.25)
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(self.stderr_task), 0.25)
            raise self.failure("Antigravity CLI closed its input before receiving the question.") from error

        init_model: str | None = None
        files_read: list[str] = []
        denied_calls: list[str] = []

        while line := await self._readline_or_fatal(process):
            try:
                event = json.loads(line)
            except (UnicodeError, ValueError):
                continue
            if not isinstance(event, dict):
                continue
            ev_type = event.get("event")
            if ev_type == "init":
                if isinstance(event.get("conversation_id"), str):
                    self.conversations[session_id] = event["conversation_id"]
                init_block = event.get("init") if isinstance(event.get("init"), dict) else {}
                candidate_model = event.get("model") or init_block.get("model")
                if isinstance(candidate_model, str) and candidate_model.strip():
                    init_model = candidate_model.strip()
            elif ev_type == "step_update":
                su = event.get("step_update") if isinstance(event.get("step_update"), dict) else {}
                if su.get("step_type") == "tool":
                    tname = str(su.get("tool_name") or "").lower()
                    tstate = su.get("state")
                    tinfo = su.get("tool_info") if isinstance(su.get("tool_info"), dict) else {}
                    if tstate == "DONE" and tname in ("read_file", "view_file"):
                        params = tinfo.get("parameters") if isinstance(tinfo.get("parameters"), dict) else {}
                        raw_p = params.get("Path") or params.get("path") or params.get("AbsolutePath") or ""
                        if isinstance(raw_p, str) and raw_p:
                            label = workspace.label_for_path(raw_p) if workspace is not None else Path(raw_p).name
                            if label not in files_read:
                                files_read.append(label)
                    elif tstate == "ERROR":
                        err_obj = tinfo.get("error") if isinstance(tinfo.get("error"), dict) else {}
                        err_msg = str(err_obj.get("message") or tinfo.get("error") or "")
                        if any(tok in err_msg.lower() for tok in ("denied", "blocked", "permission")):
                            denied_calls.append(f"{tname}:{err_msg[:120]}")
            if ev_type != "result":
                continue
            result = event.get("result")
            if not isinstance(result, dict):
                raise self.failure("Antigravity returned an invalid result.")
            conversation = result.get("conversation_id") or event.get("conversation_id")
            if isinstance(conversation, str):
                self.conversations[session_id] = conversation
            if result.get("status") != "SUCCESS":
                raise self.failure("Antigravity reported a failed turn.", result.get("error"))

            structured = result.get("structured_output")
            raw_response = result.get("response")
            raw_response_str = raw_response.strip() if isinstance(raw_response, str) else ""
            if isinstance(structured, dict):
                advice = json.dumps(structured, ensure_ascii=False)
            else:
                advice = raw_response_str
            if not isinstance(advice, str) or not advice.strip():
                raise self.failure("Antigravity returned no advice. Try a self-contained text question.")
            if session_id not in self.conversations:
                raise self.failure("Antigravity returned advice without a conversation ID.")

            reported_model = result.get("model") or event.get("model") or init_model
            reply: dict[str, object] = {
                "advice": advice.strip(),
                "raw_response_chars": len(raw_response_str) if raw_response_str else len(advice.strip()),
                "session_id": session_id,
                "duration_seconds": result.get("duration_seconds"),
                "usage": result.get("usage"),
                "model_reported": reported_model if isinstance(reported_model, str) else None,
            }
            if isinstance(structured, dict):
                reply["structured_output"] = structured
            if workspace is not None:
                reply["readonly_grounding_mode"] = "staged_hook"
                reply["readonly_files_staged"] = [item.source_name for item in workspace.files]
                reply["readonly_files_bytes"] = workspace.total_bytes
                reply["readonly_files_read"] = files_read
                reply["readonly_denied_calls"] = denied_calls
            return reply
        await process.wait()
        await self.stderr_task
        raise self.failure(f"Antigravity CLI exited before replying (exit {process.returncode}).")

    async def ask(
        self,
        prompt: str,
        session_id: str | None,
        timeout: int,
        effort: Effort | None = None,
        model: str | None = None,
        json_schema: dict[str, Any] | None = None,
        readonly_files: list[str] | None = None,
    ) -> dict[str, object]:
        clock = asyncio.get_running_loop().time
        started = clock()
        resolved_ro = validate_readonly_files(readonly_files)
        ro_signature = tuple(str(path) for path in resolved_ro)
        queue_timeout = f"Antigravity timed out after {timeout} seconds waiting for the previous advisor turn."
        try:
            await asyncio.wait_for(self.lock.acquire(), timeout)
        except asyncio.TimeoutError as error:
            raise RuntimeError(queue_timeout) from error

        ephemeral_workspace: StagedReadonlyWorkspace | None = None
        try:
            queue_seconds = clock() - started
            remaining = timeout - queue_seconds
            if remaining <= 0:
                raise RuntimeError(queue_timeout)

            workspace: StagedReadonlyWorkspace | None = None
            inline_bundle: tuple[str, list[str], int] | None = None
            if session_id is not None:
                if session_id not in self.conversations:
                    raise ValueError("Unknown advisor session. Omit session_id to start a new conversation.")
                saved_effort = self.session_efforts.get(session_id)
                if effort is not None and effort != saved_effort:
                    raise ValueError("Omit session_id to start a conversation with a different reasoning effort.")
                effort = saved_effort
                saved_model = self.session_models[session_id]
                if model is not None and model != saved_model:
                    raise ValueError("Omit session_id to start a conversation with a different model.")
                model = saved_model
                saved_ro = self.session_readonly_files.get(session_id, ())
                if ro_signature and ro_signature != saved_ro:
                    raise ValueError(
                        "Omit session_id to start a new conversation with a different readonly_files whitelist."
                    )
                workspace = self.session_workspaces.get(session_id)
            else:
                model = model or os.getenv("AGY_ADVISE_MODEL") or None
                if resolved_ro:
                    inline_bundle = try_build_inline_readonly_bundle(resolved_ro)
                    if inline_bundle is None:
                        workspace = prepare_readonly_workspace(self.directory, resolved_ro)
                        ephemeral_workspace = workspace

            effective_prompt = prompt
            if inline_bundle is not None and session_id is None:
                effective_prompt = f"{prompt}\n\n{inline_bundle[0]}"
            elif workspace is not None and session_id is None:
                effective_prompt = f"{prompt}\n\n{workspace.format_prompt_block()}"

            use_schema = (
                json_schema is not None
                and os.getenv("AGY_ADVISE_JSON_SCHEMA", "on").strip().lower() not in ("off", "0", "false", "no")
            )
            schema_json = json.dumps(json_schema, ensure_ascii=False) if use_schema else None
            single_turn = schema_json is not None and session_id is None
            self.cancel_idle()
            key = session_id or uuid4().hex
            try:
                reply = await asyncio.wait_for(
                    self.exchange(
                        effective_prompt,
                        key,
                        effort,
                        model,
                        schema_json,
                        single_turn=single_turn,
                        workspace=workspace,
                    ),
                    remaining,
                )
            except BaseException as error:
                timeout_err = (
                    self.failure(f"Antigravity timed out after {timeout} seconds, including queue wait.")
                    if isinstance(error, asyncio.TimeoutError)
                    else None
                )
                with anyio.CancelScope(shield=True):
                    await self.stop()
                if session_id is None:
                    self.conversations.pop(key, None)
                    if ephemeral_workspace is not None:
                        ephemeral_workspace.cleanup()
                        ephemeral_workspace = None
                if timeout_err is not None:
                    raise timeout_err from error
                raise
            if single_turn:
                await self.stop(graceful=True)
                if ephemeral_workspace is not None:
                    ephemeral_workspace.cleanup()
                    ephemeral_workspace = None
            else:
                if effort is not None:
                    self.session_efforts[key] = effort
                self.session_models[key] = model
                if session_id is None:
                    self.session_readonly_files[key] = ro_signature
                    if ephemeral_workspace is not None:
                        self.session_workspaces[key] = ephemeral_workspace
                        ephemeral_workspace = None
                self.idle = asyncio.create_task(self.expire())

            if inline_bundle is not None:
                _, inline_names, inline_bytes = inline_bundle
                reply["readonly_grounding_mode"] = "inline"
                reply["readonly_files_staged"] = inline_names
                reply["readonly_files_bytes"] = inline_bytes
                reply["readonly_files_read"] = list(inline_names)
                reply["readonly_denied_calls"] = []

            configured_model = model or _read_cli_default_model()
            reply.update(
                queue_seconds=round(queue_seconds, 3),
                elapsed_seconds=round(clock() - started, 3),
                requested_effort=effort,
                model_requested=model,
                model_configured=configured_model,
                prompt_chars=len(effective_prompt),
            )
            return reply
        finally:
            if ephemeral_workspace is not None:
                ephemeral_workspace.cleanup()
            self.lock.release()


def _classify_response_status(response: dict[str, object]) -> str:
    review_status = response.get("review_status")
    if review_status == "transport_error":
        error_text = str(response.get("error") or "").lower()
        return "timeout" if "timed out" in error_text else "error"
    if review_status == "invalid_decision":
        return "error"
    return "ok"


def _classify_exception_status(error: BaseException) -> str:
    if isinstance(error, ValueError):
        return "validation_error"
    if isinstance(error, asyncio.CancelledError):
        return "cancelled"
    if "timed out" in str(error).lower():
        return "timeout"
    return "error"


async def execute_advise(
    *,
    store: ProjectStore,
    advisor: Advisor,
    question: str = "",
    context: str | None = None,
    session_id: str | None = None,
    timeout_seconds: int = 240,
    effort: Effort | None = None,
    model: str | None = None,
    stance: Stance | None = None,
    readonly_files: list[str] | None = None,
    project: ProjectEvent | None = None,
) -> dict[str, object]:
    """Execute one agy_advise call and append a lightweight local audit record."""
    clock = asyncio.get_running_loop().time
    started = clock()
    invocation = {"id": uuid4().hex, "tool": "agy_advise", "transport": "mcp"}
    is_project = project is not None
    mode = "project" if is_project else "qa"
    action = project.action if is_project else ("followup" if session_id is not None else "ask")
    resolved_stance: Stance | None = None if is_project else (stance or "global")
    project_id = getattr(project, "id", None) if is_project else None
    phase = getattr(project, "phase", None) if is_project else None

    response: dict[str, object] | None = None
    prompt_chars: int | None = None
    status = "ok"
    error_text: str | None = None
    try:
        validated_ro = validate_readonly_files(readonly_files) if readonly_files else []
        ro_str_list = [str(p) for p in validated_ro] if validated_ro else None

        if project is not None:
            if session_id is not None:
                raise ValueError("Project reviews use durable evidence and a fresh advisor session.")
            if stance is not None:
                raise ValueError("Project reviews use their own structured clean-room review protocol.")
            response = await handle_project(
                store,
                project,
                advisor,
                invocation,
                question,
                context,
                timeout_seconds,
                effort,
                model,
                feedback_recorder=record_feedback,
                readonly_files=ro_str_list,
            )
            if isinstance(response.get("prompt_chars"), int):
                prompt_chars = response["prompt_chars"]
            status = _classify_response_status(response)
            return response

        if not question.strip():
            raise ValueError("Supply a self-contained question.")
        prompt = build_advice_prompt(
            question,
            context,
            resolved_stance or "global",
            is_followup=session_id is not None,
            has_readonly_files=bool(ro_str_list),
        )
        prompt_chars = len(prompt)
        ask_kwargs: dict[str, Any] = {}
        if ro_str_list:
            try:
                params = inspect.signature(advisor.ask).parameters
                if "readonly_files" in params or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
                ):
                    ask_kwargs["readonly_files"] = ro_str_list
            except (TypeError, ValueError):
                pass
        reply = await advisor.ask(prompt, session_id, timeout_seconds, effort, model, **ask_kwargs)
        if isinstance(reply.get("prompt_chars"), int):
            prompt_chars = reply["prompt_chars"]
        response = {"invocation": invocation, "requested_stance": resolved_stance, **reply}
        return response
    except BaseException as error:
        status = _classify_exception_status(error)
        error_text = str(error) or type(error).__name__
        raise
    finally:
        elapsed = clock() - started
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(
                lambda: record_invocation(
                    store.path,
                    invocation_id=invocation["id"],
                    mode=mode,
                    action=action,
                    status=status,
                    elapsed_seconds=elapsed,
                    question=question,
                    context=context,
                    prompt_chars=prompt_chars,
                    stance=resolved_stance,
                    is_followup=session_id is not None,
                    session_id=session_id,
                    effort=effort,
                    model_requested=model,
                    project_id=project_id,
                    phase=phase,
                    response=response,
                    error=error_text,
                )
            )


def server(executable: str) -> FastMCP:
    store = ProjectStore(default_db())

    @asynccontextmanager
    async def lifespan(_server):
        with TemporaryDirectory(prefix="codex-agy-") as directory:
            advisor = Advisor(executable, directory)
            try:
                yield advisor
            finally:
                with anyio.CancelScope(shield=True):
                    async with advisor.lock:
                        await advisor.stop()
                        advisor.cleanup_workspaces()

    mcp = FastMCP("Antigravity Advisor", lifespan=lifespan)

    @mcp.tool()
    async def agy_advise(
        ctx: Context,
        question: Annotated[str, Field(description="Self-contained question for Antigravity.")] = "",
        context: Annotated[str | None, Field(description="Minimal structured facts authorized for sharing with Google.")] = None,
        session_id: Annotated[str | None, Field(description="Previous ID for a follow-up. Omit to start fresh.")] = None,
        timeout_seconds: Annotated[int, Field(ge=1, le=240, description="Turn budget including any queue wait.")] = 240,
        effort: Annotated[
            Effort | None,
            Field(description="Reasoning effort for a new conversation. Omit for the CLI default or to inherit on a follow-up."),
        ] = None,
        model: Annotated[str | None, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")] = None,
        stance: Annotated[
            Stance | None,
            Field(description="Advisor stance for Q&A: global (default), red_team, or tactical."),
        ] = None,
        readonly_files: Annotated[
            list[str] | None,
            Field(
                max_length=MAX_READONLY_FILES,
                description=(
                    "Optional explicit whitelist of local code/JSON files to stage as 0o444 read-only snapshots "
                    "for primary-artifact verification (max 12 files, <=512KB each, <=2MB total; writes, commands, "
                    "network, secrets, and out-of-whitelist reads remain strictly blocked)."
                ),
            ),
        ] = None,
        project: ProjectEvent | None = None,
    ) -> dict[str, object]:
        """Consult Antigravity as an independent first-principles Chief Commander or manage a persistent project phase.

        Default Q&A uses a global-first, contrarian stance that surfaces blind spots and local-optimum traps
        across any project. Optional `readonly_files` lets the advisor directly inspect whitelisted code or
        evaluation JSONs inside a fail-closed read-only hook sandbox (no writes, commands, network, or
        out-of-whitelist reads). Project actions are init, report, status, list, memory, review, activate,
        directive (records human steering/budget updates and enforces a mandatory short red-team pre-mortem
        on every direction change), suspend (cleanly freezes superseded/stalled phases), and feedback
        (records post-hoc quality labels in-band). Uses native JSON schema enforcement, graded L2 pinned
        causal memory, and closed-loop cycle outcome attribution.
        """
        advisor = ctx.request_context.lifespan_context
        return await execute_advise(
            store=store,
            advisor=advisor,
            question=question,
            context=context,
            session_id=session_id,
            timeout_seconds=timeout_seconds,
            effort=effort,
            model=model,
            stance=stance,
            readonly_files=readonly_files,
            project=project,
        )

    return mcp


if __name__ == "__main__":
    server(sys.argv[1]).run()
