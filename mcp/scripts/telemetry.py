"""Lightweight local invocation logging, outcome tracking, and audit reporting for agy_advise."""

import json
import math
import os
import re
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from project import build_advisor_track_record, build_efficiency_scorecard, normalize_state

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]


FEEDBACK_LABELS = (
    "helpful",
    "good_dissent",
    "rubber_stamp",
    "missed_root_cause",
    "false_alarm",
    "bad_format",
)

_DISABLE_VALUES = {"off", "0", "false", "no", "none"}
_ENABLE_VALUES = {"1", "true", "yes", "on"}
_WS_RE = re.compile(r"\s+")
_PRE_MODEL_ERROR_MARKERS = (
    "authenticated session",
    "waiting for the previous advisor turn",
    "closed its input before receiving",
    "unknown advisor session",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_data_dir(db_path: Path | None = None) -> Path:
    if db_path is not None:
        return db_path.parent
    if configured := os.getenv("AGY_ADVISE_DB"):
        return Path(configured).expanduser().parent
    default = Path.home() / ".local" / "share"
    return Path(os.getenv("XDG_DATA_HOME", default)) / "codex-agy-advise"


def resolve_log_path(db_path: Path | None = None) -> Path | None:
    configured = os.getenv("AGY_ADVISE_LOG")
    if configured is not None:
        cleaned = configured.strip()
        if cleaned.lower() in _DISABLE_VALUES:
            return None
        if cleaned:
            return Path(cleaned).expanduser()
    return default_data_dir(db_path) / "invocations.jsonl"


def resolve_feedback_path(db_path: Path | None = None) -> Path:
    if configured := os.getenv("AGY_ADVISE_FEEDBACK_LOG"):
        cleaned = configured.strip()
        if cleaned:
            return Path(cleaned).expanduser()
    log_path = resolve_log_path(db_path)
    if log_path is not None:
        return log_path.parent / "feedback.jsonl"
    return default_data_dir(db_path) / "feedback.jsonl"


def log_full_enabled() -> bool:
    return os.getenv("AGY_ADVISE_LOG_FULL", "").strip().lower() in _ENABLE_VALUES


def preview_text(text: str | None, limit: int) -> str | None:
    if not text:
        return None
    compact = _WS_RE.sub(" ", text).strip()
    if not compact:
        return None
    if len(compact) <= limit:
        return compact
    return compact[: max(limit - 1, 1)].rstrip() + "…"


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    line = (json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            view = memoryview(line)
            written = 0
            while written < len(view):
                written += os.write(fd, view[written:])
        finally:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def build_invocation_record(
    *,
    invocation_id: str,
    mode: str,
    action: str,
    status: str,
    elapsed_seconds: float,
    question: str = "",
    context: str | None = None,
    prompt_chars: int | None = None,
    stance: str | None = None,
    is_followup: bool = False,
    session_id: str | None = None,
    effort: str | None = None,
    model_requested: str | None = None,
    project_id: str | None = None,
    phase: str | None = None,
    response: dict[str, Any] | None = None,
    error: str | None = None,
    include_full: bool | None = None,
) -> dict[str, Any]:
    resp = response or {}
    proj = resp.get("project") if isinstance(resp.get("project"), dict) else {}
    last_decision = proj.get("last_decision") if isinstance(proj.get("last_decision"), dict) else {}
    advice = resp.get("advice") if isinstance(resp.get("advice"), str) else None
    usage = resp.get("usage") if isinstance(resp.get("usage"), dict) else None
    review_status = resp.get("review_status") if isinstance(resp.get("review_status"), str) else None

    effective_model_req = resp.get("model_requested") if resp.get("model_requested") is not None else model_requested
    effective_effort = resp.get("requested_effort") if resp.get("requested_effort") is not None else effort
    effective_session = resp.get("session_id") if isinstance(resp.get("session_id"), str) else session_id
    effective_prompt_chars = prompt_chars if prompt_chars is not None else resp.get("prompt_chars")

    error_text = error or (resp.get("error") if isinstance(resp.get("error"), str) else None)
    error_lower = (error_text or "").lower()
    pre_model_error = any(marker in error_lower for marker in _PRE_MODEL_ERROR_MARKERS)

    llm_attempted = bool(
        (mode == "qa" and status != "validation_error")
        or (
            mode == "project"
            and review_status in ("decided", "stale", "transport_error", "invalid_decision")
        )
    )
    llm_responded = bool(
        advice is not None or usage is not None or resp.get("duration_seconds") is not None
    )
    llm_called = bool(llm_attempted and (llm_responded or not pre_model_error))

    decision_value = None
    decision_max_trials = None
    hypothesis_family = None
    directive_alignment = resp.get("directive_alignment") if isinstance(resp.get("directive_alignment"), str) else None
    dissent_raised = bool(resp.get("dissent_raised"))
    if review_status == "decided" and isinstance(last_decision.get("decision"), str):
        decision_value = last_decision["decision"]
        if isinstance(last_decision.get("max_trials"), int):
            decision_max_trials = last_decision["max_trials"]
        if isinstance(last_decision.get("hypothesis_family"), str):
            hypothesis_family = last_decision["hypothesis_family"]
        if directive_alignment is None and isinstance(last_decision.get("alignment"), str):
            directive_alignment = last_decision["alignment"]
        if directive_alignment == "dissent":
            dissent_raised = True

    effective_reasons = (
        last_decision["triggered_by"]
        if review_status == "decided" and isinstance(last_decision.get("triggered_by"), list)
        else (proj.get("review_reasons") if isinstance(proj.get("review_reasons"), list) else [])
    )

    ro_staged = resp.get("readonly_files_staged") if isinstance(resp.get("readonly_files_staged"), list) else []
    ro_read = resp.get("readonly_files_read") if isinstance(resp.get("readonly_files_read"), list) else []
    ro_denied = resp.get("readonly_denied_calls") if isinstance(resp.get("readonly_denied_calls"), list) else []
    ro_bytes = resp.get("readonly_files_bytes") if isinstance(resp.get("readonly_files_bytes"), int) else 0
    ro_mode = resp.get("readonly_grounding_mode") if isinstance(resp.get("readonly_grounding_mode"), str) else (
        "staged_hook" if ro_staged else None
    )
    is_dir_change = resp.get("is_direction_change")
    if is_dir_change is None and isinstance(last_decision.get("is_direction_change"), bool):
        is_dir_change = last_decision["is_direction_change"]
    caution_soft_dissent = bool(
        resp.get("caution_with_dissent_points")
        or (review_status == "decided" and last_decision.get("caution_with_dissent_points"))
    )
    auto_escalated = bool(
        resp.get("auto_escalated_dissent")
        or (review_status == "decided" and last_decision.get("auto_escalated_dissent"))
    )
    raw_resp_chars = resp.get("raw_response_chars") if isinstance(resp.get("raw_response_chars"), int) else None

    record: dict[str, Any] = {
        "ts": utc_now(),
        "invocation_id": invocation_id,
        "mode": mode,
        "action": action,
        "status": status,
        "llm_attempted": llm_attempted,
        "llm_called": llm_called,
        "llm_responded": llm_responded,
        "elapsed_seconds": round(max(elapsed_seconds, 0.0), 3),
        "queue_seconds": resp.get("queue_seconds"),
        "duration_seconds": resp.get("duration_seconds"),
        "model_requested": effective_model_req,
        "model_reported": resp.get("model_reported"),
        "model_configured": resp.get("model_configured"),
        "requested_effort": effective_effort,
        "usage": usage,
        "stance": resp.get("requested_stance", stance) if mode == "qa" else None,
        "is_followup": bool(is_followup) if mode == "qa" else False,
        "session_id": effective_session if mode == "qa" else None,
        "project_id": proj.get("id", project_id),
        "phase": proj.get("phase", phase),
        "domain": proj.get("domain"),
        "gate_mode": proj.get("gate_mode"),
        "route": proj.get("route"),
        "cycle": proj.get("cycle"),
        "review_status": review_status,
        "review_reasons": effective_reasons,
        "decision": decision_value,
        "directive_alignment": directive_alignment,
        "is_direction_change": bool(is_dir_change) if action == "directive" else None,
        "caution_with_dissent_points": caution_soft_dissent if action == "directive" else False,
        "auto_escalated_dissent": auto_escalated if action == "directive" else False,
        "dissent_raised": dissent_raised,
        "decision_max_trials": decision_max_trials,
        "hypothesis_family": hypothesis_family,
        "dispatch_allowed": proj.get("dispatch_allowed"),
        "readonly_grounding_mode": ro_mode,
        "readonly_files_count": len(ro_staged),
        "readonly_files_bytes": ro_bytes,
        "readonly_files_staged": ro_staged,
        "readonly_files_read": ro_read,
        "readonly_denied_calls": len(ro_denied),
        "question_chars": len(question or ""),
        "context_chars": len(context or ""),
        "prompt_chars": effective_prompt_chars if isinstance(effective_prompt_chars, int) else None,
        "advice_chars": len(advice) if advice is not None else 0,
        "raw_response_chars": raw_resp_chars,
        "question_preview": preview_text(question, 240),
        "advice_preview": preview_text(advice, 320),
        "error": preview_text(error_text, 400),
    }

    full = log_full_enabled() if include_full is None else include_full
    if full:
        record["question"] = question
        record["context"] = context
        record["advice"] = advice
    return record


def record_invocation(db_path: Path | None, **kwargs: Any) -> Path | None:
    log_path = resolve_log_path(db_path)
    if log_path is None:
        return None
    record = build_invocation_record(**kwargs)
    try:
        append_jsonl(log_path, record)
    except OSError:
        return None
    return log_path


def record_feedback(
    *,
    invocation_id: str,
    label: str,
    note: str = "",
    project_id: str | None = None,
    phase: str | None = None,
    db_path: Path | None = None,
    feedback_path: Path | None = None,
) -> Path:
    if label not in FEEDBACK_LABELS:
        raise ValueError(f"Unsupported feedback label {label!r}; choose one of {', '.join(FEEDBACK_LABELS)}.")
    if not invocation_id.strip():
        raise ValueError("invocation_id must not be empty.")
    target = feedback_path or resolve_feedback_path(db_path)
    entry = {
        "ts": utc_now(),
        "invocation_id": invocation_id.strip(),
        "label": label,
        "note": note.strip(),
        "project_id": project_id,
        "phase": phase,
    }
    append_jsonl(target, entry)
    return target


def read_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil((pct / 100.0) * len(ordered)) - 1))
    return round(ordered[index], 3)


