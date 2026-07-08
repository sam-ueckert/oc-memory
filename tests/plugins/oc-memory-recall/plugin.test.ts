import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import os from "node:os";
import { readFileSync } from "node:fs";
import { readFile, rm } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import {
  buildQuery,
  createRecallHandler,
  createPlugin,
} from "../../../plugins/oc-memory-recall/index.ts";
import { isTelemetryEnabled } from "../../../hooks/oc-memory-recall/telemetry.ts";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const FIXTURES_DIR = path.join(__dirname, "..", "..", "hooks", "oc-memory-recall", "fixtures");
const PLUGIN_SOURCE = path.join(__dirname, "..", "..", "..", "plugins", "oc-memory-recall", "index.ts");
const TELEMETRY_TMP_PATH = path.join(
  os.tmpdir(),
  `oc-memory-recall-plugin-telemetry-test-${process.pid}.jsonl`
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

function makePromptEvent(
  prompt: string,
  messages: Array<{ role?: string; content?: string } | string> = []
) {
  return { prompt, messages };
}

// ── buildQuery ────────────────────────────────────────────────────────────

describe("buildQuery", () => {
  test("uses the prompt as the primary query text", () => {
    const q = buildQuery(makePromptEvent("what did we decide about the database schema"));
    assert.match(q, /database schema/);
  });

  test("appends bounded recent message excerpts", () => {
    const q = buildQuery(
      makePromptEvent("follow up question", [
        { role: "user", content: "we chose SQLite with FTS5 earlier" },
        { role: "assistant", content: "confirmed, SQLite it is" },
      ])
    );
    assert.match(q, /follow up question/);
    assert.match(q, /SQLite/);
  });

  test("only considers the last few messages, not the whole history", () => {
    const messages = Array.from({ length: 10 }, (_, i) => ({
      role: "user",
      content: `message-number-${i}`,
    }));
    const q = buildQuery(makePromptEvent("prompt text here", messages));
    assert.ok(!q.includes("message-number-0"), "should not include old messages");
    assert.ok(q.includes("message-number-9"), "should include the most recent message");
  });

  test("handles missing prompt/messages gracefully", () => {
    assert.equal(buildQuery({}), "");
  });
});

// ── createPlugin: registration ────────────────────────────────────────────

describe("createPlugin", () => {
  test("registers a handler on before_prompt_build", () => {
    const registered: Array<{ event: string; handler: unknown }> = [];
    const fakeApi = {
      on(eventName: string, handler: unknown) {
        registered.push({ event: eventName, handler });
      },
    };

    const plugin = createPlugin();
    assert.equal(plugin.id, "oc-memory-recall");

    plugin.register(fakeApi as any);

    assert.equal(registered.length, 1);
    assert.equal(registered[0].event, "before_prompt_build");
    assert.equal(typeof registered[0].handler, "function");
  });
});

// ── createRecallHandler: behavior ──────────────────────────────────────────

describe("createRecallHandler behavior", () => {
  test("kill switch short-circuits before any exec call and returns undefined", async () => {
    process.env.OC_MEMORY_RECALL_DISABLED = "1";
    const fakeExecFile = (() => {
      throw new Error("should never be called while disabled");
    }) as any;

    const handler = createRecallHandler({ execFileFn: fakeExecFile });
    const result = await handler(
      makePromptEvent("what did we decide about the database schema")
    );

    assert.equal(result, undefined);
  });

  test("happy path: returns prompt-visible prependContext on a good result", async () => {
    const fakeExecFile = ((_cli: string, _args: unknown, _options: any, cb: any) => {
      cb(null, "we decided to use SQLite with FTS5 for structured recall storage");
    }) as any;

    const handler = createRecallHandler({ execFileFn: fakeExecFile });
    const result = await handler(
      makePromptEvent("what did we decide about the database schema")
    );

    assert.ok(result, "expected a result");
    assert.match(result!.prependContext, /^\[oc-memory Recall\]/);
    assert.match(result!.prependContext, /SQLite/);
  });

  test("passes the query as a single argv element built from prompt + recent messages", async () => {
    let capturedArgs: unknown;
    const fakeExecFile = ((_cli: string, args: unknown, _options: any, cb: any) => {
      capturedArgs = args;
      cb(null, "a perfectly fine long enough recall result for this test case");
    }) as any;

    const handler = createRecallHandler({ execFileFn: fakeExecFile });
    await handler(
      makePromptEvent("what did we decide", [{ role: "user", content: "about the database schema" }])
    );

    assert.equal((capturedArgs as string[])[0], "search");
    assert.match((capturedArgs as string[])[1], /what did we decide/);
  });

  test("fail-open: timeout/error/missing CLI returns undefined, does not throw", async () => {
    const fakeExecFile = ((_cli: string, _args: unknown, _options: any, cb: any) => {
      cb(new Error("ENOENT: no such file"));
    }) as any;

    const handler = createRecallHandler({ execFileFn: fakeExecFile });
    let result: unknown;
    await assert.doesNotReject(async () => {
      result = await handler(makePromptEvent("what did we decide about the database schema"));
    });
    assert.equal(result, undefined);
  });

  test("skips noise: short prompts, heartbeats, single-word queries", async () => {
    const fakeExecFile = (() => {
      throw new Error("should never be called for skip cases");
    }) as any;
    const handler = createRecallHandler({ execFileFn: fakeExecFile });

    assert.equal(await handler(makePromptEvent("hi")), undefined);
    assert.equal(
      await handler(makePromptEvent("HEARTBEAT check in please and thank you")),
      undefined
    );
    assert.equal(await handler(makePromptEvent("wordwordwordwordwordword")), undefined);
  });

  test("skips short or 'No results' recall output", async () => {
    const handlerNoResults = createRecallHandler({
      execFileFn: ((_c: string, _a: unknown, _o: any, cb: any) => cb(null, "No results found.")) as any,
    });
    assert.equal(
      await handlerNoResults(makePromptEvent("what did we decide about the database schema")),
      undefined
    );

    const handlerShort = createRecallHandler({
      execFileFn: ((_c: string, _a: unknown, _o: any, cb: any) => cb(null, "short")) as any,
    });
    assert.equal(
      await handlerShort(makePromptEvent("what did we decide about the database schema")),
      undefined
    );
  });
});

// ── no capture/write path is invoked ───────────────────────────────────────

describe("recall-only: no capture/write path", () => {
  test("plugin module source does not import the capture hook or any store/write helpers", () => {
    const source = readFileSync(PLUGIN_SOURCE, "utf-8");
    assert.ok(!source.includes("oc-memory-capture"), "must not import the capture hook");
    assert.ok(!source.includes("storeViaTempFile"), "must not invoke the capture write path");
    assert.ok(!/\bstore-stdin\b/.test(source), "must not invoke the CLI's write subcommand");
  });

  test("a successful recall only ever calls the CLI with a 'search' argv, never 'store*'", async () => {
    const calls: unknown[][] = [];
    const fakeExecFile = ((_cli: string, args: unknown, _options: any, cb: any) => {
      calls.push(args as unknown[]);
      cb(null, "we decided to use SQLite with FTS5 for structured recall storage");
    }) as any;

    const handler = createRecallHandler({ execFileFn: fakeExecFile });
    await handler(makePromptEvent("what did we decide about the database schema"));

    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], "search");
  });
});

