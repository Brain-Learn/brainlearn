import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { DatasetLibrary } from "./DatasetLibrary";
import type { CatalogEntry, DatasetListResponse } from "./types";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function listPage(
  overrides: Partial<DatasetListResponse> = {},
): DatasetListResponse {
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
      {
        provider: "mock-archive",
        dataset_id: "zz12b",
        title: "Beta EEG",
        public: true,
        latest_snapshot: "2026-09-02",
      },
    ],
    next_cursor: null,
    has_more: false,
    ...overrides,
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
    approximate_total_bytes: 1048576,
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

function stubLibrary(options: {
  pages?: DatasetListResponse[];
  entry?: CatalogEntry;
  error?: string;
}) {
  const calls: string[] = [];
  let pageIndex = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      calls.push(url);
      if (url.startsWith("/api/datasets/mock-archive/zz")) {
        if (options.error) {
          return {
            ok: false,
            status: 500,
            json: async () => ({ detail: options.error }),
          };
        }
        return { ok: true, json: async () => options.entry ?? entryFixture() };
      }
      if (url.startsWith("/api/datasets?")) {
        const page =
          options.pages?.[Math.min(pageIndex, options.pages.length - 1)];
        pageIndex += 1;
        return { ok: true, json: async () => page ?? listPage() };
      }
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
  return calls;
}

function search() {
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
}

test("shows guidance and never fetches without project context", () => {
  const calls = stubLibrary({});
  render(<DatasetLibrary projectPath="" token="" />);
  expect(
    screen.getByText(/Open or create a project and enter the session token/),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Search" })).toBeDisabled();
  expect(calls).toEqual([]);
});

test("makes the no-download boundary explicit with no download affordance", () => {
  stubLibrary({});
  const { container } = render(
    <DatasetLibrary projectPath="/tmp/project" token="token" />,
  );
  expect(
    screen.getByText(
      /browsing never downloads, and no download action exists yet/,
    ),
  ).toBeInTheDocument();
  const section = container.querySelector(
    'section[aria-label="Dataset library"]',
  );
  expect(section).not.toBeNull();
  // The boundary notice names downloads only to rule them out; what matters
  // is that no control offers one.
  expect(
    screen.queryByRole("button", { name: /download|import/i }),
  ).not.toBeInTheDocument();
  expect(section?.querySelector("a[href]")).toBeNull();
});

test("searches, paginates, and reports an empty result set", async () => {
  const calls = stubLibrary({
    pages: [
      listPage({ next_cursor: "mock-cursor-1", has_more: true }),
      listPage({
        items: [
          {
            provider: "mock-archive",
            dataset_id: "zz13c",
            title: "Gamma EEG",
            public: true,
            latest_snapshot: "2026-09-03",
          },
        ],
        next_cursor: null,
        has_more: false,
      }),
    ],
  });
  render(<DatasetLibrary projectPath="/tmp/project" token="token" />);
  fireEvent.change(screen.getByLabelText("Search datasets"), {
    target: { value: "EEG" },
  });
  search();
  expect(await screen.findByTestId("dataset-select-zz10a")).toBeInTheDocument();
  expect(screen.getByTestId("dataset-select-zz12b")).toBeInTheDocument();
  expect(calls).toHaveLength(1);
  expect(calls[0]).toContain("query=EEG");

  fireEvent.click(screen.getByTestId("dataset-load-more"));
  expect(await screen.findByTestId("dataset-select-zz13c")).toBeInTheDocument();
  expect(screen.getByTestId("dataset-select-zz10a")).toBeInTheDocument();
  expect(calls).toHaveLength(2);
  expect(calls[1]).toContain("after=mock-cursor-1");
  expect(screen.queryByTestId("dataset-load-more")).not.toBeInTheDocument();
});

test("load more reuses the submitted criteria, not edited controls", async () => {
  const calls = stubLibrary({
    pages: [
      listPage({ next_cursor: "mock-cursor-1", has_more: true }),
      listPage({
        items: [
          {
            provider: "mock-archive",
            dataset_id: "zz13c",
            title: "Gamma EEG",
            public: true,
            latest_snapshot: "2026-09-03",
          },
        ],
        next_cursor: null,
        has_more: false,
      }),
    ],
  });
  render(<DatasetLibrary projectPath="/tmp/project" token="token" />);
  fireEvent.change(screen.getByLabelText("Search datasets"), {
    target: { value: "EEG" },
  });
  search();
  expect(await screen.findByTestId("dataset-select-zz10a")).toBeInTheDocument();

  // Edit both controls without submitting, then continue paginating.
  fireEvent.change(screen.getByLabelText("Search datasets"), {
    target: { value: "zzz-no-match" },
  });
  fireEvent.change(screen.getByLabelText("Filter by modality"), {
    target: { value: "MRI" },
  });
  fireEvent.click(screen.getByTestId("dataset-load-more"));
  expect(await screen.findByTestId("dataset-select-zz13c")).toBeInTheDocument();
  expect(calls).toHaveLength(2);
  expect(calls[1]).toContain("query=EEG");
  expect(calls[1]).not.toContain("zzz-no-match");
  expect(calls[1]).not.toContain("modality=");
  // The displayed set still belongs to the submitted search.
  expect(screen.getByTestId("dataset-select-zz10a")).toBeInTheDocument();
});

test("a fresh search replaces the previous result set", async () => {
  const calls = stubLibrary({
    pages: [
      listPage({ next_cursor: "mock-cursor-1", has_more: true }),
      listPage({
        items: [
          {
            provider: "mock-archive",
            dataset_id: "zz13c",
            title: "Gamma EEG",
            public: true,
            latest_snapshot: "2026-09-03",
          },
        ],
        next_cursor: null,
        has_more: false,
      }),
    ],
  });
  render(<DatasetLibrary projectPath="/tmp/project" token="token" />);
  search();
  expect(await screen.findByTestId("dataset-select-zz10a")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Search datasets"), {
    target: { value: "gamma" },
  });
  search();
  expect(await screen.findByTestId("dataset-select-zz13c")).toBeInTheDocument();
  expect(screen.queryByTestId("dataset-select-zz10a")).not.toBeInTheDocument();
  expect(calls).toHaveLength(2);
  expect(calls[1]).toContain("query=gamma");
});