def _parse_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _read_project_efficiency(db_path: Path | None, project_id: str | None = None) -> list[dict[str, Any]]:
    if db_path is None or not db_path.exists():
        return []
    uri = f"file:{db_path.resolve()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as db:
            if project_id is not None:
                rows = db.execute(
                    "SELECT record FROM projects WHERE id = ? ORDER BY id, phase",
                    (project_id,),
                ).fetchall()
            else:
                rows = db.execute("SELECT record FROM projects ORDER BY id, phase").fetchall()
    except sqlite3.Error:
        return []

    items: list[dict[str, Any]] = []
    for (record,) in rows:
        try:
            state = normalize_state(json.loads(record))
        except (TypeError, ValueError):
            continue
        scorecard = build_efficiency_scorecard(state)
        track_record = build_advisor_track_record(state)
        items.append(
            {
                "id": state.get("id"),
                "phase": state.get("phase"),
                "phase_status": state.get("phase_status"),
                "score_name": state.get("score_name"),
                "baseline_score": state.get("baseline_score"),
                "best_score": state.get("best_score"),
                "pass_score": state.get("pass_score"),
                "track_record": track_record,
                **scorecard,
            }
        )
    return items


def summarize_logs(
    log_path: Path | None,
    feedback_path: Path | None = None,
    *,
    db_path: Path | None = None,
    project_id: str | None = None,
    days: int | None = None,
    since: str | None = None,
    after_line: int | None = None,
    recent: int = 8,
) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days is not None else None
    since_dt = _parse_ts(since) if since else None
    if since_dt is not None and (cutoff is None or since_dt > cutoff):
        cutoff = since_dt

    invocations = read_jsonl(log_path)
    feedback_rows = read_jsonl(feedback_path)

    filtered: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    start_idx = max((after_line or 1) - 1, 0)
    for row in invocations[start_idx:]:
        if project_id is not None and row.get("project_id") != project_id:
            continue
        if cutoff is not None:
            ts = _parse_ts(row.get("ts"))
            if ts is not None and ts < cutoff:
                continue
        filtered.append(row)
        inv_id = row.get("invocation_id")
        if isinstance(inv_id, str):
            by_id[inv_id] = row

    filtered_feedback: list[dict[str, Any]] = []
    for fb in feedback_rows:
        inv_id = fb.get("invocation_id")
        linked = by_id.get(inv_id) if isinstance(inv_id, str) else None
        effective_project = fb.get("project_id") or (linked.get("project_id") if linked else None)
        if project_id is not None and effective_project != project_id:
            continue
        if cutoff is not None:
            ts = _parse_ts(fb.get("ts"))
            if ts is not None and ts < cutoff:
                continue
        filtered_feedback.append({**fb, "linked": linked})

    mode_counts = Counter(row.get("mode", "unknown") for row in filtered)
    action_counts = Counter(f"{row.get('mode', '?')}:{row.get('action', '?')}" for row in filtered)
    status_counts = Counter(row.get("status", "unknown") for row in filtered)
    stance_counts = Counter(row["stance"] for row in filtered if row.get("mode") == "qa" and row.get("stance"))
    project_counts = Counter(row["project_id"] for row in filtered if row.get("project_id"))
    review_status_counts = Counter(
        row["review_status"] for row in filtered if row.get("mode") == "project" and row.get("review_status")
    )
    trigger_counts: Counter[str] = Counter()
    for row in filtered:
        if row.get("mode") != "project":
            continue
        reasons = row.get("review_reasons") or []
        if row.get("action") == "report":
            for reason in reasons:
                if reason != "explicit_review":
                    trigger_counts[reason] += 1
        elif row.get("action") == "review" and "explicit_review" in reasons:
            trigger_counts["explicit_review"] += 1
    decision_counts = Counter(row["decision"] for row in filtered if row.get("decision"))
    directive_alignments = Counter(
        row["directive_alignment"]
        for row in filtered
        if row.get("mode") == "project"
        and row.get("action") == "directive"
        and row.get("directive_alignment")
    )

    def _row_responded(row: dict[str, Any]) -> bool:
        if "llm_responded" in row:
            return bool(row["llm_responded"])
        return bool(
            row.get("llm_called")
            and (
                row.get("duration_seconds") is not None
                or row.get("usage") is not None
                or (row.get("advice_chars") or 0) > 0
            )
        )

    def _row_pre_model_error(row: dict[str, Any]) -> bool:
        err = (row.get("error") or "").lower()
        return bool(err and any(marker in err for marker in _PRE_MODEL_ERROR_MARKERS))

    llm_attempted_rows = [row for row in filtered if row.get("llm_attempted", row.get("llm_called"))]
    llm_responded_rows = [row for row in filtered if _row_responded(row)]
    llm_rows = [
        row
        for row in filtered
        if row.get("llm_called") and (_row_responded(row) or not _row_pre_model_error(row))
    ]
    # Consistent model counts over the exact same llm_rows population.
    model_counts = Counter(
        row.get("model_reported")
        or row.get("model_requested")
        or (f"default:{row['model_configured']}" if row.get("model_configured") else "default")
        for row in llm_rows
    )
    llm_elapsed = [float(row["elapsed_seconds"]) for row in llm_rows if isinstance(row.get("elapsed_seconds"), (int, float))]
    llm_queue = [float(row["queue_seconds"]) for row in llm_rows if isinstance(row.get("queue_seconds"), (int, float))]
    prompt_char_values = [
        int(row["prompt_chars"]) for row in llm_rows if isinstance(row.get("prompt_chars"), int) and row["prompt_chars"] > 0
    ]
    total_tokens = 0
    cache_hits = 0
    for row in llm_rows:
        usage = row.get("usage")
        if isinstance(usage, dict):
            if isinstance(usage.get("total_tokens"), (int, float)):
                total_tokens += int(usage["total_tokens"])
            if isinstance(usage.get("cache_read_tokens"), (int, float)) and usage["cache_read_tokens"] > 0:
                cache_hits += 1

    avg_elapsed = round(sum(llm_elapsed) / len(llm_elapsed), 2) if llm_elapsed else None
    avg_tokens = round(total_tokens / len(llm_responded_rows)) if llm_responded_rows else None
    avg_prompt_chars = round(sum(prompt_char_values) / len(prompt_char_values)) if prompt_char_values else None
    cache_hit_rate = round(cache_hits / len(llm_responded_rows), 3) if llm_responded_rows else None

    qa_rows = [row for row in filtered if row.get("mode") == "qa"]
    substantive_qa_rows = [row for row in qa_rows if (row.get("question_chars") or 0) > 20]
    tactical_qa_count = sum(1 for row in substantive_qa_rows if row.get("stance") == "tactical")
    tactical_qa_ratio = (
        round(tactical_qa_count / len(substantive_qa_rows), 3) if substantive_qa_rows else None
    )
    followup_count = sum(1 for row in qa_rows if row.get("is_followup"))
    timeout_count = sum(1 for row in llm_rows if row.get("status") == "timeout" or row.get("review_status") == "transport_error")

    report_count = action_counts["project:report"]
    decided_review_only_count = sum(
        1
        for row in filtered
        if row.get("mode") == "project"
        and row.get("action") in ("review", "report")
        and row.get("review_status") == "decided"
    )
    decided_directive_count = sum(
        1
        for row in filtered
        if row.get("mode") == "project"
        and row.get("action") == "directive"
        and row.get("review_status") == "decided"
    )
    decided_total_count = review_status_counts["decided"]
    review_to_report_ratio = round(decided_review_only_count / report_count, 3) if report_count else None
    directive_to_report_ratio = round(decided_directive_count / report_count, 3) if report_count else None
    decided_to_report_ratio = round(decided_total_count / report_count, 3) if report_count else None

    directive_total = sum(directive_alignments.values())
    directive_dissent_rate = (
        round(directive_alignments["dissent"] / directive_total, 3) if directive_total else None
    )
    direction_change_checks = sum(
        1
        for row in filtered
        if row.get("mode") == "project"
        and row.get("action") == "directive"
        and row.get("is_direction_change") is True
    )
    caution_with_dissent_count = sum(
        1
        for row in filtered
        if row.get("mode") == "project"
        and row.get("action") == "directive"
        and row.get("caution_with_dissent_points") is True
    )
    auto_escalated_dissent_count = sum(
        1
        for row in filtered
        if row.get("mode") == "project"
        and row.get("action") == "directive"
        and row.get("auto_escalated_dissent") is True
    )

    ro_grounded_rows = [row for row in llm_rows if (row.get("readonly_files_count") or 0) > 0]
    ro_inline_calls = sum(1 for row in ro_grounded_rows if row.get("readonly_grounding_mode") == "inline")
    ro_staged_calls = sum(
        1 for row in ro_grounded_rows if row.get("readonly_grounding_mode") in ("staged_hook", None)
    )
    ro_total_staged = sum(int(row.get("readonly_files_count") or 0) for row in ro_grounded_rows)
    ro_total_read = sum(len(row.get("readonly_files_read") or []) for row in ro_grounded_rows)
    ro_total_denied = sum(int(row.get("readonly_denied_calls") or 0) for row in filtered)

    effective_db = db_path
    if effective_db is None and log_path is not None:
        candidate = log_path.parent / "projects.sqlite3"
        if candidate.exists():
            effective_db = candidate
    project_efficiency = _read_project_efficiency(effective_db, project_id=project_id)

    feedback_counts = Counter(fb.get("label", "unknown") for fb in filtered_feedback)
    feedback_by_stance: dict[str, dict[str, int]] = {}
    for fb in filtered_feedback:
        linked = fb.get("linked")
        key = (linked.get("stance") or linked.get("mode") or "unlinked") if linked else "unlinked"
        bucket = feedback_by_stance.setdefault(key, {})
        label = fb.get("label", "unknown")
        bucket[label] = bucket.get(label, 0) + 1

    hints: list[str] = []
    if llm_rows and timeout_count / len(llm_rows) >= 0.15:
        hints.append(
            f"High timeout/transport error rate ({timeout_count}/{len(llm_rows)}): "
            "consider shorter focused questions, lower effort, or auto_review=false batching."
        )
    review_attempts = sum(
        review_status_counts[k] for k in ("decided", "stale", "transport_error", "invalid_decision")
    )
    if review_attempts and review_status_counts["invalid_decision"] / review_attempts >= 0.1:
        hints.append(
            f"Elevated invalid_decision rate ({review_status_counts['invalid_decision']}/{review_attempts}): "
            "inspect advice_preview and verify --json-schema enforcement."
        )
    if review_attempts and review_status_counts["stale"] / review_attempts >= 0.15:
        hints.append(
            f"Frequent stale reviews ({review_status_counts['stale']}/{review_attempts}): "
            "use auto_review=false when parallel trials finish together, then run a single review."
        )
    if len(substantive_qa_rows) >= 5 and (tactical_qa_ratio or 0.0) >= 0.5:
        hints.append(
            f"High tactical Q&A share ({tactical_qa_count}/{len(substantive_qa_rows)} = {tactical_qa_ratio:.0%}): "
            "avoid delegating local arithmetic/SOP details to agy_advise; reserve calls for global/red_team decisions."
        )
    if report_count >= 5 and (review_to_report_ratio or 0.0) >= 0.8:
        hints.append(
            f"Near 1:1 review-to-report ratio ({decided_review_only_count}/{report_count} = {review_to_report_ratio:.2f}): "
            "stop micromanaging with max_trials=1 or hard-stopping soft regressions; use directive / soft severity / 2-5 trial runways."
        )
    if decided_directive_count >= 3 and (report_count == 0 or (directive_to_report_ratio or 0.0) >= 1.0):
        hints.append(
            f"Directive churn detected ({decided_directive_count} directives vs {report_count} reports): "
            "avoid chaining rapid direction switches without running trials/gate checks; suspend & fork if the north-star gate shifted."
        )
    if (avg_prompt_chars or 0) > 18000 or (avg_elapsed or 0.0) > 75.0:
        hints.append(
            f"Heavy review latency/prompt overhead (avg_elapsed={avg_elapsed}s, avg_prompt_chars={avg_prompt_chars}): "
            "keep L2 pinned facts crisp and avoid dumping long procedural SOPs into questions."
        )
    if directive_total >= 5 and directive_alignments["caution"] == 0 and directive_alignments["dissent"] == 0:
        hints.append(
            f"Potential rubber-stamp risk: {directive_total}/{directive_total} directive checks returned 'concur' with zero caution/dissent."
        )
    if caution_with_dissent_count >= 3 and directive_alignments["dissent"] == 0:
        hints.append(
            f"Caution-creep detected: {caution_with_dissent_count}/{directive_total} directive checks raised dissent_points "
            "but softened alignment to 'caution' instead of pausing with 'dissent'."
        )
    if len(ro_grounded_rows) >= 3 and ro_total_read == 0:
        hints.append(
            f"Read-only files were staged in {len(ro_grounded_rows)} calls ({ro_total_staged} files) but advisor read 0 files: "
            "verify prompt grounding or check if attached files were relevant."
        )
    if ro_total_denied > 0:
        hints.append(
            f"Read-only whitelist guard blocked {ro_total_denied} out-of-whitelist/disallowed tool attempt(s); inspect logs to confirm expected behavior."
        )
    for item in project_efficiency:
        alerts = item.get("macro_alerts") or []
        if alerts:
            hints.append(
                f"Project {item['id']}/{item['phase']} alerts [{', '.join(alerts)}]: "
                f"gate_trials={item['gate_trials']}/{item['total_trials']}, "
                f"since_improvement={item['trials_since_last_improvement']}t/"
                f"{item['pivots_since_last_improvement']}p, "
                f"since_last_gate={item['trials_since_last_gate']}t, "
                f"consecutive_holds={item.get('consecutive_holds', 0)}, "
                f"max_trials=1={item['one_trial_plans']}/{item['planned_cycles']}."
            )
    if feedback_counts["rubber_stamp"] > 0:
        hints.append(
            f"{feedback_counts['rubber_stamp']} rubber_stamp feedback item(s): "
            "audit question framing or default to stance='red_team' for high-stakes reviews."
        )
    if feedback_counts["false_alarm"] > 0:
        hints.append(
            f"{feedback_counts['false_alarm']} false_alarm feedback item(s): "
            "review stagnation_limit / same_mechanism_limit / proxy_limit / regression_severity."
        )

    recent_entries = [
        {
            "ts": row.get("ts"),
            "invocation_id": row.get("invocation_id"),
            "mode": row.get("mode"),
            "action": row.get("action"),
            "status": row.get("status"),
            "project_id": row.get("project_id"),
            "phase": row.get("phase"),
            "stance": row.get("stance"),
            "review_status": row.get("review_status"),
            "decision": row.get("decision"),
            "directive_alignment": row.get("directive_alignment"),
            "elapsed_seconds": row.get("elapsed_seconds"),
            "prompt_chars": row.get("prompt_chars"),
            "question_preview": row.get("question_preview"),
            "error": row.get("error"),
        }
        for row in filtered[-recent:]
    ]

    return {
        "log_path": str(log_path) if log_path else None,
        "feedback_path": str(feedback_path) if feedback_path else None,
        "db_path": str(effective_db) if effective_db else None,
        "filters": {"project_id": project_id, "days": days, "since": since, "after_line": after_line},
        "total_invocations": len(filtered),
        "llm_invocations": len(llm_rows),
        "llm_attempted_invocations": len(llm_attempted_rows),
        "llm_responded_invocations": len(llm_responded_rows),
        "mode_counts": dict(mode_counts),
        "action_counts": dict(action_counts),
        "status_counts": dict(status_counts),
        "timeout_or_transport_errors": timeout_count,
        "timeout_rate": round(timeout_count / len(llm_rows), 3) if llm_rows else 0.0,
        "latency_seconds": {
            "avg": avg_elapsed,
            "p50": percentile(llm_elapsed, 50),
            "p95": percentile(llm_elapsed, 95),
            "max": round(max(llm_elapsed), 3) if llm_elapsed else None,
            "queue_p95": percentile(llm_queue, 95),
        },
        "total_tokens": total_tokens,
        "avg_tokens_per_call": avg_tokens,
        "avg_prompt_chars": avg_prompt_chars,
        "cache_hit_rate": cache_hit_rate,
        "qa": {
            "total": len(qa_rows),
            "followups": followup_count,
            "stance_counts": dict(stance_counts),
            "substantive_tactical_ratio": tactical_qa_ratio,
        },
        "directives": {
            "total_checked": directive_total,
            "direction_changes": direction_change_checks,
            "alignment_counts": dict(directive_alignments),
            "caution_with_dissent_points": caution_with_dissent_count,
            "auto_escalated_dissent": auto_escalated_dissent_count,
            "dissent_rate": directive_dissent_rate,
        },
        "readonly_grounding": {
            "grounded_calls": len(ro_grounded_rows),
            "inline_calls": ro_inline_calls,
            "staged_hook_calls": ro_staged_calls,
            "files_staged": ro_total_staged,
            "files_read": ro_total_read,
            "denied_tool_attempts": ro_total_denied,
        },
        "governance_efficiency": {
            "review_to_report_ratio": review_to_report_ratio,
            "directive_to_report_ratio": directive_to_report_ratio,
            "decided_to_report_ratio": decided_to_report_ratio,
            "substantive_tactical_qa_ratio": tactical_qa_ratio,
            "directive_dissent_rate": directive_dissent_rate,
        },
        "models": dict(model_counts),
        "projects": dict(project_counts),
        "project_efficiency": project_efficiency,
        "review_status_counts": dict(review_status_counts),
        "trigger_counts": dict(trigger_counts),
        "decision_counts": dict(decision_counts),
        "feedback": {
            "total": len(filtered_feedback),
            "label_counts": dict(feedback_counts),
            "by_context": feedback_by_stance,
        },
        "tuning_hints": hints,
        "recent": recent_entries,
    }


