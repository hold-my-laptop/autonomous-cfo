import type { ServerEvent } from "./types";

type Listener = (event: ServerEvent) => void;
const listeners = new Set<Listener>();

/** In-browser stand-in for the backend's SSE stream, used in mock mode. */
export const mockBus = {
  emit(event: ServerEvent) {
    listeners.forEach((l) => l(event));
  },
  subscribe(listener: Listener) {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
};
