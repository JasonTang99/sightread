import { useEffect, useRef } from "react";

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
