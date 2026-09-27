import { useEffect, useRef } from "react";
import { bindIds, matchBind, type Shortcut } from "../shortcuts";

/**
 * Register a window keydown listener once for the component's lifetime,
 * always invoking the latest handler. Handlers can close over current
 * state/props directly — no ref mirroring, no re-registration.
 */
export function useWindowKeydown(handler: (e: KeyboardEvent) => void) {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => ref.current(e);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}

/**
 * Dispatch keydown from a shortcut list. The list owns which keys fire;
 * `actions` owns what they do. A missing action id is a programming error
 * and throws on render, so a row added to the list cannot silently do
 * nothing in the view.
 */
export function useShortcuts(
  lists: Shortcut[],
  actions: Record<string, (e: KeyboardEvent) => void>,
  opts?: {
    ignore?: (e: KeyboardEvent) => boolean;
    enabled?: boolean;
  },
) {
  useWindowKeydown((e) => {
    if (opts?.enabled === false) return;
    if (opts?.ignore?.(e)) return;
    const hit = matchBind(e, lists);
    if (!hit) return;
    actions[hit.id](e);
  });
  const missing = bindIds(lists).filter((id) => typeof actions[id] !== "function");
  if (missing.length) {
    throw new Error(`Missing shortcut handlers: ${missing.join(", ")}`);
  }
}