test("reports empty, loading, and error states", async () => {
  stubLibrary({ pages: [listPage({ items: [] })] });
  render(<DatasetLibrary projectPath="/tmp/project" token="token" />);
  search();
  expect(
    await screen.findByText("No public datasets match this search."),
  ).toBeInTheDocument();

  cleanup();
  let release!: (value: unknown) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    ),
  );
  render(<DatasetLibrary projectPath="/tmp/project" token="token" />);
  search();
  expect(screen.getByRole("button", { name: "Searching…" })).toBeDisabled();
  release({ ok: true, json: async () => listPage({ items: [] }) });
  expect(
    await screen.findByText("No public datasets match this search."),
  ).toBeInTheDocument();

  cleanup();
  stubLibrary({});
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
  render(<DatasetLibrary projectPath="/tmp/project" token="token" />);
  search();
  expect(
    await screen.findByText(
      "The dataset source timed out. Retry the request; no local state was changed.",
    ),
  ).toBeInTheDocument();
});

test("selects a result and shows every required pre-download field", async () => {
  stubLibrary({});
  render(<DatasetLibrary projectPath="/tmp/project" token="token" />);
  search();
  fireEvent.click(await screen.findByTestId("dataset-select-zz10a"));
  const details = await screen.findByTestId("dataset-details");
  expect(details).toHaveTextContent("Alpha EEG");
  expect(details).toHaveTextContent("mock-archive");
  expect(details).toHaveTextContent("zz10a");
  expect(details).toHaveTextContent("2026-09-01");
  expect(details).toHaveTextContent("EEG");
  expect(details).toHaveTextContent("rest");
  expect(details).toHaveTextContent("1 MB");
  expect(details).toHaveTextContent("512 B");
  expect(details).toHaveTextContent("public");
  expect(details).toHaveTextContent("Mock public license");
  expect(details).toHaveTextContent("(no SPDX claimed)");
  expect(details).toHaveTextContent("Mock reuse statement.");
  expect(details).toHaveTextContent("Mock citation.");
  expect(details).toHaveTextContent("no DOI claimed");
  expect(details).toHaveTextContent("dataset_description.json");
  expect(details).toHaveTextContent("0 of 1 expected files carry checksums");
  expect(details).toHaveTextContent("checksum pending");
  expect(details).toHaveTextContent("None claimed");
  expect(details).toHaveTextContent("pending");
  expect(details).toHaveTextContent(
    "unverified metadata, not approved for analysis",
  );
  expect(details).toHaveTextContent("Mock entry for library tests.");
  expect(details).toHaveTextContent("https://example.invalid/datasets/zz10a");

  fireEvent.click(screen.getByTestId("dataset-back"));
  expect(await screen.findByTestId("dataset-select-zz10a")).toBeInTheDocument();
  expect(screen.queryByTestId("dataset-details")).not.toBeInTheDocument();
});

test("keyboard selection focuses the details heading", async () => {
  stubLibrary({});
  render(<DatasetLibrary projectPath="/tmp/project" token="token" />);
  search();
  const result = await screen.findByTestId("dataset-select-zz10a");
  result.focus();
  fireEvent.click(result);
  const details = await screen.findByTestId("dataset-details");
  const heading = details.querySelector("h4");
  expect(heading).not.toBeNull();
  await waitFor(() => expect(document.activeElement).toBe(heading));
});

