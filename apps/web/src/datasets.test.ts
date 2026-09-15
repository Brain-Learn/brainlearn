import { afterEach, expect, test, vi } from "vitest";

import {
  cancelDownload,
  checksumCoverage,
  formatBytes,
  getDownload,
  listDatasets,
  listDownloads,
  resolveDataset,
  resumeDownload,
  startDownload,
} from "./datasets";
import type { CatalogEntry, DownloadRecord } from "./types";

afterEach(() => {
  vi.unstubAllGlobals();
});

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

test("listDatasets builds a bounded query and validates the page shape", async () => {
  const seen: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request) => {
      seen.push(String(input));
      return {
        ok: true,
        json: async () => ({
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
          next_cursor: "mock-cursor-1",
          has_more: true,
        }),
      };
    }),
  );
  const page = await listDatasets({
    path: "/tmp/project",
    token: "token",
    provider: "mock-archive",
    query: "alpha",
    modality: "EEG",
    first: 5,
  });
  expect(page.items).toHaveLength(1);
  expect(page.has_more).toBe(true);
  const url = seen[0];
  expect(url).toContain("/api/datasets?");
  expect(url).toContain("query=alpha");
  expect(url).toContain("modality=EEG");
  expect(url).toContain("first=5");
  expect(url).toContain(`path=${encodeURIComponent("/tmp/project")}`);
});

test("listDatasets requires project context and maps backend detail", async () => {
  await expect(listDatasets({ path: "", token: "token" })).rejects.toThrow(
    "Open or create a project",
  );
  await expect(
    listDatasets({ path: "/tmp/project", token: "" }),
  ).rejects.toThrow("session token");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: false,
      status: 504,
      json: async () => ({
        detail:
          "The dataset source timed out. Retry the request; no local state was changed.",
      }),
    })),
  );
  await expect(
    listDatasets({ path: "/tmp/project", token: "token" }),
  ).rejects.toThrow("The dataset source timed out.");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: false, status: 502, json: async () => ({}) })),
  );
  await expect(
    listDatasets({ path: "/tmp/project", token: "token" }),
  ).rejects.toThrow("HTTP 502");
});

test("listDatasets rejects a misshapen page", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, json: async () => ({ items: [] }) })),
  );
  await expect(
    listDatasets({ path: "/tmp/project", token: "token" }),
  ).rejects.toThrow("does not match contract");
});

test("resolveDataset encodes identifiers and validates the entry", async () => {
  const seen: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request) => {
      seen.push(String(input));
      return { ok: true, json: async () => entryFixture() };
    }),
  );
  const entry = await resolveDataset("mock-archive", "zz10a", "2026-09-01", {
    path: "/tmp/project",
    token: "token",
  });
  expect(entry.dataset_id).toBe("zz10a");
  expect(seen[0]).toContain("/api/datasets/mock-archive/zz10a/2026-09-01?");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: false,
      status: 404,
      json: async () => ({
        detail: "Unknown dataset snapshot zz99:2026-09-01.",
      }),
    })),
  );
  await expect(
    resolveDataset("mock-archive", "zz99", "2026-09-01", {
      path: "/tmp/project",
      token: "token",
    }),
  ).rejects.toThrow("Unknown dataset snapshot");
});

test("formatBytes renders short disk sizes and guards bad input", () => {
  expect(formatBytes(0)).toBe("0 B");
  expect(formatBytes(512)).toBe("512 B");
  expect(formatBytes(2048)).toBe("2 KB");
  expect(formatBytes(5 * 1024 * 1024)).toBe("5 MB");
  expect(formatBytes(6_112_012_191)).toBe("5.7 GB");
  expect(formatBytes(-1)).toBe("unknown size");
  expect(formatBytes(Number.NaN)).toBe("unknown size");
});

test("checksumCoverage separates verified files from pending ones", () => {
  const entry = entryFixture();
  expect(checksumCoverage(entry)).toEqual({ total: 1, verified: 0 });
  expect(
    checksumCoverage({
      ...entry,
      expected_files: [
        { path: "a", byte_size: 1, sha256: "b".repeat(64) },
        { path: "b", byte_size: 1, sha256: null },
      ],
    }),
  ).toEqual({ total: 2, verified: 1 });
});

function downloadFixture(): DownloadRecord {
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
  };
}

test("download actions post shaped bodies and validate records", async () => {
  const seen: Array<{ url: string; method?: string; body: unknown }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      seen.push({
        url: String(input),
        method: init?.method,
        body: init?.body ? JSON.parse(String(init.body)) : null,
      });
      if (String(input) === "/api/datasets/downloads?path=%2Ftmp%2Fproject") {
        return { ok: true, json: async () => [downloadFixture()] };
      }
      return { ok: true, json: async () => downloadFixture() };
    }),
  );
  const context = { path: "/tmp/project", token: "token" };
  const started = await startDownload(
    "mock-archive",
    "zz10a",
    "2026-09-01",
    context,
  );
  expect(started.download_id).toBe("dl-0123456789ab");
  expect(seen[0]).toMatchObject({
    url: "/api/datasets/downloads",
    method: "POST",
    body: {
      path: "/tmp/project",
      provider: "mock-archive",
      dataset_id: "zz10a",
      snapshot: "2026-09-01",
    },
  });
  const listed = await listDownloads(context);
  expect(listed).toHaveLength(1);
  const opened = await getDownload("dl-0123456789ab", context);
  expect(opened.state).toBe("downloading");
  await cancelDownload("dl-0123456789ab", context);
  await resumeDownload("dl-0123456789ab", context);
  expect(
    seen
      .slice(2)
      .map((call) => `${call.method ?? "GET"} ${call.url}`)
      .join(" | "),
  ).toBe(
    "GET /api/datasets/downloads/dl-0123456789ab?path=%2Ftmp%2Fproject | " +
      "POST /api/datasets/downloads/dl-0123456789ab/cancel | " +
      "POST /api/datasets/downloads/dl-0123456789ab/resume",
  );
  await expect(
    startDownload("mock-archive", "zz10a", "2026-09-01", {
      path: "",
      token: "token",
    }),
  ).rejects.toThrow("Open or create a project");
});

test("download client maps conflict detail and rejects misshapen records", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: false,
      status: 409,
      json: async () => ({
        detail: "Dataset zz10a:2026-09-01 already downloaded.",
      }),
    })),
  );
  await expect(
    startDownload("mock-archive", "zz10a", "2026-09-01", {
      path: "/tmp/project",
      token: "token",
    }),
  ).rejects.toThrow("already downloaded");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      json: async () => ({ state: "downloading" }),
    })),
  );
  await expect(
    getDownload("dl-0123456789ab", { path: "/tmp/project", token: "token" }),
  ).rejects.toThrow("does not match contract");
});
