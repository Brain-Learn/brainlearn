import { afterEach, expect, test, vi } from "vitest";

import {
  cancelRun,
  mergeEvents,
  openArtifact,
  parseSseEvents,
  reviewRun,
  startRun,
  subscribeRunEvents,
} from "./runs";
import type { RunEvent, RunRecord, RunResponse } from "./types";

function event(seq: number, kind: string): RunEvent {
  return {
    schema_version: "1.0",
    seq,
    at: "2026-09-13T00:00:00+00:00",
    kind: kind as RunEvent["kind"],
    node_run_id: null,
    attempt: null,
    message: `${kind} ${seq}`,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("parseSseEvents reads data frames and ignores malformed ones", () => {
  const frame =
    `data: ${JSON.stringify(event(0, "run_queued"))}\n\n` +
    `data: ${JSON.stringify(event(1, "node_started"))}\n\n`;
  const parsed = parseSseEvents(frame);
  expect(parsed.map((item) => item.seq)).toEqual([0, 1]);
  expect(parseSseEvents(": comment\n\n")).toEqual([]);
  expect(parseSseEvents('data: {"seq": "bad"}\n\n')).toEqual([]);
});

test("mergeEvents keeps a gapless sequence and drops duplicate seqs", () => {
  const current = [event(0, "run_queued"), event(1, "node_started")];
  const merged = mergeEvents(current, [
    event(2, "node_succeeded"),
    event(1, "node_started"),
  ]);
  expect(merged.map((item) => item.seq)).toEqual([0, 1, 2]);
  const outOfOrder = mergeEvents(
    [],
    [event(3, "run_succeeded"), event(2, "node_failed")],
  );
  expect(outOfOrder.map((item) => item.seq)).toEqual([2, 3]);
});

test("startRun maps backend detail into an actionable error", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: false,
      status: 400,
      json: async () => ({ detail: "Cannot start an invalid workflow: cycle" }),
    })),
  );
  await expect(startRun("/p", {}, "token")).rejects.toThrow(
    "Cannot start an invalid workflow: cycle",
  );
});

test("startRun requires a session token", async () => {
  await expect(startRun("/p", {}, "")).rejects.toThrow("session token");
});

test("cancelRun and reviewRun post the expected payloads", async () => {
  const bodies: Array<Record<string, unknown>> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init?: RequestInit) => {
      bodies.push(JSON.parse(String(init?.body)));
      return { ok: true, json: async () => ({ run: {} }) };
    }),
  );
  await cancelRun("/p", "run-1", "token");
  await reviewRun("/p", "run-1", "gate", "approved", "looks good", "token");
  expect(bodies[0]).toEqual({ path: "/p", run_id: "run-1" });
  expect(bodies[1]).toEqual({
    path: "/p",
    run_id: "run-1",
    node_run_id: "gate",
    decision: "approved",
    note: "looks good",
  });
});

test("subscribeRunEvents streams frames and resumes after the last seq", async () => {
  const encoder = new TextEncoder();
  const frames = [
    `data: ${JSON.stringify(event(0, "run_queued"))}\n\n`,
    `data: ${JSON.stringify(event(1, "node_started"))}\n\n`,
  ];
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      body: new ReadableStream<Uint8Array>({
        start(controller) {
          for (const frame of frames) controller.enqueue(encoder.encode(frame));
          controller.close();
        },
      }),
    })),
  );
  const seen: RunEvent[] = [];
  const lastSeq = await subscribeRunEvents(
    "/p",
    "run-1",
    -1,
    "token",
    (events) => seen.push(...events),
  );
  expect(lastSeq).toBe(1);
  expect(seen.map((item) => item.seq)).toEqual([0, 1]);
});

test("subscribeRunEvents skips events at or before the resume point", async () => {
  const encoder = new TextEncoder();
  const frames = [
    `data: ${JSON.stringify(event(0, "run_queued"))}\n\n`,
    `data: ${JSON.stringify(event(1, "node_started"))}\n\n`,
    `data: ${JSON.stringify(event(2, "node_succeeded"))}\n\n`,
  ];
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      body: new ReadableStream<Uint8Array>({
        start(controller) {
          for (const frame of frames) controller.enqueue(encoder.encode(frame));
          controller.close();
        },
      }),
    })),
  );
  const seen: RunEvent[] = [];
  await subscribeRunEvents("/p", "run-1", 1, "token", (events) =>
    seen.push(...events),
  );
  expect(seen.map((item) => item.seq)).toEqual([2]);
});

test("subscribeRunEvents passes the abort signal and cancels the reader", async () => {
  const encoder = new TextEncoder();
  const seenSignals: Array<AbortSignal | undefined> = [];
  let cancelCalls = 0;
  let releaseCalls = 0;
  const frame = `data: ${JSON.stringify(event(0, "run_queued"))}\n\n`;
  const chunks = [encoder.encode(frame)];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init?: RequestInit) => {
      seenSignals.push(init?.signal as AbortSignal | undefined);
      return {
        ok: true,
        body: {
          getReader: () => ({
            read: async () => {
              const value = chunks.shift();
              return value === undefined
                ? { value: undefined, done: true as const }
                : { value, done: false as const };
            },
            cancel: async () => {
              cancelCalls += 1;
            },
            releaseLock: () => {
              releaseCalls += 1;
            },
          }),
        },
      };
    }),
  );
  const controller = new AbortController();
  const seen: RunEvent[] = [];
  const lastSeq = await subscribeRunEvents(
    "/p",
    "run-1",
    -1,
    "token",
    (events) => seen.push(...events),
    controller.signal,
  );
  expect(seenSignals[0]).toBe(controller.signal);
  expect(lastSeq).toBe(0);
  expect(seen.map((item) => item.seq)).toEqual([0]);
  expect(cancelCalls).toBe(1);
  expect(releaseCalls).toBe(1);
});

test("subscribeRunEvents maps a rejected stream into an actionable error", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: false,
      status: 404,
      json: async () => ({ detail: "Run 'missing' was not found." }),
    })),
  );
  await expect(
    subscribeRunEvents("/p", "missing", -1, "token", () => {}),
  ).rejects.toThrow("Run 'missing' was not found.");
});

test("openArtifact returns the blob with the served filename", async () => {
  const bytes = new TextEncoder().encode("artifact-bytes");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      headers: new Headers({
        "Content-Type": "text/plain",
        "Content-Disposition": 'attachment; filename="output.txt"',
      }),
      blob: async () => new Blob([bytes], { type: "text/plain" }),
    })),
  );
  const opened = await openArtifact(
    "/p",
    "run-1",
    "brainlearn-v1:artifact:" + "e".repeat(64),
    "token",
  );
  expect(opened.filename).toBe("output.txt");
  expect(opened.mediaType).toContain("text/plain");
  expect(opened.blob.size).toBe(bytes.length);
});

test("openArtifact maps a tampered file into an actionable error", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: false,
      status: 409,
      json: async () => ({
        detail: "Artifact no longer matches its recorded hash.",
      }),
    })),
  );
  await expect(
    openArtifact("/p", "run-1", "artifact-x", "token"),
  ).rejects.toThrow("Artifact no longer matches its recorded hash.");
});

export type { RunRecord, RunResponse };