test("back restores focus to the originating result", async () => {
  stubLibrary({});
  render(<DatasetLibrary projectPath="/tmp/project" token="token" />);
  search();
  const result = await screen.findByTestId("dataset-select-zz10a");
  result.focus();
  fireEvent.click(result);
  await screen.findByTestId("dataset-details");
  fireEvent.click(screen.getByTestId("dataset-back"));
  const restored = await screen.findByTestId("dataset-select-zz10a");
  await waitFor(() => expect(document.activeElement).toBe(restored));
});

test("escape from the focused heading returns focus to results", async () => {
  stubLibrary({});
  render(<DatasetLibrary projectPath="/tmp/project" token="token" />);
  search();
  const result = await screen.findByTestId("dataset-select-zz10a");
  result.focus();
  fireEvent.click(result);
  const details = await screen.findByTestId("dataset-details");
  // The component moves focus to the details heading on selection, exactly
  // where a keyboard user's Escape keypress originates.
  await waitFor(() =>
    expect(document.activeElement).toBe(details.querySelector("h4")),
  );
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });
  const restored = await screen.findByTestId("dataset-select-zz12b");
  expect(restored).toBeInTheDocument();
  await waitFor(() =>
    expect(document.activeElement).toBe(
      screen.getByTestId("dataset-select-zz10a"),
    ),
  );
});

test("details errors focus recovery without leaking internals", async () => {
  stubLibrary({ error: "Unknown dataset snapshot zz10a:2026-09-01." });
  render(<DatasetLibrary projectPath="/tmp/project" token="token" />);
  search();
  const result = await screen.findByTestId("dataset-select-zz10a");
  result.focus();
  fireEvent.click(result);
  expect(
    await screen.findByText("Unknown dataset snapshot zz10a:2026-09-01."),
  ).toBeInTheDocument();
  expect(screen.queryByText(/traceback|token|secret/i)).not.toBeInTheDocument();
  // The invoked button unmounted, so focus lands on the Back recovery
  // control; loading announcements never move focus.
  await waitFor(() =>
    expect(document.activeElement).toBe(screen.getByTestId("dataset-back")),
  );
});

test("switching projects clears results without fetching", async () => {
  const calls = stubLibrary({});
  const { rerender } = render(
    <DatasetLibrary projectPath="/tmp/project" token="token" />,
  );
  search();
  const result = await screen.findByTestId("dataset-select-zz10a");
  expect(result).toBeInTheDocument();
  expect(calls).toHaveLength(1);

  // Move focus into the results, then replace the project: focus was
  // stranded by the unmount, so it lands on the still-usable search field.
  result.focus();
  fireEvent.click(result);
  await screen.findByTestId("dataset-details");
  const callsBeforeSwitch = calls.length;
  rerender(<DatasetLibrary projectPath="/tmp/other" token="token" />);
  expect(screen.queryByTestId("dataset-select-zz10a")).not.toBeInTheDocument();
  expect(screen.queryByTestId("dataset-details")).not.toBeInTheDocument();
  await waitFor(() =>
    expect(document.activeElement).toBe(
      screen.getByLabelText("Search datasets"),
    ),
  );
  expect(calls).toHaveLength(callsBeforeSwitch);

  rerender(<DatasetLibrary projectPath="" token="token" />);
  expect(
    screen.getByText(/Open or create a project and enter the session token/),
  ).toBeInTheDocument();
  expect(screen.queryByTestId("dataset-select-zz10a")).not.toBeInTheDocument();
  expect(calls).toHaveLength(callsBeforeSwitch);
  // No control stays usable, so focus lands on the section itself rather
  // than the document void.
  await waitFor(() =>
    expect(document.activeElement).toBe(
      document.querySelector('section[aria-label="Dataset library"]'),
    ),
  );
});

test("results without an immutable snapshot cannot be selected", async () => {
  stubLibrary({
    pages: [
      listPage({
        items: [
          {
            provider: "mock-archive",
            dataset_id: "zz10a",
            title: "Alpha EEG",
            public: true,
            latest_snapshot: null,
          },
        ],
      }),
    ],
  });
  render(<DatasetLibrary projectPath="/tmp/project" token="token" />);
  search();
  expect(await screen.findByTestId("dataset-select-zz10a")).toBeDisabled();
});

test("search is keyboard operable through native controls", async () => {
  stubLibrary({});
  render(<DatasetLibrary projectPath="/tmp/project" token="token" />);
  const input = screen.getByLabelText("Search datasets");
  input.focus();
  expect(document.activeElement).toBe(input);
  fireEvent.change(input, { target: { value: "alpha" } });
  fireEvent.submit(input.closest("form")!);
  expect(await screen.findByTestId("dataset-select-zz10a")).toBeInTheDocument();
  await waitFor(() =>
    expect(screen.getByTestId("dataset-select-zz10a").tagName).toBe("BUTTON"),
  );
});
