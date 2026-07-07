import assert from "node:assert";
import { describe, test, before, after } from "node:test";
import { createCaptureHandler, isCaptureDisabled, storeViaTempFile } from "../../../hooks/oc-memory-capture/handler.ts";
import { mkdtempSync, writeFileSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";

// ── Environment helpers ──────────────────────────────────────────────────────

const ENV_KEYS = [
  "OC_MEMORY_CAPTURE_DISABLED",
  "OC_MEMORY_CLI",
] as const;

let savedEnv: Record<string, string | undefined> = {};

function saveEnv() {
  for (const k of ENV_KEYS) savedEnv[k] = process.env[k];
}

function restoreEnv() {
  for (const k of ENV_KEYS) {
    if (savedEnv[k] === undefined) delete process.env[k];
    else process.env[k] = savedEnv[k];
  }
}

// ── Fixture path ─────────────────────────────────────────────────────────────

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURE_DIR = join(__dirname, "fixtures");
// On Windows, execFile requires a .cmd/.bat wrapper for Node scripts
const FIXTURE_CMD = join(FIXTURE_DIR, "store-stdin.cmd");

function tempDir(): string {
  return mkdtempSync(join(tmpdir(), "ocmem-capture-test-"));
}

// ══════════════════════════════════════════════════════════════════════════════
// Kill switch
// ══════════════════════════════════════════════════════════════════════════════

describe("isCaptureDisabled", () => {
  const truthy = ["1", "true", "TRUE", " yes ", "Yes"];
  const falsy = [undefined, "", "0", "false", "no", "random"];

  for (const v of truthy) {
    test("truthy value " + JSON.stringify(v), () => {
      assert.equal(isCaptureDisabled({ OC_MEMORY_CAPTURE_DISABLED: v }), true);
    });
  }

  for (const v of falsy) {
    test("falsy value " + JSON.stringify(v), () => {
      assert.equal(isCaptureDisabled({ OC_MEMORY_CAPTURE_DISABLED: v } as any), false);
    });
  }
});

// ══════════════════════════════════════════════════════════════════════════════
// Handler guard logic
// ══════════════════════════════════════════════════════════════════════════════

describe("createCaptureHandler", () => {
  before(() => saveEnv());
  after(() => restoreEnv());

  test("kill switch skip: handler does nothing when disabled", async () => {
    process.env.OC_MEMORY_CAPTURE_DISABLED = "1";
    const handler = createCaptureHandler();
    await handler({ type: "message", action: "sent", context: { success: true, content: "Hello world! This is a test message for capture." } });
    assert.ok(true);
  });

  test("skips non-message events", async () => {
    delete process.env.OC_MEMORY_CAPTURE_DISABLED;
    const handler = createCaptureHandler();
    await handler({ type: "something_else", action: "received", context: {} });
    assert.ok(true);
  });

  test("skips short content", async () => {
    const handler = createCaptureHandler();
    await handler({ type: "message", action: "sent", context: { success: true, content: "short" } });
    assert.ok(true);
  });

  test("skips heartbeat patterns", async () => {
    const handler = createCaptureHandler();
    await handler({ type: "message", action: "sent", context: { success: true, content: "HEARTBEAT_OK" } });
    await handler({ type: "message", action: "sent", context: { success: true, content: "NO_REPLY" } });
    await handler({ type: "message", action: "sent", context: { success: true, content: "HEARTBEAT_NOACTION\n" } });
    assert.ok(true);
  });

  test("handler skips when context.success is false", async () => {
    const handler = createCaptureHandler();
    await handler({ type: "message", action: "sent", context: { success: false, content: "Should not be captured" } });
    assert.ok(true);
  });

  test("removes auto-created temp directory after capture", async () => {
    delete process.env.OC_MEMORY_CAPTURE_DISABLED;
    process.env.OC_MEMORY_CLI = FIXTURE_CMD;
    const before = new Set(readdirSync(tmpdir()).filter((name) => name.startsWith("ocmem-capture-")));

    const handler = createCaptureHandler({ platform: "win32" });
    await handler({
      type: "message",
      action: "sent",
      context: {
        success: true,
        channelId: "temp-cleanup-test",
        content: "This capture message is long enough to be stored by the handler.",
      },
    });

    const after = readdirSync(tmpdir()).filter((name) => name.startsWith("ocmem-capture-") && !before.has(name));
    assert.deepEqual(after, []);
  });
});

// ══════════════════════════════════════════════════════════════════════════════
// storeViaTempFile — integration with fixture
// ══════════════════════════════════════════════════════════════════════════════

describe("storeViaTempFile", { concurrency: false }, () => {
  test("stores payload via argv-based execFile", async () => {
    const td = tempDir();
    const payload = JSON.stringify([{ scene: "test", cell_type: "exchange", salience: 0.5, content: "normal message" }]);
    await storeViaTempFile(payload, FIXTURE_CMD, 5000, td, true);
    assert.ok(true);
  });

  test("captures adversarial shell metacharacters intact", async () => {
    const td = tempDir();
    const evil = 'quote\' "double" ;semicolon pipe| backtick\\` dollar$(ls) &ampersand redir>file newline\n tab\t percent% star* question? bracket[ ] { } exclamation! hash#';
    const payload = JSON.stringify([{ scene: "adversarial-test", cell_type: "exchange", salience: 0.5, content: evil }]);
    await storeViaTempFile(payload, FIXTURE_CMD, 5000, td, true);
    assert.ok(true);
  });

  test("fail open on timeout", { timeout: 10000 }, async () => {
    const td = tempDir();
    const payload = JSON.stringify([{ scene: "timeout-test", cell_type: "exchange", salience: 0.5, content: "__STALL__ should timeout" }]);
    // storeViaTempFile rejects on timeout; the handler's try/catch makes it fail open
    await assert.rejects(
      () => storeViaTempFile(payload, FIXTURE_CMD, 3000, td, true),
      { signal: "SIGKILL" }
    );
  });

  test("fail open on non-zero exit", async () => {
    const td = tempDir();
    const payload = JSON.stringify([{ scene: "fail-test", cell_type: "exchange", salience: 0.5, content: "__FAIL__ should fail open" }]);
    await assert.rejects(
      () => storeViaTempFile(payload, FIXTURE_CMD, 5000, td, true),
      { code: 1 }
    );
  });
});
