import "@testing-library/jest-dom/vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { DatasetLibrary } from "./DatasetLibrary";
import type {
  CatalogEntry,
  DatasetListResponse,
  DownloadRecord,
} from "./types";

beforeEach(() => {
  // Auto-advancing fake timers: polling intervals stay controllable via
  // advanceTimersByTimeAsync while findBy/waitFor keep working.
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function listPage(): DatasetListResponse {
  return {
    provider: "mock-archive",
    items: [
      {
        provider: "mock-archive",
        dataset_id: "zz10a",
        title: "Alpha EEG",
        public: true,
        latest_snapshot: "2026-09-01",
      },
    ],
    next_cursor: null,
    has_more: false,
  };
}

function entryFixture(): CatalogEntry {
  return {
    schema_version: "1.0",
    catalog_identity: `brainlearn-v1:dataset:${"a".repeat(64)}`,
    provider: "mock-archive",
    dataset_id: "zz10a",
    snapshot: "2026-09-01",
    title: "Alpha EEG",
    modality: "EEG",
    task: "rest",
    participants: 2,
    formats: ["BIDS"],
    approximate_total_bytes: 512,
    expected_total_bytes: 512,
    expected_files: [
      { path: "dataset_description.json", byte_size: 512, sha256: null },
    ],
    access: "public",
    license_name: "Mock public license",
    license_spdx: null,
    reuse_statement: "Mock reuse statement.",
    citations: [{ title: "Mock citation.", doi: null, url: null }],
    landing_page: "https://example.invalid/datasets/zz10a",
    compatible_templates: [],
    curator: "",
    review_status: "pending",
    reviewed_at: null,
    limitations: "Mock entry for library tests.",
  };
}

function transferFixture(
  overrides: Partial<DownloadRecord> = {},
): DownloadRecord {
  return {
    schema_version: "1.0",
    download_id: "dl-0123456789ab",
    provider: "mock-archive",
    dataset_id: "zz10a",
    snapshot: "2026-09-01",
    catalog_identity: `brainlearn-v1:dataset:${"a".repeat(64)}`,
    catalog_entry: entryFixture(),
    expected_total_bytes: 512,
    files: [
      {
        path: "dataset_description.json",
        byte_size: 512,
        sha256: null,
        bytes_completed: 0,
        verified: false,
      },
    ],
    bytes_completed: 0,
    state: "downloading",
    attempt: 1,
    max_attempts: 3,
    created_at: "2026-09-14T00:00:00+00:00",
    updated_at: "2026-09-14T00:00:00+00:00",
    failure: null,
    lock_identity: null,
    ...overrides,
  };
}

interface Script {
  downloadsList?: DownloadRecord[];
  statusQueue?: DownloadRecord[];
  startResponse?: DownloadRecord | { status: number; detail: string };
  cancelResponse?: DownloadRecord;
  resumeResponse?: DownloadRecord;
}

function stubDownloads(script: Script) {
  const calls: string[] = [];
  const posts: Array<{ url: string; body: unknown }> = [];
  const statusQueue = [...(script.statusQueue ?? [])];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url.startsWith("/api/datasets?")) {
        return { ok: true, json: async () => listPage() };
      }
      if (url.startsWith("/api/datasets/mock-archive/")) {
        return { ok: true, json: async () => entryFixture() };
      }
      if (url.startsWith("/api/datasets/downloads?")) {
        return { ok: true, json: async () => script.downloadsList ?? [] };
      }
      if (url === "/api/datasets/downloads" && init?.method === "POST") {
        posts.push({ url, body: JSON.parse(String(init.body)) });
        const response = script.startResponse ?? transferFixture();
        if ("status" in response) {
          return {
            ok: false,
            status: response.status,
            json: async () => ({ detail: response.detail }),
          };
        }
        return { ok: true, json: async () => response };
      }
      const statusMatch = /^\/api\/datasets\/downloads\/([^/?]+)\?/.exec(url);
      if (statusMatch && (!init?.method || init.method === "GET")) {
        const next =
          statusQueue.shift() ?? transferFixture({ state: "succeeded" });
        return { ok: true, json: async () => next };
      }
      const actionMatch =
        /^\/api\/datasets\/downloads\/([^/]+)\/(cancel|resume)$/.exec(url);
      if (actionMatch) {
        posts.push({ url, body: JSON.parse(String(init?.body)) });
        const record =
          actionMatch[2] === "cancel"
            ? (script.cancelResponse ?? transferFixture({ state: "cancelled" }))
            : (script.resumeResponse ?? transferFixture({ state: "queued" }));
        return { ok: true, json: async () => record };
      }
      throw new Error(`Unexpected request: ${init?.method} ${url}`);
    }),
  );
  return { calls, posts };
}

async function openDetails() {
  render(<DatasetLibrary projectPath="/tmp/project" token="token" />);
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  fireEvent.click(await screen.findByTestId("dataset-select-zz10a"));
  await screen.findByTestId("dataset-details");
}

