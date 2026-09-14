import type { RunEvent, RunRecord, RunResponse } from "./types";

function authHeaders(token: string): Record<string, string> {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  };
}

function requireToken(token: string): void {
  if (!token) {
    throw new Error(
      "A session token is required. Copy it from the BrainLearn service terminal.",
    );
  }
}

async function readError(response: Response, fallback: string): Promise<Error> {
  try {
    const body: unknown = await response.json();
    if (
      typeof body === "object" &&
      body !== null &&
      "detail" in body &&
      typeof (body as Record<string, unknown>).detail === "string"
    ) {
      return new Error((body as Record<string, unknown>).detail as string);
    }
  } catch {
    // Fall through to the generic message.
  }
  return new Error(`${fallback} (HTTP ${response.status})`);
}

async function postRun(
  url: string,
  body: unknown,
  token: string,
  fallback: string,
): Promise<RunResponse> {
  requireToken(token);
  const response = await fetch(url, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
  if (!response.ok) throw await readError(response, fallback);
  return (await response.json()) as RunResponse;
}

function isRunEvent(value: unknown): value is RunEvent {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    record.schema_version === "1.0" &&
    typeof record.seq === "number" &&
    typeof record.at === "string" &&
    typeof record.kind === "string" &&
    typeof record.message === "string"
  );
}

/** Parse one `text/event-stream` chunk into ordered, validated events. */
export function parseSseEvents(chunk: string): RunEvent[] {
  const events: RunEvent[] = [];
  for (const block of chunk.split(/\r?\n\r?\n/)) {
    const lines = block.split(/\r?\n/);
    const data: string[] = [];
    for (const line of lines) {
      if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (data.length === 0) continue;
    try {
      const parsed: unknown = JSON.parse(data.join("\n"));
      if (isRunEvent(parsed)) events.push(parsed);
    } catch {
      // Ignore malformed frames; a reconnect re-snapshots from the record.
    }
  }
  return events;
}

/** Merge newly streamed events into a gapless sequence, dropping duplicates. */
export function mergeEvents(
  current: RunEvent[],
  incoming: RunEvent[],
): RunEvent[] {
  const bySeq = new Map<number, RunEvent>();
  for (const event of current) bySeq.set(event.seq, event);
  for (const event of incoming) bySeq.set(event.seq, event);
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq);
}

export async function startRun(
  path: string,
  workflow: unknown,
  token: string,
  seed = 0,
): Promise<RunResponse> {
  return postRun(
    "/api/runs/start",
    { path, workflow, seed },
    token,
    "Run request failed",
  );
}

export async function resumeRun(
  path: string,
  runId: string,
  token: string,
): Promise<RunResponse> {
  return postRun(
    "/api/runs/start",
    { path, run_id: runId },
    token,
    "Resume request failed",
  );
}

export async function openRun(
  path: string,
  runId: string,
  token: string,
  signal?: AbortSignal,
): Promise<RunResponse> {
  requireToken(token);
  const response = await fetch("/api/runs/open", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ path, run_id: runId }),
    signal,
  });
  if (!response.ok) throw await readError(response, "Open run failed");
  return (await response.json()) as RunResponse;
}

export interface OpenArtifact {
  blob: Blob;
  filename: string;
  mediaType: string;
}

/**
 * Download one artifact recorded on a persisted run. The backend rechecks
 * project authorization, containment, symlinks, size, and SHA-256 before
 * serving; only the recorded bytes ever leave the service.
 */
export async function openArtifact(
  path: string,
  runId: string,
  artifactId: string,
  token: string,
): Promise<OpenArtifact> {
  requireToken(token);
  const response = await fetch("/api/artifacts/open", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ path, run_id: runId, artifact_id: artifactId }),
  });
  if (!response.ok) throw await readError(response, "Open artifact failed");
  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const filename =
    /filename="([^"]+)"/.exec(disposition)?.[1] ?? `${artifactId}.bin`;
  return {
    blob,
    filename,
    mediaType:
      response.headers.get("Content-Type") ?? "application/octet-stream",
  };
}

export async function listRuns(
  path: string,
  token: string,
): Promise<RunResponse[]> {
  requireToken(token);
  const response = await fetch(
    `/api/runs/list?path=${encodeURIComponent(path)}`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  if (!response.ok) throw await readError(response, "Unable to list runs");
  return (await response.json()) as RunResponse[];
}

export async function recoverRuns(
  path: string,
  token: string,
): Promise<RunResponse[]> {
  requireToken(token);
  const response = await fetch("/api/runs/recover", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ path }),
  });
  if (!response.ok) throw await readError(response, "Recovery failed");
  return (await response.json()) as RunResponse[];
}

export async function cancelRun(
  path: string,
  runId: string,
  token: string,
): Promise<RunResponse> {
  return postRun(
    "/api/runs/cancel",
    { path, run_id: runId },
    token,
    "Cancel failed",
  );
}

export async function reviewRun(
  path: string,
  runId: string,
  nodeRunId: string,
  decision: "approved" | "rejected",
  note: string,
  token: string,
): Promise<RunResponse> {
  return postRun(
    "/api/runs/review",
    { path, run_id: runId, node_run_id: nodeRunId, decision, note },
    token,
    "Review decision failed",
  );
}

export interface RunEventStream {
  onEvents: (events: RunEvent[]) => void;
  signal?: AbortSignal;
}

/**
 * Subscribe to a run's event stream starting after ``after`` (last seen seq).
 *
 * Resolves with the highest sequence observed so the caller can resume the
 * stream from that point after a reconnect. The backend terminates the stream
 * when the run reaches a terminal state; otherwise it keeps yielding.
 */
export async function subscribeRunEvents(
  path: string,
  runId: string,
  after: number,
  token: string,
  onEvents: (events: RunEvent[]) => void,
  signal?: AbortSignal,
  onOpen?: () => void,
): Promise<number> {
  requireToken(token);
  const url = `/api/runs/events?path=${encodeURIComponent(path)}&run_id=${encodeURIComponent(runId)}&after=${after}`;
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
    signal,
  });
  if (!response.ok)
    throw await readError(response, "Unable to open event stream");
  if (!response.body) return after;
  onOpen?.();

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let lastSeq = after;
  try {
    while (true) {
      if (signal?.aborted) break;
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const events = parseSseEvents(frame);
        const fresh = events.filter((event) => event.seq > after);
        if (fresh.length) {
          onEvents(fresh);
          for (const event of fresh) {
            if (event.seq > lastSeq) lastSeq = event.seq;
          }
        }
        boundary = buffer.indexOf("\n\n");
      }
    }
  } finally {
    try {
      await reader.cancel();
    } catch {
      // The stream already closed; releasing is enough.
    }
    reader.releaseLock();
  }
  return lastSeq;
}

export type { RunRecord };
