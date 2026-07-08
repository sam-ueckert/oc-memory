import { execFile } from "node:child_process";
import { estimateTokens, parseTopScore, recordRecallTelemetry } from "./telemetry.ts";

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

// ── recall bounding: env parsing + clamping (issue #6) ─────────────────────
//
// Four knobs, all env-configurable, all clamped to a safe range so a typo
// or an extreme value can't blow up prompt size, flood the CLI with an
// unbounded result set, or silently filter out everything:
//
//   OC_MEMORY_RECALL_TOP_K              default 5,    clamped 1-20
//   OC_MEMORY_RECALL_MIN_SCORE          default 0.0,  clamped 0.0-1.0
//   OC_MEMORY_RECALL_EXCERPT_MAX_CHARS  default 800,  clamped 100-4000
//   OC_MEMORY_RECALL_DEDUPE_WINDOW      default 3,    clamped 0-10
//
// OC_MEMORY_RECALL_MIN_SCORE defaults to 0.0 (no score filtering) rather
// than some non-zero value: similarity/score distributions vary by
// embedding model and corpus, so a "safe-looking" default like 0.3 could
// silently drop everything for one deployment and nothing for another.
// Enable OC_MEMORY_RECALL_TELEMETRY=1 (see telemetry.ts) to observe real
// score distributions before choosing a non-zero threshold.

const DEFAULT_TOP_K = 5;
const MIN_TOP_K = 1;
const MAX_TOP_K = 20;

export function getTopK(env: NodeJS.ProcessEnv = process.env): number {
  const raw = Number(env.OC_MEMORY_RECALL_TOP_K);
  if (!Number.isFinite(raw) || raw <= 0) return DEFAULT_TOP_K;
  return Math.min(Math.max(Math.trunc(raw), MIN_TOP_K), MAX_TOP_K);
}

const DEFAULT_MIN_SCORE = 0.0;
const MIN_MIN_SCORE = 0.0;
const MAX_MIN_SCORE = 1.0;

export function getMinScore(env: NodeJS.ProcessEnv = process.env): number {
  const raw = Number(env.OC_MEMORY_RECALL_MIN_SCORE);
  if (!Number.isFinite(raw)) return DEFAULT_MIN_SCORE;
  return Math.min(Math.max(raw, MIN_MIN_SCORE), MAX_MIN_SCORE);
}

const DEFAULT_EXCERPT_MAX_CHARS = 800;
const MIN_EXCERPT_MAX_CHARS = 100;
const MAX_EXCERPT_MAX_CHARS = 4000;

// Legacy env var name from PR #5 (OC_MEMORY_RECALL_EXCERPT_MAX, default
// 1500, clamped only at a 10 000-char ceiling). Still honored as a fallback
// when the new name is unset, so existing deployments don't break silently
// — but the effective value now goes through the tighter 100-4000 clamp
// and the new 800-char default, matching this issue's bounding scope. This
// is a documented behavior change (see HOOK.md / PLUGIN.md), not a silent
// one.
const LEGACY_EXCERPT_ENV = "OC_MEMORY_RECALL_EXCERPT_MAX";
const EXCERPT_ENV = "OC_MEMORY_RECALL_EXCERPT_MAX_CHARS";

/**
 * Returns the max number of characters for the injected recall excerpt.
 * Configurable via OC_MEMORY_RECALL_EXCERPT_MAX_CHARS (preferred) or the
 * legacy OC_MEMORY_RECALL_EXCERPT_MAX name, clamped to 100-4000.
 */
export function getExcerptMaxChars(env: NodeJS.ProcessEnv = process.env): number {
  const raw = Number(env[EXCERPT_ENV] ?? env[LEGACY_EXCERPT_ENV]);
  if (!Number.isFinite(raw) || raw <= 0) return DEFAULT_EXCERPT_MAX_CHARS;
  return Math.min(Math.max(raw, MIN_EXCERPT_MAX_CHARS), MAX_EXCERPT_MAX_CHARS);
}

/** Back-compat alias for the PR #5 export name. */
export const getExcerptMax = getExcerptMaxChars;

const DEFAULT_DEDUPE_WINDOW = 3;
const MIN_DEDUPE_WINDOW = 0;
const MAX_DEDUPE_WINDOW = 10;

/**
 * Number of past recall attempts (within this process) whose injected
 * result identifiers are remembered for de-duplication. 0 disables dedupe
 * entirely.
 */
