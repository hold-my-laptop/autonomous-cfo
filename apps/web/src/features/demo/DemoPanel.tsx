import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, USE_MOCK } from "../../api/client";
import type { DemoScenario } from "../../api/types";
import { useToast } from "../../components/Toast";

// The real backend only knows the June closure and a reset; the other steps are mock-only for now.
const buttons: { scenario: DemoScenario; label: string }[] = USE_MOCK
  ? [
      { scenario: "rent_notice", label: "1. Landlord's rent adjustment arrives" },
      { scenario: "fast_forward", label: "2. Fast-forward to 1 July" },
      { scenario: "reset", label: "Start again" },
    ]
  : [
      { scenario: "closure", label: "1. Plan the June closure" },
      { scenario: "reset", label: "Start again" },
    ];

/** Presenter controls. Open the app with ?demo=1 */
export function DemoPanel() {
  const qc = useQueryClient();
  const [running, setRunning] = useState<DemoScenario | null>(null);
  // Collapsed on phones so it doesn't cover the chart.
  const [open, setOpen] = useState(() => window.matchMedia("(min-width: 640px)").matches);

  const toast = useToast();

  const run = async (scenario: DemoScenario) => {
    setRunning(scenario);
    try {
      await api.demo(scenario);
      if (scenario === "reset") await qc.invalidateQueries();
    } catch (err) {
      toast(err instanceof Error ? err.message : "That demo step failed.", "neutral");
    } finally {
      setRunning(null);
    }
  };

  return (
    <div className="fixed bottom-4 left-4 z-40 liquid-glass liquid-panel w-60 rounded-3xl p-3 text-on-hero">
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open} className="w-full text-left text-sm text-on-hero/70">
        Presenter controls {open ? "▾" : "▸"}
      </button>
      {open && (
        <div className="mt-2 flex flex-col gap-1.5">
          {buttons.map((b) => (
            <button
              key={b.scenario}
              disabled={running !== null}
              onClick={() => run(b.scenario)}
              className="liquid-glass-pill rounded-full px-3 py-2 text-left text-sm font-semibold disabled:opacity-50"
            >
              {running === b.scenario ? "Running…" : b.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
