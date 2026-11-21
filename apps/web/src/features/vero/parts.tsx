import { Icon, type IconName } from "../../components/Icon";
import type { Classification, SourceDoc, ToolName } from "../../api/types";
import { formatEur } from "../../lib/format";

/** 3883.6 → "€3,883.60" */
export const eur = formatEur;
/** +83.6 → "+€83.60" */
export const eurDelta = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${formatEur(Math.abs(v))}`;

const CLASS_STYLE: Record<Classification, { label: string; cls: string; icon: IconName }> = {
  verified: { label: "Matches", cls: "bg-safe-tint text-safe", icon: "check" },
  unexplained: { label: "Needs evidence", cls: "bg-cream text-ask", icon: "search" },
  conflict: { label: "Conflicts with contract", cls: "bg-short-tint text-short", icon: "alert" },
  escalated: { label: "Needs evidence: escalated", cls: "bg-cream text-ask", icon: "hand" },
};

export const classLabel = (c: Classification) => CLASS_STYLE[c].label;

export function ClassificationBadge({ value, large }: { value: Classification; large?: boolean }) {
  const s = CLASS_STYLE[value];
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full font-semibold ${s.cls} ${
        large ? "px-3 py-1 text-[13px]" : "px-2.5 py-0.5 text-[11px] leading-5"
      }`}
    >
      <Icon name={s.icon} size={large ? 14 : 12} />
      {s.label}
    </span>
  );
}

export const TOOL_ICON: Record<ToolName, IconName> = {
  read_document: "file",
  search_inbox: "mail",
  search_documents: "search",
  read_clause: "file",
  get_reference_index: "eye",
  compute: "calc",
};

const BOX_TONE = {
  billed: { ring: "ring-short", tag: "bg-short text-on-fill" },
  basis: { ring: "ring-link", tag: "bg-navy text-on-hero" },
  neutral: { ring: "ring-ask", tag: "bg-ask text-on-fill" },
} as const;

/** A source document with the cited snippet boxed, like a highlighted PDF. Solid on purpose: it is paper, not a glass surface. */
export function PaperDoc({ doc, showEnglish }: { doc: SourceDoc; showEnglish: boolean }) {
  return (
    <figure className="flex h-full flex-col overflow-hidden rounded-3xl border border-line bg-surface">
      <figcaption className="flex items-center gap-2 border-b border-line bg-sunk/60 px-4 py-2.5 text-xs">
        <Icon name="file" size={14} className="text-ink-soft" />
        <span className="font-semibold">{doc.kind}</span>
        <span className="truncate font-mono text-[11px] text-ink-soft">{doc.doc_id}</span>
        <span className="ml-auto shrink-0 text-ink-muted">p. {doc.page}</span>
      </figcaption>
      <div className="flex-1 space-y-2.5 px-5 py-5 font-serif text-[13px] leading-relaxed sm:px-6">
        <p className="font-sans text-sm font-semibold">{doc.title}</p>
        <p className="font-sans text-[11px] text-ink-muted">{doc.meta}</p>
        <div className="space-y-2 pt-2">
          {doc.lines.map((l, i) =>
            l.box ? (
              <div key={i} className={`relative rounded-md bg-surface px-3 pb-2 pt-3 ring-2 ${BOX_TONE[l.box.tone].ring}`}>
                <span
                  className={`absolute -top-2.5 left-2 rounded px-1.5 font-sans text-[10px] font-semibold ${BOX_TONE[l.box.tone].tag}`}
                >
                  {l.box.label}
                </span>
                <p className="font-semibold">{l.text}</p>
                {showEnglish && l.en && <p className="mt-0.5 font-sans text-[11px] text-ink-soft">{l.en}</p>}
              </div>
            ) : (
              <div key={i}>
                <p className={l.muted ? "text-ink-soft" : l.strong ? "font-semibold" : ""}>{l.text}</p>
                {showEnglish && l.en && <p className="font-sans text-[11px] text-ink-muted">{l.en}</p>}
              </div>
            ),
          )}
        </div>
      </div>
    </figure>
  );
}
