import { useSyncExternalStore } from "react";

/**
 * Screen-reader announcements: anything can call announce("…"); <Announcer/> (in Layout) renders the
 * text in a polite live region. A counter makes the same text announce again when repeated.
 */
let current = { text: "", n: 0 };
const listeners = new Set<() => void>();

export function announce(text: string) {
  current = { text, n: current.n + 1 };
  listeners.forEach((l) => l());
}

export function useAnnouncement() {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => current,
  );
}