def format_summary(summary: dict[str, Any]) -> str:
    lines = [
        "# agy_advise Invocation & Feedback Report",
        "",
        f"- **Log**: `{summary['log_path']}`",
        f"- **Total Invocations**: {summary['total_invocations']} "
        f"(LLM attempted: {summary.get('llm_attempted_invocations', summary['llm_invocations'])}, "
        f"called: {summary['llm_invocations']}, responded: {summary.get('llm_responded_invocations', 0)})",
        f"- **Modes**: {summary['mode_counts'] or '{}'}",
        f"- **Statuses**: {summary['status_counts'] or '{}'} (timeout/transport rate: {summary['timeout_rate']:.1%})",
    ]
    lat = summary["latency_seconds"]
    if lat["p50"] is not None:
        cache_pct = f"{summary['cache_hit_rate']:.0%}" if summary.get("cache_hit_rate") is not None else "n/a"
        prompt_part = f" | avg_prompt={summary['avg_prompt_chars']}ch" if summary.get("avg_prompt_chars") else ""
        lines.append(
            f"- **LLM Latency**: avg={lat['avg']}s, P50={lat['p50']}s, P95={lat['p95']}s, max={lat['max']}s "
            f"(queue P95={lat['queue_p95']}s) | **Tokens**: {summary['total_tokens']:,} "
            f"(avg={summary.get('avg_tokens_per_call')}, cache_hit={cache_pct}{prompt_part})"
        )
    if summary["models"]:
        lines.append(f"- **Models**: {summary['models']}")
    if summary["qa"]["total"]:
        tac_part = (
            f", substantive_tactical={summary['qa']['substantive_tactical_ratio']:.0%}"
            if summary["qa"].get("substantive_tactical_ratio") is not None
            else ""
        )
        lines.append(
            f"- **Q&A**: total={summary['qa']['total']}, followups={summary['qa']['followups']}, "
            f"stances={summary['qa']['stance_counts']}{tac_part}"
        )
    if summary.get("directives", {}).get("total_checked"):
        d = summary["directives"]
        soft_part = (
            f", soft_dissent_caution={d['caution_with_dissent_points']}"
            if d.get("caution_with_dissent_points")
            else ""
        )
        esc_part = (
            f", auto_escalated={d['auto_escalated_dissent']}"
            if d.get("auto_escalated_dissent")
            else ""
        )
        lines.append(
            f"- **Directive Pre-Mortems**: total={d['total_checked']} (direction_changes={d.get('direction_changes', 0)}), "
            f"alignments={d['alignment_counts']}, dissent_rate={d['dissent_rate']:.0%}{soft_part}{esc_part}"
        )
    if summary.get("readonly_grounding", {}).get("grounded_calls"):
        rg = summary["readonly_grounding"]
        lines.append(
            f"- **Read-Only Grounding**: grounded_calls={rg['grounded_calls']} "
            f"(inline={rg.get('inline_calls', 0)}, staged_hook={rg.get('staged_hook_calls', 0)}), "
            f"files_staged={rg['files_staged']}, files_read={rg['files_read']}, "
            f"denied_attempts={rg['denied_tool_attempts']}"
        )
    if summary["projects"]:
        lines.append(f"- **Projects**: {summary['projects']}")
    if summary["review_status_counts"]:
        ge = summary.get("governance_efficiency", {})
        ratio = ge.get("review_to_report_ratio")
        dir_ratio = ge.get("directive_to_report_ratio")
        parts = []
        if ratio is not None:
            parts.append(f"review/report={ratio:.2f}")
        if dir_ratio is not None and dir_ratio > 0:
            parts.append(f"directive/report={dir_ratio:.2f}")
        ratio_part = (" | " + ", ".join(parts)) if parts else ""
        lines.append(f"- **Project Review Statuses**: {summary['review_status_counts']}{ratio_part}")
    if summary["trigger_counts"]:
        lines.append(f"- **Review Triggers**: {summary['trigger_counts']}")
    if summary["decision_counts"]:
        lines.append(f"- **Decisions**: {summary['decision_counts']}")
    if summary.get("project_efficiency"):
        lines.extend(["", "## Project Efficiency, Anti-Spinning & Outcome Attribution"])
        for item in summary["project_efficiency"]:
            score_part = (
                f"score={item['best_score']} (base={item['baseline_score']}, pass={item['pass_score']})"
                if item.get("score_name")
                else "qualitative"
            )
            gate_pct = f"{item['gate_eval_ratio']:.0%}" if item.get("gate_eval_ratio") is not None else "n/a"
            one_pct = f"{item['one_trial_plan_ratio']:.0%}" if item.get("one_trial_plan_ratio") is not None else "n/a"
            alert_part = f" | **ALERTS**: {', '.join(item['macro_alerts'])}" if item.get("macro_alerts") else ""
            tr = item.get("track_record") or {}
            tr_part = ""
            if tr.get("evaluated_cycles") or tr.get("directive_checks"):
                tr_part = (
                    f" | outcomes={tr['verdict_counts']}, "
                    f"dissent={tr['dissent_count']}(vindicated={tr['dissent_vindicated']}/refuted={tr['dissent_refuted']})"
                )
            lines.append(
                f"- `{item['id']}/{item['phase']}` ({item['phase_status']}, {score_part}): "
                f"gate={item['gate_trials']}/{item['total_trials']} ({gate_pct}), "
                f"since_gain={item['trials_since_last_improvement']}t/{item['cycles_since_last_improvement']}c/"
                f"{item['pivots_since_last_improvement']}p, since_gate={item['trials_since_last_gate']}t, "
                f"holds_streak={item.get('consecutive_holds', 0)}, max_trials=1={one_pct}{tr_part}{alert_part}"
            )
    if summary["feedback"]["total"]:
        lines.append(
            f"- **Feedback ({summary['feedback']['total']})**: {summary['feedback']['label_counts']} "
            f"| by context: {summary['feedback']['by_context']}"
        )
    if summary["tuning_hints"]:
        lines.extend(["", "## Tuning Hints"])
        for hint in summary["tuning_hints"]:
            lines.append(f"- {hint}")
    if summary["recent"]:
        lines.extend(["", "## Recent Calls"])
        for item in summary["recent"]:
            target = f"{item['project_id']}/{item['phase']}" if item.get("project_id") else (item.get("stance") or "qa")
            extra = item.get("decision") or item.get("review_status") or item.get("error") or ""
            if item.get("directive_alignment"):
                extra = f"{extra}:{item['directive_alignment']}"
            extra_part = f" [{extra}]" if extra else ""
            lines.append(
                f"- `{item['invocation_id'][:10]}` {item['ts']} {item['mode']}:{item['action']} "
                f"({target}) {item['status']} {item['elapsed_seconds']}s{extra_part}"
            )
    return "\n".join(lines)
