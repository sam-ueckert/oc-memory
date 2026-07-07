import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  sanitizeQuery,
  needsWindowsShell,
  isRecallDisabled,
  shellAllowed,
  getTimeoutMs,
  getMemCli,
  searchMemory,
  createHandler,
} from "../../../hooks/oc-memory-recall/handler.ts";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const FIXTURES_DIR = path.join(__dirname, "fixtures");

// ── env-var helpers ──────────────────────────────────────────────────────

const ENV_KEYS = [
  "OC_MEMORY_RECALL_DISABLED",
  "OC_MEMORY_CLI",
  "OC_MEMORY_RECALL_TIMEOUT_MS",
  "OC_MEMORY_CLI_ALLOW_SHELL",
];
let savedEnv: Record<string, string | undefined> = {};

beforeEach(() => {
  savedEnv = {};
  for (const k of ENV_KEYS) {
    savedEnv[k] = process.env[k];
    delete process.env[k];
  }
});

afterEach(() => {
  for (const k of ENV_KEYS) {
    if (savedEnv[k] === undefined) delete process.env[k];
    else process.env[k] = savedEnv[k];
  }
});

function makeEvent(content: string) {
  return {
    type: "message",
    action: "received",
    context: { content },
    messages: [] as string[],
  };
}

function buildNastyQuery(): string {
  // Built from parts/char codes rather than a literal string so the source
  // file stays free of ambiguous escape sequences. Contains: double quotes,
  // a semicolon, a pipe, backticks, a dollar-paren subshell, an ampersand,
  // a percent sign, and a redirect.
  const dq = String.fromCharCode(34);
  const backtick = String.fromCharCode(96);
  return (
    "two words: " +
    dq + "quoted" + dq +
    " && $(whoami) " +
    backtick + "id" + backtick +
    " | cat ; % > out.txt"
  );
}

// ── pure helper unit tests ───────────────────────────────────────────────

describe("sanitizeQuery", () => {
  test("preserves quotes and shell metacharacters", () => {
    const raw = buildNastyQuery();
    assert.equal(sanitizeQuery(raw), raw);
  });

  test("strips control characters but keeps printable text", () => {
    const NUL = String.fromCharCode(0);
    const SOH = String.fromCharCode(1);
    const DEL = String.fromCharCode(127);
    const raw = "hello" + NUL + "world" + SOH + "again" + DEL + "end";
    assert.equal(sanitizeQuery(raw), "hello world again end");
  });

  test("truncates to maxLen and trims", () => {
    const raw = "  " + "a".repeat(500) + "  ";
    const out = sanitizeQuery(raw, 10);
    assert.equal(out.length, 10);
  });
});

describe("isRecallDisabled / shellAllowed (truthy flag parsing)", () => {
  for (const v of ["1", "true", "TRUE", " yes ", "Yes"]) {
    test("truthy value " + v, () => {
      assert.equal(isRecallDisabled({ OC_MEMORY_RECALL_DISABLED: v }), true);
      assert.equal(shellAllowed({ OC_MEMORY_CLI_ALLOW_SHELL: v }), true);
    });
  }
  for (const v of [undefined, "", "0", "false", "no", "random"]) {
    test("falsy value " + JSON.stringify(v), () => {
      assert.equal(isRecallDisabled({ OC_MEMORY_RECALL_DISABLED: v } as any), false);
      assert.equal(shellAllowed({ OC_MEMORY_CLI_ALLOW_SHELL: v } as any), false);
    });
  }
});

describe("getMemCli", () => {
  test("defaults to oc-memory", () => {
    assert.equal(getMemCli({}), "oc-memory");
  });
  test("honors OC_MEMORY_CLI", () => {
    assert.equal(getMemCli({ OC_MEMORY_CLI: "/path/to/mem" }), "/path/to/mem");
  });
});

describe("getTimeoutMs", () => {
  test("defaults to 2500ms", () => {
    assert.equal(getTimeoutMs({}), 2500);
  });
  test("honors an in-range override", () => {
    assert.equal(getTimeoutMs({ OC_MEMORY_RECALL_TIMEOUT_MS: "1000" }), 1000);
  });
  test("clamps below the 500ms floor", () => {
    assert.equal(getTimeoutMs({ OC_MEMORY_RECALL_TIMEOUT_MS: "10" }), 500);
  });
  test("clamps above the 3000ms ceiling", () => {
    assert.equal(getTimeoutMs({ OC_MEMORY_RECALL_TIMEOUT_MS: "9999" }), 3000);
  });
  test("falls back to default on garbage input", () => {
    assert.equal(getTimeoutMs({ OC_MEMORY_RECALL_TIMEOUT_MS: "not-a-number" }), 2500);
  });
});