test("starts a download and shows verified success only at the end", async () => {
  const { calls, posts } = stubDownloads({
    statusQueue: [
      transferFixture({ state: "downloading", bytes_completed: 256 }),
      transferFixture({
        state: "succeeded",
        bytes_completed: 512,
        files: [
          {
            path: "dataset_description.json",
            byte_size: 512,
            sha256: null,
            bytes_completed: 512,
            verified: true,
          },
        ],
        lock_identity: `brainlearn-v1:dataset:${"b".repeat(64)}`,
      }),
    ],
  });
  await openDetails();
  fireEvent.click(screen.getByTestId("dataset-download-start"));
  expect(await screen.findByRole("progressbar")).toBeInTheDocument();
  expect(screen.queryByText(/Downloaded and verified/)).not.toBeInTheDocument();

  await vi.advanceTimersByTimeAsync(1000);
  expect(
    await screen.findByText(/Downloading 256 B of 512 B/),
  ).toBeInTheDocument();
  await vi.advanceTimersByTimeAsync(1000);
  expect(
    await screen.findByText(/Downloaded and verified: 512 B in 1 file/),
  ).toBeInTheDocument();
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();

  const starts = posts.filter((post) => post.url === "/api/datasets/downloads");
  expect(starts).toHaveLength(1);
  expect(starts[0].body).toMatchObject({
    path: "/tmp/project",
    provider: "mock-archive",
    dataset_id: "zz10a",
    snapshot: "2026-09-01",
  });
  expect(
    calls.some((call) => call.startsWith("GET /api/datasets/downloads/dl-")),
  ).toBe(true);
});

test("a duplicate start is blocked and explained", async () => {
  stubDownloads({
    startResponse: {
      status: 409,
      detail:
        "Dataset zz10a:2026-09-01 already has downloading transfer dl-0123456789ab.",
    },
  });
  await openDetails();
  fireEvent.click(screen.getByTestId("dataset-download-start"));
  fireEvent.click(screen.getByTestId("dataset-download-start"));
  expect(
    await screen.findByText(/already has downloading transfer/),
  ).toBeInTheDocument();
});

test("cancels an active transfer and resumes it", async () => {
  stubDownloads({
    statusQueue: [
      transferFixture({ state: "downloading", bytes_completed: 256 }),
      transferFixture({
        state: "succeeded",
        bytes_completed: 512,
        lock_identity: `brainlearn-v1:dataset:${"b".repeat(64)}`,
      }),
    ],
    cancelResponse: transferFixture({
      state: "cancelled",
      bytes_completed: 256,
    }),
    resumeResponse: transferFixture({ state: "queued", bytes_completed: 256 }),
  });
  await openDetails();
  fireEvent.click(screen.getByTestId("dataset-download-start"));
  await screen.findByRole("progressbar");

  await vi.advanceTimersByTimeAsync(1000);
  fireEvent.click(await screen.findByTestId("dataset-download-cancel"));
  expect(
    await screen.findByText(/Cancelled with 256 B kept/),
  ).toBeInTheDocument();

  fireEvent.click(screen.getByTestId("dataset-download-resume"));
  expect(await screen.findByText("Queued for download.")).toBeInTheDocument();
  await vi.advanceTimersByTimeAsync(1000);
  expect(
    await screen.findByText(/Downloaded and verified: 512 B in 1 file/),
  ).toBeInTheDocument();
});

test("adopts an existing transfer instead of offering a fresh start", async () => {
  stubDownloads({
    downloadsList: [transferFixture({ state: "paused", bytes_completed: 128 })],
    statusQueue: [
      transferFixture({
        state: "succeeded",
        bytes_completed: 512,
        lock_identity: `brainlearn-v1:dataset:${"b".repeat(64)}`,
      }),
    ],
  });
  await openDetails();
  expect(await screen.findByText(/Paused/)).toBeInTheDocument();
  expect(
    screen.queryByTestId("dataset-download-start"),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByTestId("dataset-download-resume"));
  expect(await screen.findByText("Queued for download.")).toBeInTheDocument();
  await vi.advanceTimersByTimeAsync(1000);
  expect(
    await screen.findByText(/Downloaded and verified: 512 B in 1 file/),
  ).toBeInTheDocument();
});

