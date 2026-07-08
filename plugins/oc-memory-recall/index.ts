import type { HandlerDeps as HookHandlerDeps, SearchOptions } from "../../hooks/oc-memory-recall/handler.ts";
import {
  isRecallDisabled,
  getMemCli,
  getTimeoutMs,
  needsWindowsShell,
  shellAllowed,
  sanitizeQuery,
  searchMemory,
  looksLikeNoise,
  getExcerptMaxChars,
  getTopK,
  getMinScore,
  getDedupeWindow,
  buildSearchArgs,
  splitResultLines,
  RecallDedupeState,
} from "../../hooks/oc-memory-recall/handler.ts";
import { estimateTokens, parseTopScore, recordRecallTelemetry } from "../../hooks/oc-memory-recall/telemetry.ts";

/**
 * oc-memory recall as an OpenClaw *plugin*, registered on the
 * `before_prompt_build` hook (see OpenClaw's `docs/plugins/hooks.md`).
 *
 * This is the same recall behavior as `hooks/oc-memory-recall` (same CLI,
 * same kill switch, same hardened argv/timeout/fail-open posture — see that
 * module for the security-relevant implementation, reused here rather than
 * duplicated), but wired through the supported plugin seam instead of the
 * internal/shell `message:received` hook.
 *
 * Why: `message:received` is fire-and-forget from OpenClaw's perspective —
 * it cannot reliably inject context into the *same* model turn that
 * triggered it. `before_prompt_build` runs synchronously in the prompt-build
 * pipeline and its return value (`prependContext`) is folded into the
 * prompt for that turn, so recall actually reaches the model that needs it.
 *
 * This module does not patch OpenClaw core, does not make `message:received`
 * awaited, and does not add any capture/write behavior — recall only.
 */

// ── OpenClaw plugin surface (minimal typed boundary) ────────────────────────
//
// oc-memory does not vendor the OpenClaw plugin SDK. These are the minimal
// shapes this plugin depends on, matching the documented `before_prompt_build`
// hook contract: the handler receives an event and (optional) context, and
// may return a result object whose `prependContext` string is folded into
// the prompt for that turn. Returning `undefined` means "no change."

export interface RecentMessageLike {
  role?: string;
  content?: string;
}

/** Shape of the `before_prompt_build` event this plugin reads from. */
export interface BeforePromptBuildEvent {
  /** The prompt/user turn currently being built. */
  prompt?: string;
  /** Bounded recent conversation history, most-recent-last. */
  messages?: Array<RecentMessageLike | string>;
}

export type BeforePromptBuildContext = unknown;

export interface BeforePromptBuildResult {
  /** Text prepended to the prompt/system context for this turn. */
  prependContext: string;
}

export type BeforePromptBuildHandler = (
  event: BeforePromptBuildEvent,
  ctx?: BeforePromptBuildContext
) => Promise<BeforePromptBuildResult | undefined> | BeforePromptBuildResult | undefined;

export interface OpenClawPluginApi {
  on(eventName: "before_prompt_build", handler: BeforePromptBuildHandler): void;
}

export interface OpenClawPlugin {
  id: string;
  register(api: OpenClawPluginApi): void;
}

// ── query construction ───────────────────────────────────────────────────

const MAX_QUERY_LEN = 200;
const MAX_RECENT_MESSAGES = 3;
const MAX_MESSAGE_EXCERPT_LEN = 120;

function extractText(entry: RecentMessageLike | string | undefined): string {
  if (!entry) return "";
  if (typeof entry === "string") return entry;
  return entry.content || "";
}

/**
 * Builds a bounded search query from the in-flight prompt plus a small
 * window of recent messages. The prompt text drives the query; recent
 * message excerpts are appended (each individually bounded) to add context
 * without letting the query grow unbounded or dominate the prompt itself.
 */
export function buildQuery(event: BeforePromptBuildEvent, maxLen = MAX_QUERY_LEN): string {
  const promptText = sanitizeQuery(event.prompt || "", maxLen);

  const recent = Array.isArray(event.messages) ? event.messages.slice(-MAX_RECENT_MESSAGES) : [];
  const recentText = recent
    .map((m) => extractText(m).trim())
    .filter(Boolean)
    .map((t) => t.substring(0, MAX_MESSAGE_EXCERPT_LEN))
    .join(" ");

  const combined = [promptText, sanitizeQuery(recentText, maxLen)]
    .filter(Boolean)
    .join(" ")
    .trim();

  return sanitizeQuery(combined, maxLen);
}

// ── handler ──────────────────────────────────────────────────────────────

export interface HandlerDeps extends HookHandlerDeps {}

/**
 * Creates the `before_prompt_build` handler. Kill switch is checked first,
 * before touching event content or spawning anything (requirement: disabled
 * state must never invoke the CLI). Recall/subprocess errors, timeouts, and
 * a missing CLI all fail open — the handler returns `undefined` and the
 * agent proceeds without recall context.
 */
export function createRecallHandler(deps: HandlerDeps = {}): BeforePromptBuildHandler {
  // One dedupe window per handler instance — see handler.ts for rationale
  // (volatile, in-process session state, not persisted to the DB for v1).
  const dedupe = new RecallDedupeState();

  return async (event) => {
    if (isRecallDisabled()) return undefined;

    const query = buildQuery(event);
    if (looksLikeNoise(query)) return undefined;

    const cli = getMemCli();
    const useShell = needsWindowsShell(cli, deps.platform) && shellAllowed();
    const topK = getTopK();
    const minScore = getMinScore();

    const start = Date.now();
    let result: string;
    try {
      const opts: SearchOptions = {
        cli,
        timeoutMs: getTimeoutMs(),
        useShell,
        execFileFn: deps.execFileFn,
        cwd: deps.cwd,
        extraArgs: buildSearchArgs({ topK, minScore }),
      };
      result = (await searchMemory(query, opts)).trim();
    } catch {
      // Timeout, missing CLI, non-zero exit, wrapper error, etc. Fail open:
      // no thrown error, no injected context, the turn proceeds normally.
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
      return undefined;
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
      return undefined;
    }

    const candidateLines = splitResultLines(result);
    const { keptLines, dedupedCount } = dedupe.filterAndRecord(candidateLines, getDedupeWindow());
    const topScore = parseTopScore(candidateLines);

    // Everything was deduped — return no context rather than an empty header.
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
      return undefined;
    }

    const excerptMaxChars = getExcerptMaxChars();
    const joined = keptLines.join("\n");
    const truncated =
      joined.length > excerptMaxChars ? joined.substring(0, excerptMaxChars) + "\n... (truncated)" : joined;

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

    return { prependContext: `[oc-memory Recall]\n${truncated}` };
  };
}

export function createPlugin(deps: HandlerDeps = {}): OpenClawPlugin {
  return {
    id: "oc-memory-recall",
    register(api) {
      api.on("before_prompt_build", createRecallHandler(deps));
    },
  };
}

const plugin = createPlugin();
export default plugin;
