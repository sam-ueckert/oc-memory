import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import os from "node:os";
import { readFile, rm } from "node:fs/promises";
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
  getTopK,
  getMinScore,
  getExcerptMax,
  getExcerptMaxChars,
  getDedupeWindow,
  buildSearchArgs,
  extractResultId,
  splitResultLines,
  RecallDedupeState,
} from "../../../hooks/oc-memory-recall/handler.ts";
import { isTelemetryEnabled, getTelemetryPath } from "../../../hooks/oc-memory-recall/telemetry.ts";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const FIXTURES_DIR = path.join(__dirname, "fixtures");
const TELEMETRY_TMP_PATH = path.join(
  os.tmpdir(),
  `oc-memory-recall-telemetry-test-${process.pid}.jsonl`
);

// ── env-var helpers ──────────────────────────────────────────────────────

const ENV_KEYS = [
  "OC_MEMORY_RECALL_DISABLED",
  "OC_MEMORY_CLI",
  "OC_MEMORY_RECALL_TIMEOUT_MS",
  "OC_MEMORY_CLI_ALLOW_SHELL",
  "OC_MEMORY_RECALL_TOP_K",
  "OC_MEMORY_RECALL_MIN_SCORE",
  "OC_MEMORY_RECALL_EXCERPT_MAX",
  "OC_MEMORY_RECALL_EXCERPT_MAX_CHARS",
  "OC_MEMORY_RECALL_DEDUPE_WINDOW",
  "OC_MEMORY_RECALL_TELEMETRY",
  "OC_MEMORY_RECALL_TELEMETRY_PATH",
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

// ── recall bounding: env parsing + clamping (issue #6) ─────────────────────

describe("getTopK", () => {
  test("defaults to 5", () => {
    assert.equal(getTopK({}), 5);
  });
  test("honors an in-range value", () => {
    assert.equal(getTopK({ OC_MEMORY_RECALL_TOP_K: "8" }), 8);
  });
  test("zero/negative values fall back to the default", () => {
    assert.equal(getTopK({ OC_MEMORY_RECALL_TOP_K: "0" }), 5);
    assert.equal(getTopK({ OC_MEMORY_RECALL_TOP_K: "-3" }), 5);
  });
  test("clamps above the ceiling of 20", () => {
    assert.equal(getTopK({ OC_MEMORY_RECALL_TOP_K: "999" }), 20);
  });
  test("fractional values >0 truncate and clamp to the floor of 1", () => {
    assert.equal(getTopK({ OC_MEMORY_RECALL_TOP_K: "0.5" }), 1);
  });
  test("falls back to default on garbage input", () => {
    assert.equal(getTopK({ OC_MEMORY_RECALL_TOP_K: "not-a-number" }), 5);
  });
});

describe("getMinScore", () => {
  test("defaults to 0.0 (no filtering) — deliberate, see HOOK.md", () => {
    assert.equal(getMinScore({}), 0.0);
  });
  test("honors an in-range value", () => {
    assert.equal(getMinScore({ OC_MEMORY_RECALL_MIN_SCORE: "0.42" }), 0.42);
  });
  test("clamps above 1.0", () => {
    assert.equal(getMinScore({ OC_MEMORY_RECALL_MIN_SCORE: "5" }), 1.0);
  });
  test("clamps below 0.0", () => {
    assert.equal(getMinScore({ OC_MEMORY_RECALL_MIN_SCORE: "-2" }), 0.0);
  });
  test("falls back to default on garbage input", () => {
    assert.equal(getMinScore({ OC_MEMORY_RECALL_MIN_SCORE: "not-a-number" }), 0.0);
  });
});

describe("getExcerptMaxChars (and legacy OC_MEMORY_RECALL_EXCERPT_MAX alias)", () => {
  test("defaults to 800", () => {
    assert.equal(getExcerptMaxChars({}), 800);
  });
  test("honors the new OC_MEMORY_RECALL_EXCERPT_MAX_CHARS name", () => {
    assert.equal(getExcerptMaxChars({ OC_MEMORY_RECALL_EXCERPT_MAX_CHARS: "500" }), 500);
  });
  test("falls back to the legacy OC_MEMORY_RECALL_EXCERPT_MAX name (PR #5) when the new one is unset", () => {
    assert.equal(getExcerptMaxChars({ OC_MEMORY_RECALL_EXCERPT_MAX: "1500" }), 1500);
  });
  test("the new name takes precedence when both are set", () => {
    assert.equal(
      getExcerptMaxChars({
        OC_MEMORY_RECALL_EXCERPT_MAX_CHARS: "600",
        OC_MEMORY_RECALL_EXCERPT_MAX: "2000",
      }),
      600
    );
  });
  test("clamps below the 100-char floor", () => {
    assert.equal(getExcerptMaxChars({ OC_MEMORY_RECALL_EXCERPT_MAX_CHARS: "10" }), 100);
  });
  test("clamps above the 4000-char ceiling (tighter than PR #5's 10k ceiling)", () => {
    assert.equal(getExcerptMaxChars({ OC_MEMORY_RECALL_EXCERPT_MAX_CHARS: "9000" }), 4000);
  });
  test("getExcerptMax remains exported as a back-compat alias", () => {
    assert.equal(getExcerptMax, getExcerptMaxChars);
  });
});

describe("getDedupeWindow", () => {
  test("defaults to 3", () => {
    assert.equal(getDedupeWindow({}), 3);
  });
  test("honors an in-range value", () => {
    assert.equal(getDedupeWindow({ OC_MEMORY_RECALL_DEDUPE_WINDOW: "5" }), 5);
  });
  test("0 disables dedupe and is honored exactly (not treated as unset)", () => {
    assert.equal(getDedupeWindow({ OC_MEMORY_RECALL_DEDUPE_WINDOW: "0" }), 0);
  });
  test("clamps above the ceiling of 10", () => {
    assert.equal(getDedupeWindow({ OC_MEMORY_RECALL_DEDUPE_WINDOW: "999" }), 10);
  });
  test("negative values fall back to the default", () => {
    assert.equal(getDedupeWindow({ OC_MEMORY_RECALL_DEDUPE_WINDOW: "-1" }), 3);
  });
  test("falls back to default on garbage input", () => {
    assert.equal(getDedupeWindow({ OC_MEMORY_RECALL_DEDUPE_WINDOW: "not-a-number" }), 3);
  });
});

describe("buildSearchArgs", () => {
  test("always includes --limit and --excerpt-max", () => {
    assert.deepEqual(buildSearchArgs({ topK: 5, minScore: 0, excerptMaxChars: 800 }), [
      "--limit",
      "5",
      "--excerpt-max",
      "800",
    ]);
  });
  test("includes --min-score only when > 0 (default 0.0 adds no flag)", () => {
    assert.deepEqual(buildSearchArgs({ topK: 8, minScore: 0.4, excerptMaxChars: 1200 }), [
      "--limit",
      "8",
      "--excerpt-max",
      "1200",
      "--min-score",
      "0.4",
    ]);
  });
});

// ── in-process dedupe (issue #6) ────────────────────────────────────────

describe("extractResultId / splitResultLines", () => {
  test("extracts the leading [id] token from a formatted CLI result line", () => {
    assert.equal(extractResultId("[42] [fact] scene:x sal:0.80 — hello"), "42");
  });
  test("returns null when there is no leading [id]", () => {
    assert.equal(extractResultId("plain text, no brackets"), null);
  });
  test("splits into trimmed, non-empty lines and drops the (FTS fallback) marker", () => {
    const raw = "(FTS fallback)\n\n[1] a\n  [2] b  \n";
    assert.deepEqual(splitResultLines(raw), ["[1] a", "[2] b"]);
  });
});

describe("RecallDedupeState (unit)", () => {
  test("drops a candidate whose id was seen in the remembered window", () => {
    const state = new RecallDedupeState();
    const first = state.filterAndRecord(["[1] a", "[2] b"], 3);
    assert.equal(first.dedupedCount, 0);
    assert.deepEqual(first.keptLines, ["[1] a", "[2] b"]);

    const second = state.filterAndRecord(["[1] a", "[3] c"], 3);
    assert.equal(second.dedupedCount, 1);
    assert.deepEqual(second.keptLines, ["[3] c"]);
  });

  test("maxWindows <= 0 disables dedupe and never records state", () => {
    const state = new RecallDedupeState();
    const first = state.filterAndRecord(["[1] a"], 0);
    assert.deepEqual(first.keptLines, ["[1] a"]);
    const second = state.filterAndRecord(["[1] a"], 0);
    assert.deepEqual(second.keptLines, ["[1] a"]);
    assert.equal(second.dedupedCount, 0);
  });

  test("falls back to full-line identity when a line has no [id] prefix", () => {
    const state = new RecallDedupeState();
    state.filterAndRecord(["raw content line one"], 3);
    const second = state.filterAndRecord(["raw content line one", "a different line"], 3);
    assert.deepEqual(second.keptLines, ["a different line"]);
  });

  test("FIFO eviction: only the last maxWindows attempts are remembered", () => {
    const state = new RecallDedupeState();
    state.filterAndRecord(["[1] a"], 1); // window: [[1]]
    state.filterAndRecord(["[2] b"], 1); // window: [[2]] (evicts [1])
    const third = state.filterAndRecord(["[1] a"], 1); // [1] no longer remembered
    assert.deepEqual(third.keptLines, ["[1] a"]);
    assert.equal(third.dedupedCount, 0);
  });

  test("reset() clears remembered state", () => {
    const state = new RecallDedupeState();
    state.filterAndRecord(["[1] a"], 3);
    state.reset();
    const after = state.filterAndRecord(["[1] a"], 3);
    assert.equal(after.dedupedCount, 0);
  });
});

describe("createHandler: topK/minScore pass-through to the CLI argv", () => {
  test("passes --limit and --min-score through when both are configured", async () => {
    process.env.OC_MEMORY_RECALL_TOP_K = "7";
    process.env.OC_MEMORY_RECALL_MIN_SCORE = "0.6";
    process.env.OC_MEMORY_RECALL_EXCERPT_MAX_CHARS = "900";
    let capturedArgs: unknown;
    const fakeExecFile = ((_cli: string, args: unknown, _options: any, cb: any) => {
      capturedArgs = args;
      cb(null, "[1] [fact] scene:test sal:0.50 — a perfectly fine long enough recall result");
    }) as any;

    const handler = createHandler({ execFileFn: fakeExecFile });
    await handler(makeEvent("what did we decide about the database schema"));

    assert.deepEqual(capturedArgs, [
      "search",
      "what did we decide about the database schema",
      "--limit",
      "7",
      "--excerpt-max",
      "900",
      "--min-score",
      "0.6",
    ]);
  });

  test("omits --min-score when unset (default 0.0 means no filtering, no flag)", async () => {
    let capturedArgs: unknown;
    const fakeExecFile = ((_cli: string, args: unknown, _options: any, cb: any) => {
      capturedArgs = args;
      cb(null, "[1] [fact] scene:test sal:0.50 — a perfectly fine long enough recall result");
    }) as any;

    const handler = createHandler({ execFileFn: fakeExecFile });
    await handler(makeEvent("what did we decide about the database schema"));

    assert.deepEqual(capturedArgs, [
      "search",
      "what did we decide about the database schema",
      "--limit",
      "5",
      "--excerpt-max",
      "800",
    ]);
  });
});

describe("createHandler: FIFO dedupe across turns", () => {
  test("does not re-inject the same result on the very next call", async () => {
    const fakeExecFile = ((_cli: string, _args: unknown, _options: any, cb: any) => {
      cb(null, "[1] [fact] scene:test sal:0.50 — the same memory both times");
    }) as any;
    const handler = createHandler({ execFileFn: fakeExecFile });

    const event1 = makeEvent("what did we decide about the database schema");
    await handler(event1);
    assert.equal(event1.messages.length, 1);

    const event2 = makeEvent("what did we decide about the database schema");
    await handler(event2);
    assert.deepEqual(
      event2.messages,
      [],
      "must inject nothing at all, not an empty [oc-memory Recall] header"
    );
  });

  test("injects only the genuinely new result when old and new results overlap", async () => {
    let call = 0;
    const fakeExecFile = ((_cli: string, _args: unknown, _options: any, cb: any) => {
      call++;
      const text =
        call === 1
          ? "[1] [fact] scene:test sal:0.50 — first memory here today\n" +
            "[2] [fact] scene:test sal:0.50 — second memory here today"
          : "[1] [fact] scene:test sal:0.50 — first memory here today\n" +
            "[3] [fact] scene:test sal:0.50 — third memory is brand new";
      cb(null, text);
    }) as any;

    const handler = createHandler({ execFileFn: fakeExecFile });
    await handler(makeEvent("what did we decide about the database schema"));

    const event2 = makeEvent("what did we decide about the database schema");
    await handler(event2);

    assert.equal(event2.messages.length, 1);
    assert.ok(!event2.messages[0].includes("first memory"), "id 1 was injected last turn, should be deduped");
    assert.ok(event2.messages[0].includes("third memory"), "id 3 is new, should be injected");
  });

  test("OC_MEMORY_RECALL_DEDUPE_WINDOW=0 disables dedupe entirely", async () => {
    process.env.OC_MEMORY_RECALL_DEDUPE_WINDOW = "0";
    const fakeExecFile = ((_cli: string, _args: unknown, _options: any, cb: any) => {
      cb(null, "[1] [fact] scene:test sal:0.50 — the same memory both times");
    }) as any;
    const handler = createHandler({ execFileFn: fakeExecFile });

    await handler(makeEvent("what did we decide about the database schema"));
    const event2 = makeEvent("what did we decide about the database schema");
    await handler(event2);

    assert.equal(event2.messages.length, 1, "dedupe disabled -> same result injected again");
  });
});

// ── telemetry: opt-in, JSONL, never raw content (issue #6) ─────────────────

describe("telemetry", () => {
  afterEach(async () => {
    await rm(TELEMETRY_TMP_PATH, { force: true });
  });

  test("isTelemetryEnabled / getTelemetryPath honor env vars", () => {
    assert.equal(isTelemetryEnabled({}), false);
    assert.equal(isTelemetryEnabled({ OC_MEMORY_RECALL_TELEMETRY: "1" }), true);
    assert.equal(isTelemetryEnabled({ OC_MEMORY_RECALL_TELEMETRY: "yes" }), true);
    assert.equal(
      getTelemetryPath({ OC_MEMORY_RECALL_TELEMETRY_PATH: "/tmp/custom.jsonl" }),
      "/tmp/custom.jsonl"
    );
  });

  test("writes nothing when telemetry is disabled (default, safe)", async () => {
    process.env.OC_MEMORY_RECALL_TELEMETRY_PATH = TELEMETRY_TMP_PATH;
    const fakeExecFile = ((_cli: string, _args: unknown, _options: any, cb: any) => {
      cb(null, "[1] [fact] scene:test sal:0.50 — a perfectly fine long enough recall result");
    }) as any;
    const handler = createHandler({ execFileFn: fakeExecFile });
    await handler(makeEvent("what did we decide about the database schema"));

    await assert.rejects(() => readFile(TELEMETRY_TMP_PATH, "utf-8"));
  });

  test("appends a JSONL line with counts/timing but never raw memory content when enabled", async () => {
    process.env.OC_MEMORY_RECALL_TELEMETRY = "1";
    process.env.OC_MEMORY_RECALL_TELEMETRY_PATH = TELEMETRY_TMP_PATH;
    const secretContent = "a recalled fact about the launch codes that must not leak into telemetry";
    const fakeExecFile = ((_cli: string, _args: unknown, _options: any, cb: any) => {
      cb(null, `[1] [fact] scene:test sal:0.50 — ${secretContent}`);
    }) as any;
    const handler = createHandler({ execFileFn: fakeExecFile });
    await handler(makeEvent("what did we decide about the database schema"));

    const raw = await readFile(TELEMETRY_TMP_PATH, "utf-8");
    const lines = raw.trim().split("\n");
    assert.equal(lines.length, 1);
    const evt = JSON.parse(lines[0]);

    assert.equal(evt.outcome, "injected");
    assert.equal(evt.candidateCount, 1);
    assert.equal(evt.injectedCount, 1);
    assert.equal(evt.dedupedCount, 0);
    assert.equal(evt.topK, 5);
    assert.equal(evt.minScore, 0);
    assert.equal(typeof evt.elapsedMs, "number");
    assert.equal(typeof evt.queryChars, "number");
    assert.ok(!raw.includes(secretContent), "telemetry must never contain raw memory content");
  });

  test("records outcome=error on a fail-open CLI error, still no raw content", async () => {
    process.env.OC_MEMORY_RECALL_TELEMETRY = "1";
    process.env.OC_MEMORY_RECALL_TELEMETRY_PATH = TELEMETRY_TMP_PATH;
    const fakeExecFile = ((_cli: string, _args: unknown, _options: any, cb: any) => {
      cb(new Error("ENOENT: no such file"));
    }) as any;
    const handler = createHandler({ execFileFn: fakeExecFile });
    await handler(makeEvent("what did we decide about the database schema"));

    const raw = await readFile(TELEMETRY_TMP_PATH, "utf-8");
    const evt = JSON.parse(raw.trim().split("\n")[0]);
    assert.equal(evt.outcome, "error");
    assert.equal(evt.candidateCount, 0);
    assert.equal(evt.injectedCount, 0);
  });
});
