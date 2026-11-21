import { useId, useState, type ReactNode } from "react";
import type { Decision, ModelInfo, ReasoningStep } from "../../api/types";
import { Icon, type IconName } from "../../components/Icon";
import { modelLine } from "../../lib/labels";

const stepIcon: Record<ReasoningStep["kind"], IconName> = {
  analysis: "eye",
  rule: "shield",
  calculation: "calc",
  recommendation: "bulb",
};

/** Text-style toggle used for "How did you decide?" and "Read the email". */
export function DisclosureButton({
  open,
  onToggle,
  controls,
  icon,
  children,
}: {
  open: boolean;
  onToggle: () => void;
  controls: string;
  icon?: IconName;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-expanded={open}
      aria-controls={controls}
      onClick={onToggle}
      className="inline-flex h-8 items-center gap-1 rounded-md px-1 text-[13px] font-medium text-link hover:underline hover:underline-offset-4"
    >
      {icon && <Icon name={icon} size={15} />}
      {children}
      <Icon name="chevron" size={15} className={`transition-transform ${open ? "rotate-180" : ""}`} />
    </button>
  );
}

/** "How did you decide?" — the business logic behind an item, step by step. */
export function ReasoningPanel({ decision: d, id }: { decision: Decision; id: string }) {
  return (
    <div id={id} className="glass-inset rounded-xl p-4">
      <ol className="divide-y divide-line">
        {d.reasoning.map((s, i) => {
          const bad = s.passed === false;
          return (
            <li key={i} className="flex gap-3 py-2 first:pt-0 last:pb-0">
              <span
                className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md border ${
                  bad ? "border-short/30 bg-short-tint text-short" : "border-line bg-surface text-link"
                }`}
              >
                <Icon name={bad ? "alert" : stepIcon[s.kind]} size={14} />
              </span>
              <div className="min-w-0">
                <p className="text-[13px] font-semibold">
                  {i + 1}. {s.title}
                  {s.passed === true && <span className="ml-2 text-xs font-medium text-safe">OK</span>}
                  {bad && <span className="ml-2 text-xs font-semibold text-short">Problem</span>}
                </p>
                <p className="text-[13px] text-ink-soft">{s.detail}</p>
              </div>
            </li>
          );
        })}
      </ol>

      {d.ruleVerdict && (
        <p className="mt-3 border-t border-line pt-3 text-xs text-ink-soft">
          {d.agreed
            ? "The AI's suggestion was checked by fixed rules, and both agree."
            : "The AI and the fixed rules disagree, so you decide."}
        </p>
      )}
      {d.offer?.note && <p className="mt-1 text-xs text-ink-soft">{d.offer.note}.</p>}
      {d.model && <ModelLine model={d.model} className="mt-2" />}
    </div>
  );
}

/** "Granite 3.3 · vLLM on OpenShift AI · trace abc123 · guardrails passed" */
export function ModelLine({ model, className = "", dark }: { model: ModelInfo; className?: string; dark?: boolean }) {
  const blocked = model.guardrails === "blocked";
  return (
    <p
      className={`flex items-center gap-1.5 font-mono text-[10.5px] ${
        blocked ? "text-short" : dark ? "text-on-hero/45" : "text-ink-muted"
      } ${className}`}
      title="The open model that produced this, where it ran, its MLflow trace and the guardrail check"
    >
      <Icon name="shield" size={11} />
      {modelLine(model)}
    </p>
  );
}

export function EmailPanel({ decision: d, id }: { decision: Decision; id: string }) {
  const [english, setEnglish] = useState(false);
  const m = d.draftMessage;
  if (!m) return null;
  const text = english && m.english ? m.english : m;
  // Screen readers switch pronunciation by lang (the originals are German letters).
  const lang = english && m.english ? "en" : (m.language ?? (m.english ? "de" : "en"));
  return (
    <div id={id} className="glass-inset rounded-xl border border-line p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="text-xs text-ink-soft">To: {m.to}</p>
        {m.english && (
          <div className="flex gap-1" role="group" aria-label="Language">
            {[false, true].map((en) => (
              <button
                key={String(en)}
                type="button"
                aria-pressed={english === en}
                onClick={() => setEnglish(en)}
                className={`h-6 rounded-full px-2.5 text-[11px] font-semibold ${english === en ? "btn-primary" : "text-ink-soft hover:glass-inset"}`}
              >
                {en ? "English" : "Original"}
              </button>
            ))}
          </div>
        )}
      </div>
      <div lang={lang}>
        <p className="font-semibold">{text.subject}</p>
        <p className="mt-2 whitespace-pre-line text-[13px]">{text.body}</p>
      </div>
    </div>
  );
}

/** Self-contained toggle + panel (used in the done list). */
export function Reasoning({ decision: d, label = "How did you decide?" }: { decision: Decision; label?: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  if (d.reasoning.length === 0) return null;
  return (
    <div>
      <DisclosureButton open={open} onToggle={() => setOpen((o) => !o)} controls={id}>
        {label}
      </DisclosureButton>
      {open && (
        <div className="mt-2">
          <ReasoningPanel decision={d} id={id} />
        </div>
      )}
    </div>
  );
}

export function DraftEmail({ decision: d }: { decision: Decision }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  if (!d.draftMessage) return null;
  return (
    <div>
      <DisclosureButton open={open} onToggle={() => setOpen((o) => !o)} controls={id} icon="mail">
        Read the draft I wrote
      </DisclosureButton>
      {open && (
        <div className="mt-2">
          <EmailPanel decision={d} id={id} />
        </div>
      )}
    </div>
  );
}
