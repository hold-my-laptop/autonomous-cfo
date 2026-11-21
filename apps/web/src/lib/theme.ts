import { useEffect, useState } from "react";

/**
 * Light / dark theme. The choice is set on <html data-theme="…">; every colour in index.css is a
 * variable that switches with it. Until the user picks one, the theme follows the system setting.
 * index.html applies the saved choice before the first paint, so the page never flashes.
 */
export type Theme = "light" | "dark";

const KEY = "vero-theme";
const systemDark = () => window.matchMedia("(prefers-color-scheme: dark)").matches;

function saved(): Theme | null {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null; // storage blocked (private window, previews): fall back to the system setting
  }
}

function apply(theme: Theme) {
  document.documentElement.dataset.theme = theme;
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => saved() ?? (systemDark() ? "dark" : "light"));

  useEffect(() => apply(theme), [theme]);

  // Follow the system setting while the user hasn't picked a theme.
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      if (!saved()) setTheme(mq.matches ? "dark" : "light");
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const toggle = () =>
    setTheme((t) => {
      const next = t === "dark" ? "light" : "dark";
      try {
        localStorage.setItem(KEY, next);
      } catch {
        /* not saved; the toggle still works for this visit */
      }
      return next;
    });

  return { theme, toggle };
}
