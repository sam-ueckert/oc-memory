import { execFile } from "node:child_process";

// Path to your oc-memory CLI wrapper. Adjust if installed differently.
const DEFAULT_MEM_CLI = "oc-memory";

// Hard timeout budget for the recall subprocess (see issue #137). Recall must
// never delay message delivery beyond this window — on timeout (or any other
// error) the hook fails open: no injected memories, message handling
// continues normally.
const DEFAULT_TIMEOUT_MS = 2500;
const MIN_TIMEOUT_MS = 500;
const MAX_TIMEOUT_MS = 3000;

type ExecFileFn = typeof execFile;

function truthyFlag(raw: string | undefined): boolean {
  const v = (raw || "").trim().toLowerCase();
  return v === "1" || v === "true" || v === "yes";
}

/**
 * Kill switch: set OC_MEMORY_RECALL_DISABLED=1 (or "true"/"yes") to turn the
 * recall hook off entirely without uninstalling it. Checked before any other
 * work (including reading event content).
 */
export function isRecallDisabled(env: NodeJS.ProcessEnv = process.env): boolean {
  return truthyFlag(env.OC_MEMORY_RECALL_DISABLED);
}

export function getMemCli(env: NodeJS.ProcessEnv = process.env): string {
  return env.OC_MEMORY_CLI || DEFAULT_MEM_CLI;
}

export function getTimeoutMs(env: NodeJS.ProcessEnv = process.env): number {
  const raw = Number(env.OC_MEMORY_RECALL_TIMEOUT_MS);
  if (!Number.isFinite(raw) || raw <= 0) return DEFAULT_TIMEOUT_MS;
  return Math.min(Math.max(raw, MIN_TIMEOUT_MS), MAX_TIMEOUT_MS);
}

/**
 * Windows can't CreateProcess a .cmd/.bat file directly — it needs cmd.exe as
 * an interpreter. Node (>=18.20.2 / 20.12.2, post CVE-2024-27980) applies
 * argument-array escaping when a shell is used, but this is still a shell
 * invocation, so it is opt-in only: set OC_MEMORY_CLI_ALLOW_SHELL=1 to allow
 * it for a .cmd/.bat OC_MEMORY_CLI wrapper. Non-Windows and non-.cmd/.bat
 * paths never use a shell.
 */
export function needsWindowsShell(
  cliPath: string,
  platform: NodeJS.Platform = process.platform
): boolean {
  return platform === "win32" && /\.(cmd|bat)$/i.test(cliPath);
}

export function shellAllowed(env: NodeJS.ProcessEnv = process.env): boolean {
  return truthyFlag(env.OC_MEMORY_CLI_ALLOW_SHELL);
}

// Matches ASCII control characters (0x00-0x1F, 0x7F). Built from char codes
// rather than a literal regex to keep the source file free of raw control
// bytes.
const CONTROL_CHARS_RE = new RegExp(
  "[" + String.fromCharCode(0) + "-" + String.fromCharCode(31) + String.fromCharCode(127) + "]",
  "g"
);

/**
 * Strip control characters only. Quotes and shell metacharacters are safe to
 * keep — the query is passed as a single argv element via execFile, never
 * interpolated into a shell string.
 */
export function sanitizeQuery(content: string, maxLen = 200): string {
  return content
    .replace(CONTROL_CHARS_RE, " ")
    .substring(0, maxLen)
    .trim();
}

export interface SearchOptions {
  cli: string;
  timeoutMs: number;
  useShell: boolean;
  execFileFn?: ExecFileFn;
  /** Working directory for the child process. Defaults to the current
   * process's cwd when omitted (normal production behavior). Exposed mainly
   * so tests can point at fixture scripts without touching global state. */
  cwd?: string;
}

/**
 * Run `<cli> search <query>` as an argument-array child process (never a
 * hand-built shell string), with a hard timeout. Resolves with stdout, or
 * rejects on timeout/spawn/non-zero-exit — callers must fail open (no
 * recall) on rejection. Async: does not block the event loop while the
 * child process runs.
 */
export function searchMemory(query: string, opts: SearchOptions): Promise<string> {
  const execFileFn = opts.execFileFn ?? execFile;
  return new Promise((resolve, reject) => {
    execFileFn(
      opts.cli,
      ["search", query],
      {
        timeout: opts.timeoutMs,
        killSignal: "SIGKILL",
        encoding: "utf-8",
        windowsHide: true,
        shell: opts.useShell,
        cwd: opts.cwd,
      } as any,
      (err: unknown, stdout: unknown) => {
        if (err) {
          reject(err);
          return;
        }
        resolve(String(stdout));
      }
    );
  });
}

export interface HandlerDeps {
  execFileFn?: ExecFileFn;
  platform?: NodeJS.Platform;
  /** Test-only: working directory for the spawned child process. */
  cwd?: string;
}

export function createHandler(deps: HandlerDeps = {}) {
  return async (event: any) => {
    // Kill switch first — before touching event content or spawning anything.
    if (isRecallDisabled()) return;

    if (event.type !== "message" || event.action !== "received") return;

    const content = event.context?.content;
    if (!content || content.length < 10) return;

    // Skip heartbeat polls
    if (content.includes("HEARTBEAT") || content.includes("Read HEARTBEAT.md")) return;

    const query = sanitizeQuery(content);
    if (!query || query.split(/\s+/).length < 2) return;

    const cli = getMemCli();
    const useShell = needsWindowsShell(cli, deps.platform) && shellAllowed();

    let result: string;
    try {
      result = (
        await searchMemory(query, {
          cli,
          timeoutMs: getTimeoutMs(),
          useShell,
          execFileFn: deps.execFileFn,
          cwd: deps.cwd,
        })
      ).trim();
    } catch {
      // Timeout, missing CLI, non-zero exit, wrapper error, etc. — fail
      // open. No recall, no thrown error, message handling continues.
      return;
    }

    if (!result || result.includes("No results") || result.length < 20) return;

    const truncated = result.length > 1500
      ? result.substring(0, 1500) + "\n... (truncated)"
      : result;

    event.messages.push(`[oc-memory Recall]\n${truncated}`);
  };
}

const handler = createHandler();
export default handler;
