import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import type { ConnectionState } from "../api/live";
import { OPEN_SETTINGS_EVENT, setA11y, TEXT_SIZES, useA11y } from "../lib/a11y";
import { Icon } from "./Icon";

const connectionText: Record<ConnectionState, string> = { live: "Connected", connecting: "Connecting…", offline: "Offline" };
const dotClass: Record<ConnectionState, string> = { live: "bg-safe", connecting: "bg-tight", offline: "bg-short" };

function initials(name?: string) {
  if (!name) return "··";
  const words = name.replace(/\s+(GmbH|AG|KG)$/i, "").trim().split(/\s+/);
  return ((words[0]?.[0] ?? "") + (words[1]?.[0] ?? "")).toUpperCase();
}

/** Everything in the panel the ↑/↓ keys move between: buttons and the selected text-size radio. */
const FOCUSABLE = 'button:not([disabled]), input[type="radio"]:checked, a[href]';

const shortcuts: [string[], string][] = [
  [["Tab", "Shift+Tab"], "Move between items"],
  [["Enter"], "Open or choose"],
  [["←", "→"], "Switch tabs and options"],
  [["↑", "↓"], "Move inside this menu"],
  [["Alt+1–4"], "Go to a page"],
  [["Alt+S"], "Open these settings"],
  [["Esc"], "Close"],
];

function Switch({
  checked,
  onChange,
  label,
  description,
}: {
  checked: boolean;
  onChange: (on: boolean) => void;
  label: string;
  description: string;
}) {
  const id = useId();
  return (
    <div className="flex items-start gap-3">
      <div className="min-w-0 flex-1">
        <p id={`${id}-l`} className="text-[13px] font-semibold text-ink">
          {label}
        </p>
        <p id={`${id}-d`} className="text-xs text-ink-soft">
          {description}
        </p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={`${id}-l`}
        aria-describedby={`${id}-d`}
        onClick={() => onChange(!checked)}
        className={`relative mt-0.5 inline-flex h-6 w-10 shrink-0 items-center rounded-full border transition-colors ${
          checked ? "btn-primary border-transparent" : "border-line-strong bg-sunk"
        }`}
      >
        <span
          aria-hidden="true"
          className={`h-[18px] w-[18px] rounded-full shadow transition-transform ${
            checked ? "translate-x-[18px] bg-[color:var(--primary-ink)]" : "translate-x-[2px] bg-surface"
          }`}
        />
      </button>
    </div>
  );
}

function Section({ children }: { children: ReactNode }) {
  return <div className="border-t border-line px-4 py-3">{children}</div>;
}

/**
 * The avatar in the header, and the account dropdown it opens. "Settings" expands in place with the
 * accessibility options. The panel is solid, not glass: it sits inside the glass navbar.
 */