export function getDedupeWindow(env: NodeJS.ProcessEnv = process.env): number {
  const raw = Number(env.OC_MEMORY_RECALL_DEDUPE_WINDOW);
  if (!Number.isFinite(raw) || raw < 0) return DEFAULT_DEDUPE_WINDOW;
  return Math.min(Math.max(Math.trunc(raw), MIN_DEDUPE_WINDOW), MAX_DEDUPE_WINDOW);
}

/** Builds the extra CLI flags passed through to `oc-memory search`, derived
 * from the (already clamped) bounding knobs above. min-score is only passed
 * when > 0 so a default/unset threshold behaves exactly as before (no
 * filtering, no extra flag). excerptMaxChars is always passed so the CLI's
 * direct-output truncation matches the hook/plugin injection budget instead
 * of falling back to the CLI's shorter human-display default. */
export function buildSearchArgs(opts: {
  topK: number;
  minScore: number;
  excerptMaxChars: number;
}): string[] {
  const args = ["--limit", String(opts.topK), "--excerpt-max", String(opts.excerptMaxChars)];
  if (opts.minScore > 0) {
    args.push("--min-score", String(opts.minScore));
  }
  return args;
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
    .trim()
    .substring(0, maxLen);
}

/**
 * Quick check: is this text likely auto-generated noise (heartbeat polls,
 * very short queries, empty text) rather than a real recall query?
 * Uses a regex for the heartbeat prefix match rather than literal `.includes`
 * calls, so it catches variants without re-maintenance.
 */
export function looksLikeNoise(text: string): boolean {
  if (!text) return true;
  if (text.split(/\s+/).length < 2) return true;
  if (/^\s*(?:HEARTBEAT|Read HEARTBEAT\.md)\b/mi.test(text)) return true;
  return false;
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
  /** Extra argv elements appended after the query (e.g. `--limit`,
   * `--min-score`), built by buildSearchArgs(). Defaults to none, so
   * existing direct callers of searchMemory are unaffected. */
  extraArgs?: string[];
}

/**
 * Run `<cli> search <query> [...extraArgs]` as an argument-array child
 * process (never a hand-built shell string), with a hard timeout. Resolves
 * with stdout, or rejects on timeout/spawn/non-zero-exit — callers must
 * fail open (no recall) on rejection. Async: does not block the event loop
 * while the child process runs.
 */
