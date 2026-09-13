import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import {
  FIT_VIEW_DURATION_MS,
  matchesReducedMotion,
  resolveFitViewDuration,
  usePrefersReducedMotion,
} from "./motion";

function stubMatchMedia(
  matches: boolean,
  listeners: Set<() => void> = new Set(),
) {
  const query = {
    matches,
    media: "(prefers-reduced-motion: reduce)",
    addEventListener: vi.fn((_type: string, listener: () => void) => {
      listeners.add(listener);
    }),
    removeEventListener: vi.fn((type: string, listener: () => void) => {
      void type;
      listeners.delete(listener);
    }),
  };
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => query),
  );
  return { query, listeners };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("fit-view animation is disabled under reduced motion", () => {
  expect(resolveFitViewDuration(false)).toBe(FIT_VIEW_DURATION_MS);
  expect(FIT_VIEW_DURATION_MS).toBeGreaterThan(0);
  expect(resolveFitViewDuration(true)).toBe(0);
});

test("reduced-motion matching follows the media query", () => {
  stubMatchMedia(true);
  expect(matchesReducedMotion()).toBe(true);
  stubMatchMedia(false);
  expect(matchesReducedMotion()).toBe(false);
});

test("missing matchMedia means ordinary motion", () => {
  vi.stubGlobal("matchMedia", undefined as unknown as typeof window.matchMedia);
  expect(matchesReducedMotion()).toBe(false);
  const { result } = renderHook(() => usePrefersReducedMotion());
  expect(result.current).toBe(false);
});

test("the hook reflects the preference and live changes", () => {
  const listeners = new Set<() => void>();
  const { query } = stubMatchMedia(false, listeners);
  const { result } = renderHook(() => usePrefersReducedMotion());
  expect(result.current).toBe(false);

  act(() => {
    query.matches = true;
    listeners.forEach((listener) => listener());
  });
  expect(result.current).toBe(true);
});
