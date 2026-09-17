import "@testing-library/jest-dom/vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { LocalImport } from "./LocalImport";
import type { LocalImportRecord } from "./types";

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const PROJECT_PATH = "/home/researcher/studies/eeg-pilot";
const TOKEN = "test-session-token-12345";

function mockScanningRecord(importId = "li-123456789abc"): LocalImportRecord {
  return {
    schema_version: "1.0",
    import_id: importId,
    state: "scanning",
    local_path: "raw-data",
    lock: null,
    failure: null,
    created_at: "2026-09-17T00:00:00Z",
    updated_at: "2026-09-17T00:00:00Z",
  };
}

function mockReadyRecord(importId = "li-123456789abc"): LocalImportRecord {
  return {
    schema_version: "1.0",
    import_id: importId,
    state: "ready",
    local_path: "raw-data",
    lock: {
      schema_version: "1.0",
      dataset_identity: `brainlearn-v1:dataset:${"f".repeat(64)}`,
      catalog_identity: null,
      provider: "local",
      dataset_id: "raw-data",
      snapshot: "local",
      access: "restricted",
      title: "Pilot EEG Dataset",
      modality: "",
      task: "",
      participants: 0,
      formats: ["EDF"],
      citations: [],
      compatible_templates: [],
      landing_page: null,
      limitations: "Internal use only.",
      retrieved_at: "2026-09-17T00:01:00Z",
      local_path: "raw-data",
      expected_total_bytes: 1024,
      expected_files: [
        {
          path: "sub-01.edf",
          byte_size: 1024,
          sha256: "a".repeat(64),
        },
      ],
      license_name: null,
      license_spdx: null,
      reuse_statement: null,
    },
    failure: null,
    created_at: "2026-09-17T00:00:00Z",
    updated_at: "2026-09-17T00:01:00Z",
  };
}

function mockFailedRecord(
  code = "verification_failed",
  message = "The import storage layout is blocked by a symlink.",
): LocalImportRecord {
  return {
    schema_version: "1.0",
    import_id: "li-123456789abc",
    state: "failed",
    local_path: "raw-data",
    lock: null,
    failure: { code, message },
    created_at: "2026-09-17T00:00:00Z",
    updated_at: "2026-09-17T00:01:00Z",
  };
}

function mockCancelledRecord(): LocalImportRecord {
  return {
    schema_version: "1.0",
    import_id: "li-123456789abc",
    state: "cancelled",
    local_path: "raw-data",
    lock: null,
    failure: {
      code: "cancelled",
      message: "The local import was cancelled.",
    },
    created_at: "2026-09-17T00:00:00Z",
    updated_at: "2026-09-17T00:01:00Z",
  };
}

test("renders guidance and disables inputs when project is not open", () => {
  render(<LocalImport projectPath="" token="" />);

  expect(
    screen.getByText(
      "Provide an authorized project and session token to import private data.",
    ),
  ).toBeInTheDocument();

  expect(
    screen.getByLabelText("Directory path relative to project root"),
  ).toBeDisabled();
  expect(screen.getByLabelText("Dataset title")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Start import" })).toBeDisabled();
});

test("does not steal focus on project or token change", () => {
  const externalButton = document.createElement("button");
  externalButton.textContent = "External";
  document.body.appendChild(externalButton);
  externalButton.focus();
  expect(document.activeElement).toBe(externalButton);

  const { rerender } = render(
    <LocalImport projectPath={PROJECT_PATH} token={TOKEN} />,
  );

  // Focus must not be stolen to input
  expect(document.activeElement).toBe(externalButton);

  rerender(<LocalImport projectPath="/another/path" token="another-token" />);
  expect(document.activeElement).toBe(externalButton);
  document.body.removeChild(externalButton);
});

test("successful import flow: start -> scanning -> ready -> focus ready heading", async () => {
  const scanningRec = mockScanningRecord();
  const readyRec = mockReadyRecord();

  let pollCount = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/datasets/local/import" && init?.method === "POST") {
        return new Response(JSON.stringify(scanningRec), { status: 200 });
      }
      if (url.startsWith("/api/datasets/local/imports/")) {
        pollCount += 1;
        const rec = pollCount >= 2 ? readyRec : scanningRec;
        return new Response(JSON.stringify(rec), { status: 200 });
      }
      return new Response("Not found", { status: 404 });
    }),
  );

  render(<LocalImport projectPath={PROJECT_PATH} token={TOKEN} />);

  fireEvent.change(
    screen.getByLabelText("Directory path relative to project root"),
    { target: { value: "raw-data" } },
  );
  fireEvent.change(screen.getByLabelText("Dataset title"), {
    target: { value: "Pilot EEG Dataset" },
  });
  fireEvent.change(screen.getByLabelText("Dataset limitations"), {
    target: { value: "Internal use only." },
  });
  fireEvent.change(screen.getByLabelText("Dataset formats"), {
    target: { value: "EDF" },
  });

  fireEvent.click(screen.getByRole("button", { name: "Start import" }));

  await screen.findByText("Scanning and hashing files…");

  // Advance timers to trigger polling
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1100);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1100);
  });

  await screen.findByText("Import complete");
  expect(screen.getByText(readyRec.lock!.dataset_identity)).toBeInTheDocument();
  expect(screen.getByText("Internal use only.")).toBeInTheDocument();

  // Ready heading should be focused
  const readyHeading = screen.getByRole("heading", { name: "Import complete" });
  expect(document.activeElement).toBe(readyHeading);

  // Click "Import another dataset" returns to form and focuses directory input
  fireEvent.click(screen.getByTestId("local-import-back"));
  await waitFor(() => {
    expect(
      screen.getByLabelText("Directory path relative to project root"),
    ).toBeInTheDocument();
  });
});

