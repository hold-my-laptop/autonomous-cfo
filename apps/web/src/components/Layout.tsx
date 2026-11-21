import { useEffect, useLayoutEffect, useRef, type KeyboardEvent } from "react";
import { NavLink, Outlet, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useCompany } from "../api/queries";
import { useLive } from "../api/live";
import { ChatPanel } from "../features/chat/ChatPanel";
import { DemoPanel } from "../features/demo/DemoPanel";
import { OPEN_SETTINGS_EVENT, useA11y } from "../lib/a11y";
import { announce, useAnnouncement } from "../lib/announce";
import { useTheme } from "../lib/theme";
import { Icon } from "./Icon";
import { Wordmark } from "./Logo";
import { ProfileMenu } from "./ProfileMenu";
import { dismissBootSplash } from "../lib/bootSplash";

// The active tab has no fill of its own: the sliding pill behind it shows which one is active.
const navClass = ({ isActive }: { isActive: boolean }) =>
  `nav-tab relative z-10 inline-flex h-10 items-center whitespace-nowrap rounded-full px-4 text-[15px] font-medium ${
    isActive ? "text-[color:var(--primary-ink)]" : "text-ink hover:bg-chip"
  }`;

/** Main tabs, with a pill that slides to the active tab. */
function NavTabs() {
  const navRef = useRef<HTMLElement>(null);
  const pillRef = useRef<HTMLSpanElement>(null);
  const { pathname } = useLocation();

  useLayoutEffect(() => {
    const nav = navRef.current;
    const pill = pillRef.current;
    if (!nav || !pill) return;
    const place = () => {
      const active = nav.querySelector<HTMLElement>('a[aria-current="page"]');
      if (!active) {
        pill.style.opacity = "0";
        return;
      }
      pill.style.width = `${active.offsetWidth}px`;
      pill.style.transform = `translateX(${active.offsetLeft}px)`;
      pill.style.opacity = "1";
    };
    place();
    // Animate only after the first placement, so the pill doesn't fly in on page load.
    const ready = requestAnimationFrame(() => pill.classList.add("nav-pill-ready"));
    const ro = new ResizeObserver(place);
    ro.observe(nav);
    document.fonts?.ready.then(place);
    return () => {
      cancelAnimationFrame(ready);
      ro.disconnect();
    };
  }, [pathname]);

  // ←/→ move focus between the tabs (wrapping around), Home/End jump to the first/last. No visual change.
  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    const tabs = [...(navRef.current?.querySelectorAll<HTMLAnchorElement>("a") ?? [])];
    const i = tabs.indexOf(document.activeElement as HTMLAnchorElement);
    if (i === -1 || !tabs.length) return;
    const next =
      e.key === "ArrowRight" ? (i + 1) % tabs.length
      : e.key === "ArrowLeft" ? (i - 1 + tabs.length) % tabs.length
      : e.key === "Home" ? 0
      : e.key === "End" ? tabs.length - 1
      : -1;
    if (next === -1) return;
    e.preventDefault();
    tabs[next].focus();
  };

  return (
    <nav
      ref={navRef}
      aria-label="Main"
      onKeyDown={onKeyDown}
      className="liquid-glass-pill col-span-2 row-start-2 flex gap-1 justify-self-start overflow-x-auto rounded-full p-1 max-lg:max-w-full lg:col-span-1 lg:row-start-1 lg:col-start-2"
    >
      <span ref={pillRef} aria-hidden="true" className="nav-pill btn-primary nav-active absolute left-0 top-1 h-10 rounded-full opacity-0" />
      <NavLink to="/" end className={navClass}>Overview</NavLink>
      <NavLink to="/contract" className={navClass}>Contract check</NavLink>
      <NavLink to="/costs" className={navClass}>Cost changes</NavLink>
      <NavLink to="/activity" className={navClass}>Activity</NavLink>
    </nav>
  );
}

const PAGES = ["/", "/contract", "/costs", "/activity"];
const PAGE_NAMES: Record<string, string> = {
  "/": "Overview",
  "/contract": "Contract check",
  "/costs": "Cost changes",
  "/activity": "Activity",
};