export function searchMemory(query: string, opts: SearchOptions): Promise<string> {
  const execFileFn = opts.execFileFn ?? execFile;
  const args = ["search", query, ...(opts.extraArgs ?? [])];
  return new Promise((resolve, reject) => {
    execFileFn(
      opts.cli,
      args,
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

// ── in-process dedupe (issue #6) ────────────────────────────────────────
//
// Dedupe is volatile session state, deliberately not persisted to the DB
// for v1: it only needs to stop the *same* recall result from being
// re-injected into consecutive turns within one running hook/plugin
// process, not to track dedupe across restarts.

/** Extracts the leading `[<id>]` token from a formatted CLI result line
 * (e.g. `[42] [fact] scene:x sal:0.80 — ...`), used as a stable identity
 * for dedupe. Falls back to `null` when the line doesn't match (e.g. raw,
 * non-CLI-formatted text in tests) — callers should use the full line text
 * as the identity in that case. */
export function extractResultId(line: string): string | null {
  const m = /^\[([^\]]+)\]/.exec(line.trim());
  return m ? m[1] : null;
}

/** Splits raw CLI stdout into individual, trimmed, non-empty result lines,
 * dropping the "(FTS fallback)" marker line so it never participates in
 * dedupe or gets counted as a candidate. */
export function splitResultLines(raw: string): string[] {
  return raw
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l.length > 0 && l !== "(FTS fallback)");
}

export interface DedupeResult {
  keptLines: string[];
  dedupedCount: number;
}

/**
 * FIFO window over the identifiers injected on the last N recall attempts.
 * Candidates matching an id already in the window are dropped; surviving
 * ids from this attempt are recorded, and the oldest attempt is evicted
 * once the window exceeds maxWindows. windowSize is passed per-call (not
 * fixed at construction) so it can track live env changes.
 */
export class RecallDedupeState {
  private windows: string[][] = [];

  filterAndRecord(candidateLines: string[], maxWindows: number): DedupeResult {
    if (maxWindows <= 0) {
      this.windows = [];
      return { keptLines: candidateLines, dedupedCount: 0 };
    }

    const seen = new Set<string>();
    for (const batch of this.windows) {
      for (const id of batch) seen.add(id);
    }

    const keptLines: string[] = [];
    const keptIds: string[] = [];
    let dedupedCount = 0;
    for (const line of candidateLines) {
      const id = extractResultId(line) ?? line;
      if (seen.has(id)) {
        dedupedCount++;
        continue;
      }
      keptLines.push(line);
      keptIds.push(id);
    }

    if (keptIds.length > 0) {
      this.windows.push(keptIds);
    }
    while (this.windows.length > maxWindows) this.windows.shift();

    return { keptLines, dedupedCount };
  }

  /** Test/ops escape hatch: clears all remembered dedupe state. */
  reset(): void {
    this.windows = [];
  }
}

export interface HandlerDeps {
  execFileFn?: ExecFileFn;
  platform?: NodeJS.Platform;
  /** Test-only: working directory for the spawned child process. */
  cwd?: string;
}

export function createHandler(deps: HandlerDeps = {}) {
  // One dedupe window per handler instance — i.e. per "session" for the
  // life of this hook process. A fresh createHandler() call (e.g. a new
  // test, or a process restart) starts with a clean window.
  const dedupe = new RecallDedupeState();

  return async (event: any) => {
    // Kill switch first — before touching event content or spawning anything.
    if (isRecallDisabled()) return;

    if (event.type !== "message" || event.action !== "received") return;

    const content = event.context?.content;
    if (!content || content.length < 10) return;

    // Skip heartbeat polls
    if (looksLikeNoise(content)) return;

    const query = sanitizeQuery(content);
    if (!query || query.split(/\s+/).length < 2) return;

    const cli = getMemCli();
    const useShell = needsWindowsShell(cli, deps.platform) && shellAllowed();
    const topK = getTopK();
    const minScore = getMinScore();
    const excerptMaxChars = getExcerptMaxChars();

    const start = Date.now();
    let result: string;
    try {
      result = (
        await searchMemory(query, {
          cli,
          timeoutMs: getTimeoutMs(),
          useShell,
          execFileFn: deps.execFileFn,
          cwd: deps.cwd,
          extraArgs: buildSearchArgs({ topK, minScore, excerptMaxChars }),
        })
      ).trim();
    } catch {
      // Timeout, missing CLI, non-zero exit, wrapper error, etc. — fail
      // open. No recall, no thrown error, message handling continues.
      await recordRecallTelemetry({
        queryChars: query.length,
        queryTokensEstimate: estimateTokens(query),
        topK,
        minScore,
        candidateCount: 0,
        dedupedCount: 0,
        injectedCount: 0,
        injectedChars: 0,
        elapsedMs: Date.now() - start,
        outcome: "error",
      });
      return;
    }
    const elapsedMs = Date.now() - start;

    if (!result || result.includes("No results") || result.length < 20) {
      await recordRecallTelemetry({
        queryChars: query.length,
        queryTokensEstimate: estimateTokens(query),
        topK,
        minScore,
        candidateCount: 0,
        dedupedCount: 0,
        injectedCount: 0,
        injectedChars: 0,
        elapsedMs,
        outcome: "empty",
      });
      return;
    }

    const candidateLines = splitResultLines(result);
    const { keptLines, dedupedCount } = dedupe.filterAndRecord(candidateLines, getDedupeWindow());
    const topScore = parseTopScore(candidateLines);

    // Everything was deduped (or the CLI returned nothing usable after
    // splitting) — inject nothing rather than an empty/near-empty header.
    if (keptLines.length === 0) {
      await recordRecallTelemetry({
        queryChars: query.length,
        queryTokensEstimate: estimateTokens(query),
        topK,
        minScore,
        candidateCount: candidateLines.length,
        dedupedCount,
        injectedCount: 0,
        injectedChars: 0,
        topScore,
        elapsedMs,
        outcome: "empty",
      });
      return;
    }

    const joined = keptLines.join("\n");
    const truncated = joined.length > excerptMaxChars
      ? joined.substring(0, excerptMaxChars) + "\n... (truncated)"
      : joined;

    event.messages.push(`[oc-memory Recall]\n${truncated}`);

    await recordRecallTelemetry({
      queryChars: query.length,
      queryTokensEstimate: estimateTokens(query),
      topK,
      minScore,
      candidateCount: candidateLines.length,
      dedupedCount,
      injectedCount: keptLines.length,
      injectedChars: truncated.length,
      topScore,
      elapsedMs,
      outcome: "injected",
    });
  };
}

const handler = createHandler();
export default handler;