// ── real subprocess integration (no shell, no mocks) ───────────────────────

describe("real subprocess integration (fixtures/search)", () => {
  test("createRecallHandler end-to-end against the real fixture CLI", async () => {
    process.env.OC_MEMORY_CLI = process.execPath;
    const handler = createRecallHandler({ cwd: FIXTURES_DIR, platform: process.platform });
    const result = await handler(
      makePromptEvent("tell me something about the project architecture please")
    );

    assert.ok(result);
    assert.match(result!.prependContext, /^\[oc-memory Recall\]/);
    assert.match(result!.prependContext, /oc-memory recall fixture result ::/);
  });

  test("a stalling child is killed at the timeout and the handler fails open", async () => {
    process.env.OC_MEMORY_CLI = process.execPath;
    process.env.OC_MEMORY_RECALL_TIMEOUT_MS = "300";
    const handler = createRecallHandler({ cwd: FIXTURES_DIR, platform: process.platform });

    const start = Date.now();
    const result = await handler(makePromptEvent("please __STALL__ forever thanks"));
    const elapsed = Date.now() - start;

    assert.equal(result, undefined);
    assert.ok(elapsed < 3000, "expected fast fail-open, took " + elapsed + "ms");
  });

  test("a non-zero exit from the CLI fails open", async () => {
    process.env.OC_MEMORY_CLI = process.execPath;
    const handler = createRecallHandler({ cwd: FIXTURES_DIR, platform: process.platform });
    const result = await handler(makePromptEvent("please __FAIL__ now thanks"));
    assert.equal(result, undefined);
  });
});

// ── recall bounding: topK/minScore pass-through, dedupe, telemetry ─────────
//
// The env-parsing/clamping helpers themselves (getTopK, getMinScore,
// getExcerptMaxChars, getDedupeWindow, buildSearchArgs, RecallDedupeState)
// are unit-tested against the shared implementation in
// tests/hooks/oc-memory-recall/handler.test.ts; these tests cover the
// plugin's own wiring of that shared logic through createRecallHandler.