describe("needsWindowsShell", () => {
  test("true for .cmd on win32", () => {
    assert.equal(needsWindowsShell("C:/Users/heyni/bin/oc-memory-cherry.cmd", "win32"), true);
  });
  test("true for .bat on win32 (case-insensitive)", () => {
    assert.equal(needsWindowsShell("C:/tools/mem.BAT", "win32"), true);
  });
  test("false for a plain binary on win32", () => {
    assert.equal(needsWindowsShell("C:/tools/oc-memory.exe", "win32"), false);
  });
  test("false for .cmd on non-Windows platforms", () => {
    assert.equal(needsWindowsShell("/usr/local/bin/mem.cmd", "linux"), false);
    assert.equal(needsWindowsShell("/usr/local/bin/mem.cmd", "darwin"), false);
  });
});

// ── searchMemory: argv-array wiring proof (fake execFileFn, no real spawn) ─

describe("searchMemory argv wiring", () => {
  test("passes the query as a single argv element, never a shell string", async () => {
    const nasty = buildNastyQuery();
    let capturedArgs: unknown;
    let capturedOptions: any;

    const fakeExecFile = ((_cli: string, args: unknown, options: any, cb: any) => {
      capturedArgs = args;
      capturedOptions = options;
      cb(null, "ok result from fixture that is long enough to pass the length gate");
    }) as any;

    const out = await searchMemory(nasty, {
      cli: "oc-memory",
      timeoutMs: 2500,
      useShell: false,
      execFileFn: fakeExecFile,
    });

    assert.deepEqual(capturedArgs, ["search", nasty]);
    assert.equal(capturedOptions.shell, false);
    assert.equal(capturedOptions.timeout, 2500);
    assert.match(out, /ok result/);
  });

  test("wires shell:true only when explicitly requested (e.g. .cmd wrapper)", async () => {
    let capturedOptions: any;
    const fakeExecFile = ((_cli: string, _args: unknown, options: any, cb: any) => {
      capturedOptions = options;
      cb(null, "fine, thanks for asking, this is plenty long");
    }) as any;

    await searchMemory("two words", {
      cli: "C:/Users/heyni/bin/oc-memory-cherry.cmd",
      timeoutMs: 2500,
      useShell: true,
      execFileFn: fakeExecFile,
    });

    assert.equal(capturedOptions.shell, true);
  });

  test("rejects when the child callback reports an error", async () => {
    const fakeExecFile = ((_cli: string, _args: unknown, _options: any, cb: any) => {
      cb(new Error("boom"));
    }) as any;

    await assert.rejects(
      () =>
        searchMemory("two words", {
          cli: "oc-memory",
          timeoutMs: 2500,
          useShell: false,
          execFileFn: fakeExecFile,
        }),
      /boom/
    );
  });
});

// ── createHandler: full message-lifecycle behavior (fake execFileFn) ──────

