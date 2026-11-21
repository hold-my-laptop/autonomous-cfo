import { useSyncExternalStore } from "react";

/**
 * Accessibility settings: text size, high contrast, colour-blind friendly colours, keyboard navigation.
 * They live on <html> as data attributes (index.css styles them) and are saved in localStorage.
 * index.html applies the saved values before the first paint, so the page never flashes.
 */
export type TextSize = "normal" | "large" | "xlarge";
export type A11ySettings = {
  textSize: TextSize;
  highContrast: boolean;
  colorBlind: boolean;
  reduceMotion: boolean;
  keyboard: boolean;
};

const KEY = "vero-a11y";
const DEFAULTS: A11ySettings = { textSize: "normal", highContrast: false, colorBlind: false, reduceMotion: false, keyboard: false };

/** Opens the profile menu with Settings expanded (Alt+S). */
export const OPEN_SETTINGS_EVENT = "vero:open-settings";

export const TEXT_SIZES: { value: TextSize; label: string; name: string }[] = [
  { value: "normal", label: "A", name: "Normal text" },
  { value: "large", label: "A+", name: "Large text" },
  { value: "xlarge", label: "A++", name: "Extra large text" },
];

function load(): A11ySettings {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "null") as Partial<A11ySettings> | null;
    if (!raw || typeof raw !== "object") return DEFAULTS;
    return {
      textSize: TEXT_SIZES.some((t) => t.value === raw.textSize) ? (raw.textSize as TextSize) : DEFAULTS.textSize,
      highContrast: raw.highContrast === true,
      colorBlind: raw.colorBlind === true,
      reduceMotion: raw.reduceMotion === true,
      keyboard: raw.keyboard === true,
    };
  } catch {
    return DEFAULTS; // storage blocked or bad JSON: defaults
  }
}

function save(s: A11ySettings) {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* not saved; the settings still work for this visit */
  }
}

/**
 * Puts the settings on <html>: data-text-size, data-contrast="high", data-colorblind="on" and
 * data-motion="reduce" (each only when on), data-keyboard.
 */
export function apply(s: A11ySettings) {
  const root = document.documentElement;
  root.dataset.textSize = s.textSize;
  if (s.highContrast) root.dataset.contrast = "high";
  else delete root.dataset.contrast;
  if (s.colorBlind) root.dataset.colorblind = "on";
  else delete root.dataset.colorblind;
  if (s.reduceMotion) root.dataset.motion = "reduce";
  else delete root.dataset.motion;
  root.dataset.keyboard = s.keyboard ? "on" : "off";
}

let state = load();
apply(state);
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((l) => l());

export function setA11y(patch: Partial<A11ySettings>) {
  state = { ...state, ...patch };
  apply(state);
  save(state);
  notify();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  // Another tab changed the settings: follow it.
  const onStorage = (e: StorageEvent) => {
    if (e.key !== KEY) return;
    state = load();
    apply(state);
    notify();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

export function useA11y(): A11ySettings {
  return useSyncExternalStore(subscribe, () => state);
}

// ───────────────────────── Reduced motion ─────────────────────────

const motionQuery = () => window.matchMedia("(prefers-reduced-motion: reduce)");

function subscribeMotion(listener: () => void) {
  const mq = motionQuery();
  mq.addEventListener("change", listener);
  return () => mq.removeEventListener("change", listener);
}

/** True when the OS asks for less motion, or the "Reduce motion" setting is on. */
export function prefersReducedMotion(): boolean {
  return state.reduceMotion || motionQuery().matches;
}

/**
 * For motion CSS can't stop (Recharts animations, smooth scrolling from script).
 * CSS animations and transitions are handled in index.css for both cases.
 */
export function useReducedMotion(): boolean {
  const osReduce = useSyncExternalStore(subscribeMotion, () => motionQuery().matches);
  return useA11y().reduceMotion || osReduce;
}

/** "smooth" unless motion is reduced. */
export const scrollBehavior = (): ScrollBehavior => (prefersReducedMotion() ? "auto" : "smooth");