test("failed import flow: start -> fails -> focus Back button", async () => {
  const failedRec = mockFailedRecord();

  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/datasets/local/import" && init?.method === "POST") {
        return new Response(JSON.stringify(failedRec), { status: 200 });
      }
      return new Response("Not found", { status: 404 });
    }),
  );

  render(<LocalImport projectPath={PROJECT_PATH} token={TOKEN} />);

  fireEvent.change(
    screen.getByLabelText("Directory path relative to project root"),
    { target: { value: "bad-data" } },
  );
  fireEvent.change(screen.getByLabelText("Dataset title"), {
    target: { value: "Bad Data" },
  });

  fireEvent.click(screen.getByRole("button", { name: "Start import" }));

  await screen.findByText(failedRec.failure!.message);
  const backBtn = screen.getByTestId("local-import-back");
  await waitFor(() => {
    expect(document.activeElement).toBe(backBtn);
  });

  // Pressing Escape in failure returns to form
  fireEvent.keyDown(screen.getByTestId("local-import-failed"), {
    key: "Escape",
  });
  await waitFor(() => {
    expect(
      screen.getByLabelText("Directory path relative to project root"),
    ).toBeInTheDocument();
  });
});

test("cancellation flow: scanning -> cancel -> cancelled view", async () => {
  const scanningRec = mockScanningRecord();
  const cancelledRec = mockCancelledRecord();

  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/datasets/local/import") {
        return new Response(JSON.stringify(scanningRec), { status: 200 });
      }
      if (url.endsWith("/cancel") && init?.method === "POST") {
        return new Response(JSON.stringify(cancelledRec), { status: 200 });
      }
      return new Response(JSON.stringify(scanningRec), { status: 200 });
    }),
  );

  render(<LocalImport projectPath={PROJECT_PATH} token={TOKEN} />);

  fireEvent.change(
    screen.getByLabelText("Directory path relative to project root"),
    { target: { value: "raw-data" } },
  );
  fireEvent.change(screen.getByLabelText("Dataset title"), {
    target: { value: "Pilot EEG" },
  });

  fireEvent.click(screen.getByRole("button", { name: "Start import" }));

  await screen.findByText("Scanning and hashing files…");

  fireEvent.click(screen.getByTestId("local-import-cancel"));

  await screen.findByText("Import cancelled. You may start a new import.");
  expect(screen.getByTestId("local-import-reset")).toBeInTheDocument();
});

test("project replacement during pending start drops stale response", async () => {
  let resolveStart: (value: Response) => void;
  const startPromise = new Promise<Response>((resolve) => {
    resolveStart = resolve;
  });

  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url === "/api/datasets/local/import") {
        return startPromise;
      }
      return new Response("{}", { status: 200 });
    }),
  );

  const { rerender } = render(
    <LocalImport projectPath={PROJECT_PATH} token={TOKEN} />,
  );

  fireEvent.change(
    screen.getByLabelText("Directory path relative to project root"),
    { target: { value: "raw-data" } },
  );
  fireEvent.change(screen.getByLabelText("Dataset title"), {
    target: { value: "Pilot" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Start import" }));

  // Switch project before start resolves
  rerender(<LocalImport projectPath="/switched/project" token="new-token" />);

  // Resolve old start with ready record
  act(() => {
    resolveStart(
      new Response(JSON.stringify(mockReadyRecord()), { status: 200 }),
    );
  });

  // Stale response must NOT transition the UI to ready
  await vi.advanceTimersByTimeAsync(100);
  expect(screen.queryByText("Import complete")).not.toBeInTheDocument();
  expect(
    screen.getByLabelText("Directory path relative to project root"),
  ).toBeInTheDocument();
});

test("validates complete lock response and rejects invalid response", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      // Invalid response missing schema_version
      return new Response(
        JSON.stringify({ import_id: "li-123", state: "ready", lock: {} }),
        { status: 200 },
      );
    }),
  );

  render(<LocalImport projectPath={PROJECT_PATH} token={TOKEN} />);

  fireEvent.change(
    screen.getByLabelText("Directory path relative to project root"),
    { target: { value: "raw-data" } },
  );
  fireEvent.change(screen.getByLabelText("Dataset title"), {
    target: { value: "Pilot" },
  });

  fireEvent.click(screen.getByRole("button", { name: "Start import" }));

  await screen.findByText(
    "The local import response does not match contract version 1.0.",
  );
});