describe("createHandler behavior", () => {
  test("kill switch short-circuits before any exec call", async () => {
    process.env.OC_MEMORY_RECALL_DISABLED = "1";
    const fakeExecFile = (() => {
      throw new Error("should never be called while disabled");
    }) as any;

    const handler = createHandler({ execFileFn: fakeExecFile });
    const event = makeEvent("what did we decide about the database schema");
    await handler(event);

    assert.deepEqual(event.messages, []);
  });

  test("happy path: injects recall message on a good result", async () => {
    const fakeExecFile = ((_cli: string, _args: unknown, _options: any, cb: any) => {
      cb(null, "we decided to use SQLite with FTS5 for structured recall storage");
    }) as any;

    const handler = createHandler({ execFileFn: fakeExecFile });
    const event = makeEvent("what did we decide about the database schema");
    await handler(event);

    assert.equal(event.messages.length, 1);
    assert.match(event.messages[0], /^\[oc-memory Recall\]/);
    assert.match(event.messages[0], /SQLite/);
  });

  test("fail-open: CLI error does not throw and injects nothing", async () => {
    const fakeExecFile = ((_cli: string, _args: unknown, _options: any, cb: any) => {
      cb(new Error("ENOENT: no such file"));
    }) as any;

    const handler = createHandler({ execFileFn: fakeExecFile });
    const event = makeEvent("what did we decide about the database schema");
    await assert.doesNotReject(() => handler(event));
    assert.deepEqual(event.messages, []);
  });

  test("skips short messages, heartbeats, and single-word queries", async () => {
    const fakeExecFile = (() => {
      throw new Error("should never be called for skip cases");
    }) as any;
    const handler = createHandler({ execFileFn: fakeExecFile });

    const short = makeEvent("hi");
    await handler(short);
    assert.deepEqual(short.messages, []);

    const heartbeat = makeEvent("HEARTBEAT check in please and thank you");
    await handler(heartbeat);
    assert.deepEqual(heartbeat.messages, []);

    const oneWord = makeEvent("wordwordwordwordwordwordwordword");
    await handler(oneWord);
    assert.deepEqual(oneWord.messages, []);
  });

  test("skips short or 'No results' recall output", async () => {
    const handler1 = createHandler({
      execFileFn: ((_c: string, _a: unknown, _o: any, cb: any) => cb(null, "No results found.")) as any,
    });
    const e1 = makeEvent("what did we decide about the database schema");
    await handler1(e1);
    assert.deepEqual(e1.messages, []);

    const handler2 = createHandler({
      execFileFn: ((_c: string, _a: unknown, _o: any, cb: any) => cb(null, "short")) as any,
    });
    const e2 = makeEvent("what did we decide about the database schema");
    await handler2(e2);
    assert.deepEqual(e2.messages, []);
  });

  test("shell option follows platform + OC_MEMORY_CLI_ALLOW_SHELL", async () => {
    process.env.OC_MEMORY_CLI = "C:/Users/heyni/bin/oc-memory-cherry.cmd";

    let capturedShell: unknown;
    const fakeExecFile = ((_cli: string, _args: unknown, options: any, cb: any) => {
      capturedShell = options.shell;
      cb(null, "a perfectly fine long enough recall result for this test case");
    }) as any;

    // Not allowed by default, even on a .cmd path.
    const handlerNoOverride = createHandler({ execFileFn: fakeExecFile, platform: "win32" });
    await handlerNoOverride(makeEvent("what did we decide about the database schema"));
    assert.equal(capturedShell, false);

    // Explicit opt-in required.
    process.env.OC_MEMORY_CLI_ALLOW_SHELL = "1";
    const handlerAllowed = createHandler({ execFileFn: fakeExecFile, platform: "win32" });
    await handlerAllowed(makeEvent("what did we decide about the database schema"));
    assert.equal(capturedShell, true);

    // Not a .cmd/.bat path -> shell stays false even with the flag set.
    process.env.OC_MEMORY_CLI = "/usr/local/bin/oc-memory";
    const handlerNonWindows = createHandler({ execFileFn: fakeExecFile, platform: "linux" });
    await handlerNonWindows(makeEvent("what did we decide about the database schema"));
    assert.equal(capturedShell, false);
  });
});

// ── real subprocess integration tests (no shell, no mocks) ────────────────
//
// cli = the Node binary itself, cwd = fixtures/, so the arg array
// ["search", query] resolves to running fixtures/search with `query` as its
// only argv entry -- a faithful, shell-free stand-in for a real
// `<oc-memory-cli> search "<query>"` invocation.

describe("real subprocess integration (fixtures/search)", () => {
  test("query with quotes/shell metacharacters survives intact through a real child process", async () => {
    const nasty = buildNastyQuery();
    const out = await searchMemory(nasty, {
      cli: process.execPath,
      timeoutMs: 2500,
      useShell: false,
      cwd: FIXTURES_DIR,
    });
    assert.match(out, /oc-memory recall fixture result ::/);
    assert.ok(out.includes(nasty), "fixture must echo the exact original query, unmangled");
  });

  test("a stalling child is killed at the timeout and the caller fails open, without blocking the event loop", async () => {
    let ticks = 0;
    const ticker = setInterval(() => {
      ticks++;
    }, 20);

    const start = Date.now();
    await assert.rejects(() =>
      searchMemory("please __STALL__ forever thanks", {
        cli: process.execPath,
        timeoutMs: 300,
        useShell: false,
        cwd: FIXTURES_DIR,
      })
    );
    const elapsed = Date.now() - start;

    clearInterval(ticker);

    // Should resolve close to the 300ms timeout budget, nowhere near the
    // fixture's 60s stall.
    assert.ok(elapsed < 3000, "expected fast fail-open, took " + elapsed + "ms");
    // The interval kept firing while we awaited the child process, proving
    // the event loop was not blocked during the wait.
    assert.ok(ticks > 0, "event loop should keep processing timers while recall runs");
  });

  test("a non-zero exit from the CLI rejects (caller fails open)", async () => {
    await assert.rejects(() =>
      searchMemory("please __FAIL__ now thanks", {
        cli: process.execPath,
        timeoutMs: 2500,
        useShell: false,
        cwd: FIXTURES_DIR,
      })
    );
  });

  test("createHandler end-to-end against the real fixture CLI", async () => {
    process.env.OC_MEMORY_CLI = process.execPath;
    const handler = createHandler({ cwd: FIXTURES_DIR, platform: process.platform });
    const event = makeEvent("tell me something about the project architecture please");
    await handler(event);

    assert.equal(event.messages.length, 1);
    assert.match(event.messages[0], /^\[oc-memory Recall\]/);
    assert.match(event.messages[0], /oc-memory recall fixture result ::/);
  });
});
