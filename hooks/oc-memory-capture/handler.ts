import { execFile } from "node:child_process";
import { writeFileSync, unlinkSync, mkdtempSync, rmdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// Path to your oc-memory CLI wrapper. Adjust if installed differently.
const MEM_CLI = process.env.OC_MEMORY_CLI || "oc-memory";

// Hard timeout for capture writes — capture must never block message delivery.
const CAPTURE_TIMEOUT_MS = 3000;

function truthyFlag(raw: string | undefined): boolean {
  const v = (raw || "").trim().toLowerCase();
  return v === "1" || v === "true" || v === "yes";
}

/**
 * Kill switch: set OC_MEMORY_CAPTURE_DISABLED=1 to turn the capture hook off
 * entirely without uninstalling it. Checked before reading event content.
 */
export function isCaptureDisabled(env: NodeJS.ProcessEnv = process.env): boolean {
  return truthyFlag(env.OC_MEMORY_CAPTURE_DISABLED);
}

/**
 * Windows can't CreateProcess a .cmd/.bat file directly in all Node versions
 * — cmd.exe is needed as interpreter. When the CLI path ends in .cmd or .bat
 * on Windows, use shell mode.
 */
function needsWindowsShell(
  cliPath: string,
  platform: NodeJS.Platform = process.platform
): boolean {
  return platform === "win32" && /\\.(cmd|bat)$/i.test(cliPath);
}

/**
 * Write `data` to a temp file, then invoke `<cli> store-stdin <tmpfile>` as an
 * argv-based child process (never a shell pipeline). Deletes the temp file
 * after the child exits (success or failure).
 *
 * On Windows, .cmd/.bat wrapper CLIs use shell mode for CreateProcess
 * compatibility. All data flows through a temp file, not stdin/stdout shell
 * piping, so shell mode does not introduce command-injection risk.
 *
 * Async, bounded timeout, fail-open.
 */
export function storeViaTempFile(
  data: string,
  cli: string,
  timeoutMs: number,
  tempDir: string,
  useShell: boolean
): Promise<void> {
  return new Promise((resolve, reject) => {
    const tmpPath = join(tempDir, "capture-payload.json");
    try {
      writeFileSync(tmpPath, data, "utf-8");
    } catch (err) {
      reject(err);
      return;
    }

    try {
      execFile(
        cli,
        ["store-stdin", tmpPath],
        {
          timeout: timeoutMs,
          killSignal: "SIGKILL",
          encoding: "utf-8",
          windowsHide: true,
          shell: useShell,
        } as any,
        (err: unknown) => {
          // Clean up temp file regardless of outcome
          try { unlinkSync(tmpPath); } catch { /* ignore */ }

          if (err) {
            reject(err);
            return;
          }
          resolve();
        }
      );
    } catch (err) {
      try { unlinkSync(tmpPath); } catch { /* ignore */ }
      reject(err);
    }
  });
}

// ── Handler ──────────────────────────────────────────────────────────────────

export interface CaptureHandlerDeps {
  /** Test injection point for child_process.execFile */
  execFileFn?: typeof execFile;
  /** Override process.platform (test injection) */
  platform?: NodeJS.Platform;
  /** Temp directory override (test injection). Default: os.tmpdir() */
  tempDir?: string;
}

export function createCaptureHandler(deps: CaptureHandlerDeps = {}) {
  return async (event: any) => {
    // Kill switch first — before touching event content.
    if (isCaptureDisabled()) return;

    if (event.type !== "message" || event.action !== "sent") return;
    if (!event.context?.success) return;

    const outbound = event.context?.content;
    if (!outbound || outbound.length < 20) return;

    // Skip non-substantive responses
    const skipPatterns = ["NO_REPLY", "HEARTBEAT_OK", "HEARTBEAT_NOACTION"];
    const outTrimmed = outbound.trim();
    if (skipPatterns.some((p) => outTrimmed === p || outTrimmed.startsWith(p + "\n"))) return;

    const channel = event.context?.channelId || "unknown";
    const now = new Date();
    const dateStr = now.toISOString().split("T")[0];
    const scene = `conv-${dateStr}`;

    const maxLen = 2000;
    const stored = outbound.length > maxLen
      ? outbound.substring(0, maxLen) + " [...truncated]"
      : outbound;

    const cell = `[${channel}] ${stored}`;

    const storePayload = JSON.stringify([{
      scene,
      cell_type: "exchange",
      salience: 0.5,
      content: cell.replace(/\n/g, " ").substring(0, 2000),
    }]);

    const cli = process.env.OC_MEMORY_CLI || MEM_CLI;
    const useShell = needsWindowsShell(cli, deps.platform);
    const timeoutMs = CAPTURE_TIMEOUT_MS;

    let tempDir: string | undefined;
    let cleanupTempDir = false;

    try {
      tempDir = deps.tempDir;
      if (!tempDir) {
        tempDir = mkdtempSync(join(tmpdir(), "ocmem-capture-"));
        cleanupTempDir = true;
      }

      await storeViaTempFile(storePayload, cli, timeoutMs, tempDir, useShell);
    } catch {
      // Timeout, missing CLI, non-zero exit — fail open silently.
    } finally {
      if (cleanupTempDir && tempDir) {
        try { rmdirSync(tempDir); } catch { /* ignore */ }
      }
    }
  };
}

const handler = createCaptureHandler();
export default handler;
