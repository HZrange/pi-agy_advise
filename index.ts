// Antigravity Advisor — bridge to the local `agy` CLI.
// One long-lived `agy` stream-json session, exposed to Pi as the agy_advise tool.
// `agy_advise` tool. The agy process keeps its own conversation and prompt
// cache; Pi never resends full history — only the question plus optional
// minimal context. Requires `agy` on PATH and the allow rules in
// ~/.gemini/antigravity-cli/settings.json. Permission bypass requires explicit
// user authorization.

import { Type } from "typebox";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const IDLE_MS = 10 * 60 * 1000;
const KILL_GRACE_MS = 3_000;

type Advice = { response: string; conversationId?: string; seconds?: number; usage?: unknown };
type AgyEvent = {
  event?: string;
  conversation_id?: unknown;
  result?: {
    conversation_id?: unknown;
    status?: string;
    response?: string;
    duration_seconds?: number;
    usage?: unknown;
    error?: string;
  };
};

export default function (pi: ExtensionAPI) {
  let child: ChildProcessWithoutNullStreams | null = null;
  let startedCwd: string | undefined;
  let startedSkipPermissions: boolean | undefined;
  let conversationId: string | undefined;
  let pending: { resolve: (v: Advice) => void; reject: (e: Error) => void } | null = null;
  let idle: ReturnType<typeof setTimeout> | null = null;
  let chain: Promise<unknown> = Promise.resolve();
  let stderrTail = "";
  let shuttingDown = false;

  function clearIdle() {
    if (idle) {
      clearTimeout(idle);
      idle = null;
    }
  }

  function scheduleIdle() {
    idle = setTimeout(() => teardown(), IDLE_MS);
    idle.unref();
  }

  function teardown(resetConversation = true, error = new Error("agy planner closed")) {
    clearIdle();
    const dying = child;
    child = null;
    startedCwd = undefined;
    startedSkipPermissions = undefined;
    stderrTail = "";
    if (resetConversation) conversationId = undefined;

    const request = pending;
    pending = null;
    request?.reject(error);

    if (!dying?.pid || dying.exitCode !== null || dying.signalCode !== null) return;
    const signalChild = (signal: NodeJS.Signals) => {
      try {
        dying.kill(signal);
      } catch {
        // The process may have exited before the signal was sent.
      }
    };
    const forceKill = setTimeout(() => {
      if (dying.exitCode === null && dying.signalCode === null) signalChild("SIGKILL");
    }, KILL_GRACE_MS);
    forceKill.unref();
    dying.once("exit", () => clearTimeout(forceKill));
    signalChild("SIGTERM");
  }

  function start(cwd: string, skipPermissions: boolean) {
    // NOTE: no --mode flag. Plan mode makes agy write an implementation_plan.md
    // and then wait for a review that headless mode cannot answer, so the turn
    // ends on "please approve" and no advice is ever returned. Leaving the mode
    // unset lets the CLI pick its documented headless default (auto since
    // v1.5.7, falling back to default mode). Passing --mode auto explicitly is
    // wrong here: on accounts where auto is unavailable the CLI prints
    // "--mode auto is not available for this account; continuing in the default
    // mode" on every start.
    const args = ["--input-format", "stream-json", "--output-format", "stream-json"];
    if (skipPermissions) args.push("--dangerously-skip-permissions");
    if (conversationId) args.push("--conversation", conversationId);

    const c = spawn("agy", args, { cwd, stdio: ["pipe", "pipe", "pipe"] });
    child = c;
    startedCwd = cwd;
    startedSkipPermissions = skipPermissions;
    let buf = "";
    const fail = (error: Error) => {
      if (child === c) teardown(true, error);
    };

    c.stdout.setEncoding("utf8");
    c.stderr.setEncoding("utf8");
    c.stderr.on("data", (chunk: string) => {
      if (child !== c) return;
      // Keep the tail for diagnostics; an unread stderr pipe would block the child.
      stderrTail = (stderrTail + chunk).slice(-4000);
    });

    c.stdout.on("data", (chunk: string) => {
      if (child !== c) return;
      buf += chunk;
      let nl: number;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        let ev: AgyEvent;
        try {
          ev = JSON.parse(line);
        } catch {
          continue;
        }
        if (!ev || typeof ev !== "object") continue;
        if (ev.event === "init" && typeof ev.conversation_id === "string") conversationId = ev.conversation_id;
        if (ev.event !== "result" || !pending) continue;

        const result = ev.result;
        const diagnostics = stderrTail ? `\nagy stderr:\n${stderrTail}` : "";
        if (result?.status !== "SUCCESS") {
          fail(new Error(`agy ${result?.status ?? "error"}: ${result?.error ?? "unknown"}${diagnostics}`));
          return;
        }
        const response = typeof result.response === "string" ? result.response.trim() : "";
        if (!response) {
          fail(new Error(
            "agy returned an empty response. A tool needed approval that headless mode cannot prompt for. " +
              "If agy's toolPermission is still request-review, set it to always-proceed and keep dangerous commands in a deny list. " +
              "Do not answer this by forbidding agy from using tools. " +
              "Keep the default permissions unless the user explicitly authorizes a bypass." +
              diagnostics,
          ));
          return;
        }
        const id = result.conversation_id ?? ev.conversation_id;
        if (typeof id === "string") conversationId = id;
        const request = pending;
        pending = null;
        scheduleIdle();
        request.resolve({ response, conversationId, seconds: result.duration_seconds, usage: result.usage });
      }
    });

    c.stdin.on("error", fail);
    c.stdout.on("error", fail);
    c.stderr.on("error", fail);
    c.on("close", (code, signal) => {
      if (child !== c) return;
      const diagnostics = stderrTail ? `\nagy stderr:\n${stderrTail}` : "";
      fail(new Error(`agy planner process exited (code=${code}, signal=${signal})${diagnostics}`));
    });
    c.on("error", fail);
  }

  async function ask(
    prompt: string,
    timeoutMs: number,
    cwd: string,
    skipPermissions: boolean,
    signal?: AbortSignal,
  ): Promise<Advice> {
    if (shuttingDown) throw new Error("agy planner closed");
    if (signal?.aborted) throw new Error("cancelled");
    clearIdle();

    // The child can exit before its stdio streams finish closing.
    if (child && (child.exitCode !== null || child.signalCode !== null)) teardown();
    if (!child || startedCwd !== cwd || startedSkipPermissions !== skipPermissions) {
      const keepConversation = child !== null && startedCwd === cwd;
      teardown(!keepConversation);
      try {
        start(cwd, skipPermissions);
      } catch (error) {
        teardown();
        throw error;
      }
    }
    const c = child!;
    stderrTail = "";

    return new Promise<Advice>((resolve, reject) => {
      const cleanup = () => {
        clearTimeout(timer);
        signal?.removeEventListener("abort", onAbort);
      };
      const timer = setTimeout(() => {
        teardown(true, new Error(`agy planner timed out after ${timeoutMs}ms`));
      }, timeoutMs);
      const onAbort = () => {
        teardown(true, new Error("cancelled"));
      };
      signal?.addEventListener("abort", onAbort, { once: true });

      const request = {
        resolve: (advice: Advice) => {
          cleanup();
          resolve(advice);
        },
        reject: (error: Error) => {
          cleanup();
          reject(error);
        },
      };
      pending = request;
      const payload = JSON.stringify({ event: "user", message: { content: prompt } }) + "\n";
      try {
        c.stdin.write(payload, (error) => {
          if (error && child === c && pending === request) teardown(true, error);
        });
      } catch (error) {
        teardown(true, error instanceof Error ? error : new Error(String(error)));
      }
    });
  }

  pi.on("session_shutdown", async () => {
    shuttingDown = true;
    teardown();
  });

  pi.registerTool({
    name: "agy_advise",
    label: "Antigravity Advisor",
    description:
      "Ask the Antigravity CLI for a plan, design opinion, or independent review. It reads files and runs commands to ground the answer; agy's toolPermission preset decides what it may run without asking. Returns advice text.",
    promptSnippet: "Ask the Antigravity planner for a plan or independent second opinion",
    promptGuidelines: [
      "Use agy_advise at decision points: choosing an architecture, being stuck for more than one attempt, before a destructive change, or when an independent second review is wanted. Do not call it inside tight tool loops where it only adds latency.",
      "Never pre-collapse the problem for agy_advise. Do not hand it candidate options (A/B/C, \"option 1 or 2\") and do not give it your own conclusion to approve. agy is the strongest model available here: give it the goal, the constraints, and the observed failure, then let it choose the solution space.",
      "Keep agy_advise input factual. Pass the facts agy cannot see (paths, error text, constraints) and one line per hypothesis already ruled out. Do not paste Pi's own reasoning chain, and do not ask agy to re-read files Pi already read.",
      "Let agy use read-only tools. It grounds the answer by reading the workspace and running commands; a blocked command means agy's toolPermission preset still asks for approval, which headless mode cannot answer. Raise the preset or deny the dangerous actions instead of forbidding tools.",
      "Never put your own operating limits into the question (\"do not read files\", \"do not run commands\", \"stay static\", \"do not spawn a worker\"). Those constrain Pi, not agy. agy reads them as a narrower task and reports progress instead of answering. Tighten scope through permissions, not through instructions.",
      "Set skipPermissions to true only when the user explicitly authorizes the permission bypass for this request. If a permission problem occurs, explain the blocked operation when the diagnostics identify it.",
    ],
    parameters: Type.Object({
      question: Type.String({ description: "The goal, observed failure if relevant, and open question. Make the first turn self-contained." }),
      context: Type.Optional(Type.String({ description: "Up to 10 lines of relevant facts, paths, constraints or ruled-out hypotheses." })),
      timeoutMs: Type.Optional(Type.Number({ description: "Default 240000" })),
      skipPermissions: Type.Optional(
        Type.Boolean({
          description:
            "Pass --dangerously-skip-permissions, bypassing tool approvals for actions such as shell commands and workspace writes. Requires explicit user authorization.",
        }),
      ),
    }),
    async execute(_id, p, signal, _onUpdate, ctx) {
      const asked = p.context ? `${p.question}\n\nKnown context:\n${p.context}` : p.question;
      const prompt = `${asked}\n\nAnswer from first principles. If the question already offers candidate options or a proposed approach, do not simply pick one: derive what you think is right from the goal, the constraints, and the observed failures, and say why.`;

      const run = () => ask(prompt, p.timeoutMs ?? 240_000, ctx.cwd, p.skipPermissions === true, signal);
      const result = chain.then(run, run);
      chain = result.then(
        () => undefined,
        () => undefined,
      );

      const v = await result;
      return {
        content: [{ type: "text", text: v.response }],
        details: { conversationId: v.conversationId, seconds: v.seconds, usage: v.usage },
      };
    },
  });
}