/**
 * A title per page ("Overview · Vero"), so tabs, history and screen readers say where you are,
 * and an announcement when the page changes inside the app (the first load is announced by the title).
 */
function usePageTitle() {
  const { pathname } = useLocation();
  const first = useRef(true);
  useEffect(() => {
    const name = PAGE_NAMES[pathname];
    document.title = name ? `${name} · Vero` : "Vero";
    if (first.current) {
      first.current = false;
      return;
    }
    if (name) announce(`${name} page`);
  }, [pathname]);
}

/** The polite live region for announce(): new items, page changes. */
function Announcer() {
  const { text, n } = useAnnouncement();
  // A trailing no-break space on every other message makes a repeated text count as new.
  return (
    <div aria-live="polite" aria-atomic="true" className="sr-only">
      {text ? text + (n % 2 ? " " : "") : ""}
    </div>
  );
}

/**
 * Keyboard shortcuts while keyboard navigation is on: Alt+1–4 open a page and move focus to its
 * content, Alt+S opens the settings. `e.code` keeps them working on every keyboard layout;
 * Ctrl/Meta combinations (and AltGr, which is Ctrl+Alt on Windows) are left alone.
 */
function useShortcuts(enabled: boolean) {
  const navigate = useNavigate();
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (!e.altKey || e.ctrlKey || e.metaKey || e.repeat) return;
      const page = /^Digit([1-4])$/.exec(e.code);
      if (page) {
        e.preventDefault();
        navigate(PAGES[Number(page[1]) - 1]);
        requestAnimationFrame(() => document.getElementById("main")?.focus());
      } else if (e.code === "KeyS") {
        e.preventDefault();
        window.dispatchEvent(new Event(OPEN_SETTINGS_EVENT));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled, navigate]);
}

export function Layout() {
  const { data: company, isPending } = useCompany();
  const { connection } = useLive();
  const { theme, toggle } = useTheme();
  const { keyboard } = useA11y();
  const [params] = useSearchParams();
  const showDemo = params.get("demo") === "1";
  useShortcuts(keyboard);
  usePageTitle();

  // First data in (or failed): fade out the loading screen from index.html.
  useEffect(() => {
    if (!isPending) dismissBootSplash();
  }, [isPending]);

  return (
    <div className="min-h-screen">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-full focus:bg-surface focus:px-4 focus:py-2"
      >
        Skip to content
      </a>

      <header className="sticky top-3 z-[60] mx-3 mt-3 sm:mx-6">
        <div className="nav-glass mx-auto max-w-6xl">
        <div className="mx-auto grid max-w-6xl grid-cols-[1fr_auto] items-center gap-y-2 px-4 py-2 sm:px-8 lg:grid-cols-[1fr_auto_1fr]">
          <div className="flex items-center gap-2 lg:pr-4">
            <Wordmark className="text-[24px]" />
          </div>

          <NavTabs />

          <div className="flex min-w-0 items-center justify-end gap-2.5">
            <button
              type="button"
              onClick={toggle}
              aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              title={theme === "dark" ? "Light theme" : "Dark theme"}
              className="nav-tab liquid-glass-pill flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-ink-soft hover:text-ink"
            >
              <Icon key={theme} name={theme === "dark" ? "sun" : "moon"} size={16} className="icon-in" />
            </button>
            <span className="hidden truncate text-[15px] font-semibold sm:inline">{company?.name ?? "Loading…"}</span>
            <ProfileMenu companyName={company?.name} ownerName={company?.ownerFirstName} connection={connection} />
          </div>
        </div>
        </div>
      </header>

      <main id="main" tabIndex={-1} className="mx-auto max-w-6xl px-4 py-8 sm:px-8 sm:py-10">
        <Outlet />
      </main>

      <footer className="border-t border-line">
        <p className="mx-auto flex max-w-6xl items-center gap-2 px-4 py-5 text-xs text-ink-soft sm:px-8">
          <span className="h-2 w-2 shrink-0 rounded-full bg-[#ee0000]" aria-hidden="true" />
          Runs fully on Red Hat OpenShift AI with open models. Your data never leaves your environment.
        </p>
      </footer>

      {showDemo && <DemoPanel />}
      <ChatPanel />
      <Announcer />
    </div>
  );
}