export function ProfileMenu({
  companyName,
  ownerName,
  connection,
}: {
  companyName?: string;
  ownerName?: string;
  connection: ConnectionState;
}) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const a11y = useA11y();
  const wrapRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const settingsRef = useRef<HTMLDivElement>(null);
  const focusOnOpen = useRef<"first" | "settings">("first");
  const panelId = useId();
  const settingsId = useId();
  const textSizeName = useId();

  const items = () =>
    [...(panelRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? [])].filter((el) => el.offsetParent !== null);

  const close = useCallback((refocus: boolean) => {
    setOpen(false);
    if (refocus) triggerRef.current?.focus();
  }, []);

  // Focus the first item when the panel opens (or the first setting, when opened with Alt+S).
  useEffect(() => {
    if (!open) return;
    const target =
      focusOnOpen.current === "settings"
        ? settingsRef.current?.querySelector<HTMLElement>(FOCUSABLE)
        : panelRef.current?.querySelector<HTMLElement>(FOCUSABLE);
    target?.focus();
  }, [open]);

  // Click outside closes.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) close(false);
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [open, close]);

  // Alt+S (see Layout): open with Settings expanded.
  useEffect(() => {
    const onOpen = () => {
      focusOnOpen.current = "settings";
      setExpanded(true);
      setOpen(true);
      // Already open: move focus now, the open effect won't run again.
      requestAnimationFrame(() => settingsRef.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus());
    };
    window.addEventListener(OPEN_SETTINGS_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_SETTINGS_EVENT, onOpen);
  }, []);

  const toggle = () => {
    focusOnOpen.current = "first";
    setOpen((o) => !o);
  };

  const onTriggerKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key === "ArrowDown" && !open) {
      e.preventDefault();
      focusOnOpen.current = "first";
      setOpen(true);
    }
  };

  const onPanelKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") {
      e.preventDefault();
      close(true);
      return;
    }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    // ↑/↓ move between items (also on a radio, where they would otherwise change the text size).
    e.preventDefault();
    const list = items();
    if (!list.length) return;
    const i = list.indexOf(document.activeElement as HTMLElement);
    const next = e.key === "ArrowDown" ? (i + 1) % list.length : (i - 1 + list.length) % list.length;
    list[i === -1 ? 0 : next].focus();
  };

  return (
    <div
      ref={wrapRef}
      className="relative shrink-0"
      onBlur={(e) => {
        // Focus left the avatar and the panel (e.g. Tab past the end): close without stealing focus back.
        if (open && !wrapRef.current?.contains(e.relatedTarget as Node | null)) close(false);
      }}
    >
      <button
        ref={triggerRef}
        type="button"
        onClick={toggle}
        onKeyDown={onTriggerKey}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={`Account and settings for ${companyName ?? "your company"}. ${connectionText[connection]}`}
        className="relative flex h-9 w-9 shrink-0 items-center justify-center rounded-full liquid-glass-pill avatar text-[13px] font-semibold"
        title={`${companyName ?? ""} · ${connectionText[connection]}`}
      >
        {initials(companyName)}
        <span
          className={`absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-surface ${dotClass[connection]}`}
          aria-hidden="true"
        />
        <span className="sr-only">{connectionText[connection]}</span>
      </button>

      {open && (
        <div
          ref={panelRef}
          id={panelId}
          role="dialog"
          aria-label="Account and settings"
          // Focusable, so a click on plain text inside the panel keeps focus inside (and doesn't close it).
          tabIndex={-1}
          onKeyDown={onPanelKey}
          // Width: right-aligned under the avatar, which sits 28px in from the window edge on phones
          // (header mx-3 + px-4, zoomed with the text size), so keep 1rem free on the left.
          className="absolute right-0 focus:outline-none top-[calc(100%+1.25rem)] w-[min(20rem,calc((100vw-1rem)/var(--ui-zoom,1)-28px))] overflow-y-auto rounded-2xl border border-line bg-surface text-ink shadow-[0_16px_40px_rgb(15_26_53/0.22)]"
          style={{ maxHeight: "calc((100svh - 7rem) / var(--ui-zoom, 1))" }}
        >
          {/* Who */}
          <div className="px-4 py-3">
            <p className="truncate text-[15px] font-semibold">{companyName ?? "Loading…"}</p>
            {ownerName && <p className="truncate text-[13px] text-ink-soft">{ownerName}</p>}
            <p className="mt-1.5 flex items-center gap-1.5 text-xs text-ink-soft">
              <span className={`h-2 w-2 rounded-full ${dotClass[connection]}`} aria-hidden="true" />
              {connectionText[connection]}
            </p>
          </div>

          {/* Settings */}
          <div className="border-t border-line p-1.5">
            <button
              type="button"
              aria-expanded={expanded}
              aria-controls={settingsId}
              onClick={() => setExpanded((x) => !x)}
              className="flex w-full items-center gap-3 rounded-xl px-2.5 py-2 text-left hover:bg-chip"
            >
              <Icon name="settings" size={18} className="shrink-0 text-ink-soft" />
              <span className="min-w-0 flex-1">
                <span className="block text-[14px] font-semibold">Settings</span>
                <span className="block text-xs text-ink-soft">Accessibility</span>
              </span>
              <Icon name="chevron" size={16} className={`shrink-0 text-ink-soft transition-transform ${expanded ? "rotate-180" : ""}`} />
            </button>
          </div>

          {expanded && (
            <div id={settingsId} ref={settingsRef} role="group" aria-label="Accessibility settings">
              <Section>
                <fieldset>
                  <legend className="text-[13px] font-semibold">Text size</legend>
                  <div className="mt-2 flex gap-1 rounded-full bg-sunk p-1">
                    {TEXT_SIZES.map((t, i) => {
                      const selected = a11y.textSize === t.value;
                      return (
                        <label
                          key={t.value}
                          className={`a11y-seg flex h-8 flex-1 cursor-pointer items-center justify-center rounded-full font-semibold ${
                            selected ? "btn-primary" : "text-ink hover:bg-chip"
                          }`}
                        >
                          <input
                            type="radio"
                            name={textSizeName}
                            value={t.value}
                            checked={selected}
                            onChange={() => setA11y({ textSize: t.value })}
                            className="sr-only"
                          />
                          <span aria-hidden="true" style={{ fontSize: `${13 + i * 2}px` }}>
                            {t.label}
                          </span>
                          <span className="sr-only">{t.name}</span>
                        </label>
                      );
                    })}
                  </div>
                </fieldset>
              </Section>

              <Section>
                <Switch
                  checked={a11y.highContrast}
                  onChange={(on) => setA11y({ highContrast: on })}
                  label="High contrast"
                  description="Stronger text, borders and solid surfaces."
                />
              </Section>

              <Section>
                <Switch
                  checked={a11y.colorBlind}
                  onChange={(on) => setA11y({ colorBlind: on })}
                  label="Colour-blind friendly"
                  description="Blue for good, orange for problems, stripes on overdraft in charts."
                />
              </Section>

              <Section>
                <Switch
                  checked={a11y.reduceMotion}
                  onChange={(on) => setA11y({ reduceMotion: on })}
                  label="Reduce motion"
                  description="No animations, sliding or smooth scrolling."
                />
              </Section>

              <Section>
                <Switch
                  checked={a11y.keyboard}
                  onChange={(on) => setA11y({ keyboard: on })}
                  label="Keyboard navigation"
                  description="A clear focus ring and keyboard shortcuts."
                />
                {a11y.keyboard && (
                  <ul className="mt-3 space-y-1.5 text-xs text-ink-soft">
                    {shortcuts.map(([keys, what]) => (
                      <li key={what} className="flex items-center justify-between gap-3">
                        <span className="flex flex-wrap gap-1">
                          {keys.map((k) => (
                            <kbd key={k}>{k}</kbd>
                          ))}
                        </span>
                        <span className="text-right">{what}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </Section>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
