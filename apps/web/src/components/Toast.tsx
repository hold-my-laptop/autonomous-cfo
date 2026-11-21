import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { Icon } from "./Icon";

type Toast = { id: number; message: string; tone: "done" | "neutral" };
const ToastContext = createContext<(message: string, tone?: Toast["tone"]) => void>(() => {});

export const useToast = () => useContext(ToastContext);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const dismiss = (id: number) => setToasts((t) => t.filter((x) => x.id !== id));

  const show = useCallback((message: string, tone: Toast["tone"] = "done") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, message, tone }]);
    setTimeout(() => dismiss(id), 7000);
  }, []);

  return (
    <ToastContext.Provider value={show}>
      {children}
      <div aria-live="polite" className="pointer-events-none fixed inset-x-0 bottom-6 z-50 flex flex-col items-center gap-2 px-4">
        {toasts.map((t) => (
          <div
            key={t.id}
            role="status"
            className="toast-in pointer-events-auto flex max-w-xl items-start gap-3 liquid-glass liquid-panel rounded-3xl px-4 py-3 text-sm text-on-hero"
          >
            {t.tone === "done" && <Icon name="check" className="mt-0.5 text-safe-tint" />}
            <p className="flex-1">{t.message}</p>
            <button onClick={() => dismiss(t.id)} aria-label="Close message" className="rounded p-0.5 text-on-hero/70 hover:text-on-hero">
              <Icon name="x" size={18} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
