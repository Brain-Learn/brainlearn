import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import App from "./App";

const workflow = {
  schema_version: "1.0",
  id: "test",
  metadata: {
    name: "Test EEG",
    description: "",
    created_with: "test",
    modality: "EEG",
    status: "example",
  },
  nodes: [
    {
      id: "filter",
      type: "eeg.filter",
      label: "Band-pass Filter",
      category: "Preprocessing",
      description: "Filter a signal.",
      position: { x: 0, y: 0 },
      ports: [],
      parameters: [
        { id: "low", label: "Low cutoff", value: 1, required: true },
      ],
      pauses_for_review: false,
    },
  ],
  edges: [],
};

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue({ ok: true, json: () => Promise.resolve(workflow) }),
  );
  class ResizeObserverMock {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", ResizeObserverMock);
});

test("loads the example graph and exposes selection details", async () => {
  render(<App />);
  await waitFor(() =>
    expect(screen.getAllByText("Band-pass Filter").length).toBeGreaterThan(0),
  );
  expect(screen.getByText("Low cutoff")).toBeInTheDocument();
  expect(
    screen.getByText(
      "Execution is intentionally unavailable in this foundation slice.",
    ),
  ).toBeInTheDocument();
});
