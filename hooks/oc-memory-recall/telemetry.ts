import { appendFile, mkdir } from "node:fs/promises";
import path from "node:path";
import os from "node:os";

/**
 * Lightweight, opt-in JSONL telemetry for recall attempts (issue #6:
 * "Configurable recall bounding for OpenClaw memory injection").
 *
 * Disabled by default — set OC_MEMORY_RECALL_TELEMETRY=1 (or "true"/"yes")
 * to enable. This keeps the hook/plugin default-safe (no disk writes unless
 * explicitly opted in) while still giving operators a path to tune
 * OC_MEMORY_RECALL_TOP_K / OC_MEMORY_RECALL_MIN_SCORE /
 * OC_MEMORY_RECALL_EXCERPT_MAX_CHARS / OC_MEMORY_RECALL_DEDUPE_WINDOW
 * empirically, since score distributions vary by embedding model and corpus.
 *
 * When enabled, one compact JSON line is appended per recall *attempt*
 * (i.e. once the CLI has actually been invoked — not for early no-op skips
 * like the kill switch or noise filtering) to
 * OC_MEMORY_RECALL_TELEMETRY_PATH (default
 * ~/.oc-memory/recall-telemetry.jsonl).
 *
 * Telemetry never includes raw memory content — only counts, character
 * lengths, scores, and timing.
 */

function truthyFlag(raw: string | undefined): boolean {
  const v = (raw || "").trim().toLowerCase();
  return v === "1" || v === "true" || v === "yes";
}

export function isTelemetryEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  return truthyFlag(env.OC_MEMORY_RECALL_TELEMETRY);
}

export function getTelemetryPath(env: NodeJS.ProcessEnv = process.env): string {
  return (
    env.OC_MEMORY_RECALL_TELEMETRY_PATH ||
    path.join(os.homedir(), ".oc-memory", "recall-telemetry.jsonl")
  );
}

/**
 * Dependency-free, approximate token count (~4 chars/token). Good enough
 * for tuning purposes; not a real tokenizer and not meant to be exact.
 */
export function estimateTokens(text: string): number {
  if (!text) return 0;
  return Math.max(1, Math.ceil(text.length / 4));
}

export type RecallOutcome = "injected" | "empty" | "error";

export interface RecallTelemetryEvent {
  /** Length of the search query in characters (not the query itself). */
  queryChars: number;
  /** Rough token estimate for the query. */
  queryTokensEstimate: number;
  /** Effective OC_MEMORY_RECALL_TOP_K used for this attempt. */
  topK: number;
  /** Effective OC_MEMORY_RECALL_MIN_SCORE used for this attempt. */
  minScore: number;
  /** Number of result lines returned by the CLI before dedupe. */
  candidateCount: number;
  /** Number of candidate lines dropped by the in-process dedupe window. */
  dedupedCount: number;
  /** Number of result lines actually injected (0 when nothing survives). */
  injectedCount: number;
  /** Character length of the injected excerpt (0 when nothing injected). */
  injectedChars: number;
  /** Highest "sim:" score seen among candidates, when the CLI reported one. */
  topScore?: number;
  /** Wall-clock time for the CLI subprocess call, in milliseconds. */
  elapsedMs: number;
  outcome: RecallOutcome;
}

/**
 * Append one telemetry line. Best-effort and fire-and-forget-safe: any
 * filesystem/permission error is swallowed so telemetry can never delay or
 * break the fail-open recall path.
 */
export async function recordRecallTelemetry(
  evt: RecallTelemetryEvent,
  env: NodeJS.ProcessEnv = process.env
): Promise<void> {
  if (!isTelemetryEnabled(env)) return;
  const filePath = getTelemetryPath(env);
  const line = JSON.stringify({ ts: new Date().toISOString(), ...evt });
  try {
    await mkdir(path.dirname(filePath), { recursive: true });
    await appendFile(filePath, line + "\n", "utf-8");
  } catch {
    // Best-effort only — never throw into the recall hot path.
  }
}

/** Parses the highest "sim:<float>" score out of a set of CLI result lines,
 * if any are present (vector search results include this; FTS fallback and
 * fixture/test output generally do not). */
export function parseTopScore(lines: string[]): number | undefined {
  let best: number | undefined;
  const re = /sim:(-?\d+(?:\.\d+)?)/;
  for (const line of lines) {
    const m = re.exec(line);
    if (!m) continue;
    const v = Number(m[1]);
    if (Number.isFinite(v) && (best === undefined || v > best)) best = v;
  }
  return best;
}
