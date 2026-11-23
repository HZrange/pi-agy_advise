// Antigravity Advisor — bridge to the local `agy` CLI.
// One long-lived `agy` stream-json session in plan mode, exposed to Pi as the
// `agy_advise` tool. The agy process keeps its own conversation and prompt
// cache; Pi never resends full history — only the question plus optional
// minimal context. Requires `agy` on PATH and the allow rules in
// ~/.gemini/antigravity-cli/settings.json (or skipPermissions=true).

import { Type } from "typebox";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const IDLE_MS = 10 * 60 * 1000;

type Advice = { response: string; seconds?: number; usage?: unknown; stderr: string };

export default function (pi: ExtensionAPI) {
  let child: ChildProcessWithoutNullStreams | null = null;
  let startedCwd: string | undefined;
  let buf = "";
  let conversationId: string | undefined;
  let pending: { resolve: (v: Advice) => void; reject: (e: Error) => void } | null = null;
  let idle: ReturnType<typeof setTimeout> | null = null;
  let chain: Promise<unknown> = Promise.resolve();
  let stderrTail = "";

  function teardown() {
    if (idle) {
      clearTimeout(idle);
      idle = null;
    }
    if (child) {
      try {
        child.kill("SIGTERM");
      } catch {
        /* already gone */
      }
      child = null;
    }
    startedCwd = undefined;
    buf = "";
    if (pending) {
      const p = pending;
      pending = null;
      p.reject(new Error("agy planner closed"));
    }
  }

  function start(cwd: string, skipPermissions: boolean) {
    // NOTE: --mode plan works by expanding the `/plan` slash command, so it must
    // NOT be combined with --disable-slash-commands (the CLI then warns
    // "--mode plan has no effect while slash command expansion is disabled").
    const args = ["--input-format", "stream-json", "--output-format", "stream-json", "--mode", "plan"];
    if (skipPermissions) args.push("--dangerously-skip-permissions");
    if (conversationId) args.push("--conversation", conversationId);

    const c = spawn("agy", args, { cwd, stdio: ["pipe", "pipe", "pipe"] }) as ChildProcessWithoutNullStreams;
    child = c;
    startedCwd = cwd;
    stderrTail = "";
    c.stdout.setEncoding("utf8");
    c.stderr.setEncoding("utf8");
    c.stderr.on("data", (chunk: string) => {
      // Keep the tail for diagnostics; an unread stderr pipe would block the child.
      stderrTail = (stderrTail + chunk).slice(-4000);
    });

    c.stdout.on("data", (chunk: string) => {
      buf += chunk;
      let nl: number;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        let ev: any;
        try {
          ev = JSON.parse(line);
        } catch {
          continue;
        }
        if (ev.event === "init" && typeof ev.conversation_id === "string") conversationId = ev.conversation_id;
        if (ev.event === "result" && pending) {
          const p = pending;
          pending = null;
          const r = ev.result ?? {};
          if (r.status === "SUCCESS")
            p.resolve({ response: String(r.response ?? ""), seconds: r.duration_seconds, usage: r.usage, stderr: stderrTail });
          else p.reject(new Error(`agy ${r.status}: ${r.error ?? "unknown"}${stderrTail ? `\n${stderrTail}` : ""}`));
        }
      }
    });

    const fail = (e: Error) => {
      child = null;
      startedCwd = undefined;
      if (pending) {
        const p = pending;
        pending = null;
        p.reject(e);
      }
    };
    c.on("close", () => fail(new Error("agy planner process exited")));
    c.on("error", fail);
  }

  function ask(prompt: string, timeoutMs: number, cwd: string, skipPermissions: boolean, signal?: AbortSignal): Promise<Advice> {
    if (signal?.aborted) return Promise.reject(new Error("cancelled"));
    if (!child || startedCwd !== cwd) {
      teardown();
      start(cwd, skipPermissions);
    }
    const c = child!;
    return new Promise<Advice>((resolve, reject) => {
      const settle = () => {
        clearTimeout(timer);
        signal?.removeEventListener("abort", onAbort);
      };
      const timer = setTimeout(() => {
        pending = null;
        teardown();
        reject(new Error(`agy planner timed out after ${timeoutMs}ms`));
      }, timeoutMs);
      const onAbort = () => {
        pending = null;
        settle();
        teardown();
        reject(new Error("cancelled"));
      };
      signal?.addEventListener("abort", onAbort, { once: true });
      pending = {
        resolve: (v) => {
          settle();
          resolve(v);
        },
        reject: (e) => {
          settle();
          reject(e);
        },
      };
      c.stdin.write(JSON.stringify({ event: "user", message: { content: prompt } }) + "\n");
    });
  }

  pi.on("session_shutdown", async () => {
    teardown();
  });

  pi.registerTool({
    name: "agy_advise",
    label: "Antigravity Advisor",
    description:
      "Ask the Antigravity CLI agent (plan mode, read-only) for a plan, design opinion, or second review. It is the strongest model available here: hand it open questions. Returns advice text only; it never edits files.",
    promptSnippet: "Ask the Antigravity planner for a plan or second opinion (read-only)",
    promptGuidelines: [
      "Use agy_advise at decision points: choosing an architecture, being stuck, before a destructive change, or when an independent second review is wanted. Do not call it inside tight tool loops where it only adds latency.",
      "Never pre-collapse the problem for agy_advise. Do not hand it candidate options (A/B/C, \"option 1 or 2\") or your own conclusion to approve. agy is the strongest model here: give it the goal, the constraints, and the observed failure, then let it choose the solution space.",
      "Keep agy_advise input factual. Pass the facts agy cannot see (paths, error text, constraints) and one line per hypothesis already ruled out. Do not paste Pi's own reasoning chain or ask agy to re-read files Pi already read.",
    ],
    parameters: Type.Object({
      question: Type.String({ description: "The decision or question, self-contained" }),
      context: Type.Optional(Type.String({ description: "Minimal facts/files agy needs; omit to keep cost low" })),
      timeoutMs: Type.Optional(Type.Number({ description: "Default 240000" })),
      skipPermissions: Type.Optional(
        Type.Boolean({
          description:
            "Pass --dangerously-skip-permissions. Needed when agy wants a shell command that headless cannot prompt for; without it the turn is cancelled and returns an empty answer.",
        }),
      ),
    }),
    async execute(_id, p, signal, _onUpdate, ctx) {
      // Send the question as a plain request. Read-only behaviour comes from
      // `--mode plan`, not from instructions inside the prompt. The trailing
      // sentence counteracts the common failure mode of handing agy a
      // pre-collapsed choice: it is told to derive the answer itself.
      const asked = p.context ? `${p.question}\n\nKnown context:\n${p.context}` : p.question;
      const prompt = `${asked}\n\nAnswer from first principles. If the question already offers candidate options or a proposed approach, do not simply pick one: derive what you think is right from the goal, the constraints, and the observed failures, and say why.`;

      const run = () => ask(prompt, p.timeoutMs ?? 240_000, ctx.cwd, p.skipPermissions === true, signal);
      const result = chain.then(run, run);
      chain = result.then(
        () => undefined,
        () => undefined,
      );

      const v = await result;
      if (!v.response.trim()) {
        throw new Error(
          `agy returned an empty response (process exit 0, but the turn was cancelled). ` +
            `This usually means a tool needed approval that headless mode cannot prompt for.\n` +
            `Fix 1: add an allow rule to permissions.allow in ~/.gemini/antigravity-cli/settings.json ` +
            `(for example "command(find)").\n` +
            `Fix 2: call this tool with skipPermissions: true.\n` +
            (v.stderr ? `agy stderr:\n${v.stderr}` : ""),
        );
      }
      if (idle) clearTimeout(idle);
      idle = setTimeout(teardown, IDLE_MS);
      return {
        content: [{ type: "text", text: v.response.trim() }],
        details: { conversationId, seconds: v.seconds, usage: v.usage },
      };
    },
  });
}
