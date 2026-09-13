import { useEffect, useState } from "react";

export const FIT_VIEW_DURATION_MS = 200;

export const NODE_REMOVAL_TRANSITION_MS = 160;

export function resolveFitViewDuration(prefersReducedMotion: boolean): number {
  return prefersReducedMotion ? 0 : FIT_VIEW_DURATION_MS;
}

export function matchesReducedMotion(): boolean {
  if (
    typeof window === "undefined" ||
    typeof window.matchMedia !== "function"
  ) {
    return false;
  }
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(matchesReducedMotion);
  useEffect(() => {
    if (
      typeof window === "undefined" ||
      typeof window.matchMedia !== "function"
    ) {
      return;
    }
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = () => setReduced(query.matches);
    if (typeof query.addEventListener === "function") {
      query.addEventListener("change", onChange);
      return () => query.removeEventListener("change", onChange);
    }
    if (typeof query.addListener === "function") {
      query.addListener(onChange);
      return () => query.removeListener(onChange);
    }
    return;
  }, []);
  return reduced;
}
