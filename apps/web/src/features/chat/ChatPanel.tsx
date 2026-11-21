import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../../api/client";
import { useChat } from "../../api/queries";
import type { ChatTurn } from "../../api/types";
import { Icon } from "../../components/Icon";
import { scrollBehavior } from "../../lib/a11y";

type Message = ChatTurn & { fallback?: boolean; error?: boolean };

const MAX_QUESTION = 500;

const examples = ["When does my cash get tight?", "What needs my OK?", "Which payments can move?"];

function errorText(err: unknown): string {
  if (err instanceof ApiError && err.status === 429) return "Vero is busy with another question. Try again in a moment.";
  if (err instanceof ApiError && err.status === 422) return "That question is too long. Keep it under 500 characters.";
  return "Couldn't reach Vero. Check that the AI service is running.";
}

/** "Ask Vero": owner questions, answered by the AI service from read-only data. */
export function ChatPanel() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const chat = useChat();
  const listRef = useRef<HTMLOListElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const openButtonRef = useRef<HTMLButtonElement>(null);
  const returnFocus = useRef(false);
  const panelId = useId();

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: scrollBehavior() });
  }, [messages, chat.isPending]);

  // Opening focuses the question box; closing puts focus back on "Ask Vero".
  useEffect(() => {
    if (open) inputRef.current?.focus();
    else if (returnFocus.current) {
      returnFocus.current = false;
      openButtonRef.current?.focus();
    }
  }, [open]);

  const close = () => {
    returnFocus.current = true;
    setOpen(false);
  };

  const ask = async (question: string) => {
    const q = question.trim().slice(0, MAX_QUESTION);
    if (!q || chat.isPending) return;
    // Only real turns go back as history: no error lines.
    const history: ChatTurn[] = messages.filter((m) => !m.error).map(({ role, content }) => ({ role, content }));
    setMessages((m) => [...m, { role: "user", content: q }]);
    setDraft("");
    try {
      const res = await chat.mutateAsync({ question: q, history });
      setMessages((m) => [...m, { role: "assistant", content: res.answer, fallback: res.source === "FALLBACK" }]);
    } catch (err) {
      setMessages((m) => [...m, { role: "assistant", content: errorText(err), error: true }]);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    ask(draft);
  };

  if (!open) {
    return (
      <button
        ref={openButtonRef}
        type="button"
        onClick={() => setOpen(true)}
        aria-expanded={false}
        aria-controls={panelId}
        className="btn-primary fixed bottom-4 right-4 z-40 inline-flex h-12 items-center gap-2 rounded-full px-5 text-[14px] font-semibold shadow-lg"
      >
        <Icon name="chat" size={18} />
        Ask Vero
      </button>
    );
  }

  return (
    <section
      id={panelId}
      aria-label="Ask Vero"
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          close();
        }
      }}
      className="liquid-glass liquid-panel fixed bottom-4 right-4 z-40 flex max-h-[min(36rem,calc(100vh-6rem))] w-[min(24rem,calc(100vw-2rem))] flex-col rounded-3xl text-on-hero"
    >
      <header className="flex items-center gap-2 px-4 pb-2 pt-3">
        <Icon name="chat" size={16} />
        <h2 className="mr-auto text-sm font-semibold">Ask Vero</h2>
        <button
          type="button"
          onClick={close}
          aria-label="Close chat"
          className="rounded p-1 text-on-hero/70 hover:text-on-hero"
        >
          <Icon name="x" size={18} />
        </button>
      </header>

      <ol ref={listRef} aria-live="polite" className="min-h-0 flex-1 space-y-2 overflow-y-auto px-4 py-2">
        {messages.length === 0 && (
          <li className="space-y-2">
            <p className="text-[13px] text-on-hero/75">
              Ask about your cash, payments or what Vero proposed. Vero can only read your data; it can't change anything.
            </p>
            <div className="flex flex-wrap gap-1.5">
              {examples.map((q) => (
                <button
                  key={q}
                  type="button"
                  onClick={() => ask(q)}
                  className="liquid-glass-pill rounded-full px-3 py-1.5 text-left text-[12px] font-medium"
                >
                  {q}
                </button>
              ))}
            </div>
          </li>
        )}
        {messages.map((m, i) => (
          <li key={i} className={m.role === "user" ? "flex justify-end" : "flex"}>
            <div
              className={`max-w-[85%] whitespace-pre-line rounded-2xl px-3 py-2 text-[13px] ${
                m.role === "user" ? "btn-primary" : m.error ? "bg-short/20 text-on-hero" : "liquid-glass-pill"
              }`}
            >
              <span className="sr-only">{m.role === "user" ? "You: " : "Vero: "}</span>
              {m.content}
              {m.fallback && (
                <span className="mt-1 block text-[11px] text-on-hero/60">Short answer without the AI model.</span>
              )}
            </div>
          </li>
        ))}
        {chat.isPending && (
          <li className="flex">
            <span className="liquid-glass-pill animate-pulse rounded-2xl px-3 py-2 text-[13px]">Vero is looking…</span>
          </li>
        )}
      </ol>

      <form onSubmit={submit} className="flex items-end gap-2 px-3 pb-3 pt-2">
        <label htmlFor={`${panelId}-q`} className="sr-only">
          Your question
        </label>
        <textarea
          id={`${panelId}-q`}
          ref={inputRef}
          rows={1}
          value={draft}
          maxLength={MAX_QUESTION}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              ask(draft);
            }
          }}
          placeholder="Ask a question…"
          className="liquid-glass-pill max-h-28 min-h-10 flex-1 resize-none rounded-2xl px-3 py-2 text-[13px] text-on-hero placeholder:text-on-hero/50 focus:outline-none"
        />
        <button
          type="submit"
          disabled={!draft.trim() || chat.isPending}
          aria-label="Send question"
          className="btn-primary flex h-10 w-10 shrink-0 items-center justify-center rounded-full disabled:opacity-50"
        >
          <Icon name="send" size={16} />
        </button>
      </form>
    </section>
  );
}