test("failure shows no success and offers a retry", async () => {
  stubDownloads({
    downloadsList: [
      transferFixture({
        state: "failed",
        failure: {
          code: "checksum_mismatch",
          message: "Checksum mismatch, retry.",
        },
      }),
    ],
    startResponse: transferFixture({ state: "queued" }),
    statusQueue: [
      transferFixture({
        state: "succeeded",
        bytes_completed: 512,
        lock_identity: `brainlearn-v1:dataset:${"b".repeat(64)}`,
      }),
    ],
  });
  await openDetails();
  expect(await screen.findByText(/Download failed/)).toBeInTheDocument();
  expect(screen.queryByText(/Downloaded and verified/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByTestId("dataset-download-start"));
  expect(await screen.findByRole("progressbar")).toBeInTheDocument();
  await vi.advanceTimersByTimeAsync(1000);
  expect(
    await screen.findByText(/Downloaded and verified: 512 B in 1 file/),
  ).toBeInTheDocument();
});

test("leaving the view stops progress polling", async () => {
  const { calls } = stubDownloads({
    statusQueue: [
      transferFixture({ state: "downloading", bytes_completed: 10 }),
    ],
  });
  const { rerender } = render(
    <DatasetLibrary projectPath="/tmp/project" token="token" />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  fireEvent.click(await screen.findByTestId("dataset-select-zz10a"));
  await screen.findByTestId("dataset-details");
  fireEvent.click(screen.getByTestId("dataset-download-start"));
  await screen.findByRole("progressbar");
  await vi.advanceTimersByTimeAsync(3000);
  const polls = calls.filter((call) =>
    call.startsWith("GET /api/datasets/downloads/dl-"),
  ).length;
  expect(polls).toBeGreaterThan(0);

  fireEvent.click(screen.getByTestId("dataset-back"));
  await screen.findByTestId("dataset-select-zz10a");
  rerender(<DatasetLibrary projectPath="/tmp/other" token="token" />);
  await vi.advanceTimersByTimeAsync(5000);
  expect(
    calls.filter((call) => call.startsWith("GET /api/datasets/downloads/dl-")),
  ).toHaveLength(polls);
});

function stubDeferredRace() {
  const calls: string[] = [];
  let resolveList!: (response: unknown) => void;
  let resolveStart!: (response: unknown) => void;
  const listGate = new Promise<unknown>((resolve) => {
    resolveList = resolve;
  });
  const startGate = new Promise<unknown>((resolve) => {
    resolveStart = resolve;
  });
  const ok = (body: unknown) => ({ ok: true, json: async () => body });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url.startsWith("/api/datasets?")) return ok(listPage());
      if (url.startsWith("/api/datasets/mock-archive/"))
        return ok(entryFixture());
      if (url.startsWith("/api/datasets/downloads?")) return listGate;
      if (url === "/api/datasets/downloads" && init?.method === "POST") {
        return startGate.then(() =>
          ok(transferFixture({ state: "downloading", bytes_completed: 0 })),
        );
      }
      throw new Error(`Unexpected request: ${init?.method} ${url}`);
    }),
  );
  return {
    calls,
    releaseList: (records: unknown) =>
      act(async () => {
        resolveList(ok(records));
        await listGate;
      }),
    releaseStart: () =>
      act(async () => {
        resolveStart(undefined);
        await startGate;
      }),
  };
}

async function openDetailsWithHeldAdoption() {
  render(<DatasetLibrary projectPath="/tmp/project" token="token" />);
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  fireEvent.click(await screen.findByTestId("dataset-select-zz10a"));
  await screen.findByTestId("dataset-details");
  // The Download action is available while transfer adoption is held.
  fireEvent.click(screen.getByTestId("dataset-download-start"));
}

function expectStartedTransfer() {
  // Installed transfer with progress, and no stuck pending state on
  // unrelated controls.
  expect(screen.getByRole("progressbar")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Search" })).toBeEnabled();
}

function expectSingleStart(calls: string[]) {
  const starts = calls.filter(
    (call) => call === "POST /api/datasets/downloads",
  );
  expect(starts).toHaveLength(1);
}

test("a late adoption never supersedes a user-started download", async () => {
  const { calls, releaseList, releaseStart } = stubDeferredRace();
  await openDetailsWithHeldAdoption();
  // The start resolves first; the held adoption resolves afterwards with a
  // conflicting paused record it must not install over the started transfer.
  await releaseStart();
  expectStartedTransfer();
  await releaseList([
    transferFixture({ download_id: "dl-stale000000", state: "paused" }),
  ]);
  expectStartedTransfer();
  expect(
    screen.queryByTestId("dataset-download-resume"),
  ).not.toBeInTheDocument();
  expectSingleStart(calls);
});

test("an early adoption does not block a user-started download", async () => {
  const { calls, releaseList, releaseStart } = stubDeferredRace();
  await openDetailsWithHeldAdoption();
  // The held adoption resolves first (finding nothing), then the start.
  await releaseList([]);
  await releaseStart();
  expectStartedTransfer();
  expectSingleStart(calls);
});

test("switching projects during a held adoption issues no crossover", async () => {
  const { calls, releaseList } = stubDeferredRace();
  const { rerender } = render(
    <DatasetLibrary projectPath="/tmp/project" token="token" />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  fireEvent.click(await screen.findByTestId("dataset-select-zz10a"));
  await screen.findByTestId("dataset-details");
  rerender(<DatasetLibrary projectPath="/tmp/other" token="token" />);
  // The stale adoption resolves after the context changed: it must install
  // nothing and issue no further transfer requests.
  await releaseList([transferFixture({ state: "downloading" })]);
  expect(screen.queryByTestId("dataset-details")).not.toBeInTheDocument();
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  expect(
    calls.filter((call) => call.includes("/api/datasets/downloads")),
  ).toHaveLength(1);
});