describe("createRecallHandler: topK/minScore pass-through to the CLI argv", () => {
  test("passes --limit and --min-score through when both are configured", async () => {
    process.env.OC_MEMORY_RECALL_TOP_K = "7";
    process.env.OC_MEMORY_RECALL_MIN_SCORE = "0.6";
    let capturedArgs: unknown;
    const fakeExecFile = ((_cli: string, args: unknown, _options: any, cb: any) => {
      capturedArgs = args;
      cb(null, "[1] [fact] scene:test sal:0.50 — a perfectly fine long enough recall result");
    }) as any;

    const handler = createRecallHandler({ execFileFn: fakeExecFile });
    await handler(makePromptEvent("what did we decide about the database schema"));

    const args = capturedArgs as string[];
    assert.equal(args[0], "search");
    assert.deepEqual(args.slice(-4), ["--limit", "7", "--min-score", "0.6"]);
  });

  test("omits --min-score when unset (default 0.0 means no filtering, no flag)", async () => {
    let capturedArgs: unknown;
    const fakeExecFile = ((_cli: string, args: unknown, _options: any, cb: any) => {
      capturedArgs = args;
      cb(null, "[1] [fact] scene:test sal:0.50 — a perfectly fine long enough recall result");
    }) as any;

    const handler = createRecallHandler({ execFileFn: fakeExecFile });
    await handler(makePromptEvent("what did we decide about the database schema"));

    const args = capturedArgs as string[];
    assert.deepEqual(args.slice(-2), ["--limit", "5"]);
  });
});

describe("createRecallHandler: FIFO dedupe across turns", () => {
  test("does not re-return the same result on the very next call", async () => {
    const fakeExecFile = ((_cli: string, _args: unknown, _options: any, cb: any) => {
      cb(null, "[1] [fact] scene:test sal:0.50 — the same memory both times");
    }) as any;
    const handler = createRecallHandler({ execFileFn: fakeExecFile });

    const first = await handler(makePromptEvent("what did we decide about the database schema"));
    assert.ok(first);

    const second = await handler(makePromptEvent("what did we decide about the database schema"));
    assert.equal(
      second,
      undefined,
      "must return undefined (no prependContext at all), not an empty header"
    );
  });

  test("returns only the genuinely new result when old and new results overlap", async () => {
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

    const handler = createRecallHandler({ execFileFn: fakeExecFile });
    await handler(makePromptEvent("what did we decide about the database schema"));
    const second = await handler(makePromptEvent("what did we decide about the database schema"));

    assert.ok(second);
    assert.ok(!second!.prependContext.includes("first memory"));
    assert.ok(second!.prependContext.includes("third memory"));
  });

  test("OC_MEMORY_RECALL_DEDUPE_WINDOW=0 disables dedupe entirely", async () => {
    process.env.OC_MEMORY_RECALL_DEDUPE_WINDOW = "0";
    const fakeExecFile = ((_cli: string, _args: unknown, _options: any, cb: any) => {
      cb(null, "[1] [fact] scene:test sal:0.50 — the same memory both times");
    }) as any;
    const handler = createRecallHandler({ execFileFn: fakeExecFile });

    const first = await handler(makePromptEvent("what did we decide about the database schema"));
    const second = await handler(makePromptEvent("what did we decide about the database schema"));

    assert.ok(first);
    assert.ok(second, "dedupe disabled -> same result returned again");
  });
});

describe("telemetry (plugin wiring)", () => {
  afterEach(async () => {
    await rm(TELEMETRY_TMP_PATH, { force: true });
  });

  test("writes nothing when telemetry is disabled (default, safe)", async () => {
    process.env.OC_MEMORY_RECALL_TELEMETRY_PATH = TELEMETRY_TMP_PATH;
    assert.equal(isTelemetryEnabled(process.env), false);

    const fakeExecFile = ((_cli: string, _args: unknown, _options: any, cb: any) => {
      cb(null, "[1] [fact] scene:test sal:0.50 — a perfectly fine long enough recall result");
    }) as any;
    const handler = createRecallHandler({ execFileFn: fakeExecFile });
    await handler(makePromptEvent("what did we decide about the database schema"));

    await assert.rejects(() => readFile(TELEMETRY_TMP_PATH, "utf-8"));
  });

  test("appends a JSONL line with counts/timing but never raw memory content when enabled", async () => {
    process.env.OC_MEMORY_RECALL_TELEMETRY = "1";
    process.env.OC_MEMORY_RECALL_TELEMETRY_PATH = TELEMETRY_TMP_PATH;
    const secretContent = "a recalled fact about the launch codes that must not leak into telemetry";
    const fakeExecFile = ((_cli: string, _args: unknown, _options: any, cb: any) => {
      cb(null, `[1] [fact] scene:test sal:0.50 — ${secretContent}`);
    }) as any;
    const handler = createRecallHandler({ execFileFn: fakeExecFile });
    await handler(makePromptEvent("what did we decide about the database schema"));

    const raw = await readFile(TELEMETRY_TMP_PATH, "utf-8");
    const lines = raw.trim().split("\n");
    assert.equal(lines.length, 1);
    const evt = JSON.parse(lines[0]);

    assert.equal(evt.outcome, "injected");
    assert.equal(evt.injectedCount, 1);
    assert.equal(evt.dedupedCount, 0);
    assert.ok(!raw.includes(secretContent), "telemetry must never contain raw memory content");
  });
});
