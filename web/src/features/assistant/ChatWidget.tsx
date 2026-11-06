import { useCallback, useEffect, useRef, useState } from "react";
import { ErrorNote, Spinner } from "@/components/common/Feedback";
import { Icon } from "@/components/common/icons";
import { speak, speechOutSupported, useSpeech } from "@/features/restaurant/useSpeech";
import type { ChatTurn } from "@/features/restaurant/restaurantApi";
import { errorMessage } from "@/services/apiClient";

/**
 * The assistant, as a button in the corner that opens a conversation.
 *
 * One widget for all three places it appears -- the restaurant dashboard, the
 * platform admin and the storefront. What it can do is entirely the server's
 * business; this is the conversation, the box, and the Yes/No a proposal
 * needs. Only the dashboard ever gets a proposal, because only its tools make
 * them.
 *
 * NOTHING HAPPENS UNTIL THE BUTTON IS PRESSED. A proposal shows its sentence
 * and a Yes; a destructive one says so on a red button.
 *
 * KEPT FOR THE VISIT. The conversation is held in sessionStorage, so moving
 * between pages does not throw it away, and closing the tab does.
 */

export type ChatReply = {
  text: string;
  proposal?: Record<string, unknown> | null;
  destructive?: boolean;
};

type Turn = {
  id: number;
  mine: boolean;
  text: string;
  proposal?: Record<string, unknown> | null;
  destructive?: boolean;
  /** Set once acted on, so the buttons go and the outcome reads as history. */
  settled?: string;
};

type Props = {
  /** Where the conversation is kept for the visit. One per site. */
  storageKey: string;
  title: string;
  greeting: string;
  examples: string[];
  placeholder: string;
  footnote?: string;
  send: (text: string, history: ChatTurn[]) => Promise<ChatReply>;
  /** Carry out a proposal. Absent where nothing is ever proposed. */
  confirm?: (proposal: Record<string, unknown>) => Promise<string>;
  /** Offer the microphone and reading answers aloud. */
  voice?: boolean;
  /** Raised clear of a bar fixed to the bottom of the screen on phones. */
  aboveMobileBar?: boolean;
};

const SPEAK_BACK_KEY = "ze:assistant-speaks";
// What goes back to the server with each message. The server keeps fewer.
const HISTORY_SENT = 12;

function load(key: string): Turn[] {
  try {
    const raw = window.sessionStorage.getItem(key);
    const turns = raw ? (JSON.parse(raw) as Turn[]) : [];
    return Array.isArray(turns) ? turns : [];
  } catch {
    return [];
  }
}

export function ChatWidget({
  storageKey, title, greeting, examples, placeholder, footnote, send: ask, confirm,
  voice: withVoice = false, aboveMobileBar = false,
}: Props) {
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState<Turn[]>(() => load(storageKey));
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [speaksBack, setSpeaksBack] = useState(() => {
    try {
      return window.localStorage.getItem(SPEAK_BACK_KEY) === "yes";
    } catch {
      return false;
    }
  });

  const box = useRef<HTMLInputElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const nextId = useRef(turns.reduce((max, t) => Math.max(max, t.id), 0) + 1);

  useEffect(() => {
    try {
      window.sessionStorage.setItem(storageKey, JSON.stringify(turns.slice(-40)));
    } catch {
      /* Storage switched off: the conversation lasts as long as the page. */
    }
  }, [storageKey, turns]);

  const say = useCallback((turn: Omit<Turn, "id">) => {
    const id = nextId.current++;
    setTurns((all) => [...all, { ...turn, id }]);
  }, []);

  const send = useCallback(
    async (text: string) => {
      const sentence = text.trim();
      if (!sentence || busy) return;
      setError(null);
      setTyped("");
      const history: ChatTurn[] = turns
        .slice(-HISTORY_SENT)
        .map((t) => ({ role: t.mine ? "user" : "assistant", text: t.text }));
      say({ mine: true, text: sentence });
      setBusy(true);
      try {
        const reply = await ask(sentence, history);
        say({ mine: false, text: reply.text, proposal: reply.proposal, destructive: reply.destructive });
        // Only the first line aloud: a menu read out in full is a minute of
        // somebody's life.
        if (withVoice && speaksBack) speak(reply.text.split("\n")[0] ?? reply.text);
      } catch (e) {
        setError(errorMessage(e));
      } finally {
        setBusy(false);
      }
    },
    [ask, busy, say, speaksBack, turns, withVoice],
  );

  const voice = useSpeech(send);

  // The newest turn in view, without moving the page behind the panel.
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns, busy, open, voice.listening]);

  // Back in the box once a reply is in: the box is disabled while waiting,
  // which takes the cursor out of it, and the next question would otherwise
  // be typed into nothing.
  useEffect(() => {
    if (open && !busy) box.current?.focus();
  }, [open, busy]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  async function agree(turn: Turn) {
    if (!confirm || !turn.proposal) return;
    setError(null);
    setBusy(true);
    try {
      const done = await confirm(turn.proposal);
      setTurns((all) => all.map((one) => (one.id === turn.id ? { ...one, settled: done } : one)));
      if (withVoice && speaksBack) speak(done);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  function decline(turn: Turn) {
    setTurns((all) =>
      all.map((one) => (one.id === turn.id ? { ...one, settled: "Left as it was." } : one)),
    );
  }

  function toggleSpeech() {
    setSpeaksBack((on) => {
      const next = !on;
      try {
        window.localStorage.setItem(SPEAK_BACK_KEY, next ? "yes" : "no");
      } catch {
        /* Kept for this visit only. */
      }
      return next;
    });
  }

  const lift = aboveMobileBar ? "bottom-[92px] sm:bottom-6" : "bottom-4 sm:bottom-6";
  const listening = withVoice && voice.listening;

  return (
    <>
      {open && (
        <section
          role="dialog"
          aria-label={title}
          className={`fixed inset-x-3 z-nav flex max-h-[min(600px,calc(100dvh-140px))] flex-col overflow-hidden rounded-[18px] border border-hairline bg-surface shadow-[0_18px_50px_#25262033] sm:inset-x-auto sm:right-6 sm:w-[390px] ${
            aboveMobileBar ? "bottom-[160px] sm:bottom-24" : "bottom-[84px] sm:bottom-24"
          }`}
        >
          <header className="flex items-center justify-between gap-3 border-b border-hairline px-4 py-3">
            <p className="flex items-center gap-2 font-semibold">
              <Icon name="chat" className="h-5 w-5 text-brick" />
              {title}
            </p>
            <div className="flex items-center gap-1">
              {turns.length > 0 && (
                <button
                  type="button"
                  className="link text-caption"
                  onClick={() => setTurns([])}
                  disabled={busy}
                >
                  New chat
                </button>
              )}
              <button
                type="button"
                className="rounded-full p-1.5 text-muted hover:bg-paper hover:text-ink"
                onClick={() => setOpen(false)}
                aria-label="Close"
              >
                <Icon name="close" className="h-5 w-5" />
              </button>
            </div>
          </header>

          <div
            ref={scroller}
            className="min-h-[180px] flex-1 overflow-y-auto bg-paper px-4 py-4"
            role="log"
            aria-live="polite"
            aria-label={`Conversation with ${title}`}
          >
            {turns.length === 0 ? (
              <div>
                <p className="text-sm text-muted">{greeting}</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {examples.map((example) => (
                    <button
                      key={example}
                      type="button"
                      className="chip"
                      disabled={busy}
                      onClick={() => void send(example)}
                    >
                      {example}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <ol className="flex flex-col gap-3">
                {turns.map((turn) => (
                  <li key={turn.id} className={turn.mine ? "flex justify-end" : "flex flex-col items-start"}>
                    <div
                      className={
                        turn.mine
                          ? "max-w-[85%] rounded-panel bg-ink px-3.5 py-2.5 text-sm text-white"
                          : `max-w-[92%] rounded-panel border px-3.5 py-2.5 text-sm ${
                              turn.destructive && !turn.settled
                                ? "border-danger bg-dangerSoft"
                                : "border-hairline bg-surface"
                            }`
                      }
                    >
                      {/* Plain text with real newlines; none of it is markup. */}
                      <span className="whitespace-pre-wrap [overflow-wrap:anywhere]">{turn.text}</span>
                    </div>

                    {turn.proposal && !turn.settled && confirm && (
                      <div className="mt-2 flex flex-wrap items-center gap-3">
                        <button
                          type="button"
                          className={turn.destructive ? "btn-danger btn-compact" : "btn-primary btn-compact"}
                          disabled={busy}
                          onClick={() => void agree(turn)}
                        >
                          {turn.destructive ? "Yes — it cannot be undone" : "Yes, do it"}
                        </button>
                        <button type="button" className="link" disabled={busy} onClick={() => decline(turn)}>
                          No
                        </button>
                      </div>
                    )}

                    {turn.settled && (
                      <p className="mt-1.5 flex items-center gap-1.5 text-caption text-muted">
                        <Icon name="check" className="h-3.5 w-3.5" />
                        {turn.settled}
                      </p>
                    )}
                  </li>
                ))}
                {busy && (
                  <li className="flex items-center gap-2 text-sm text-muted">
                    <Spinner /> Thinking…
                  </li>
                )}
              </ol>
            )}

            {listening && (
              <p className="mt-3 flex items-center gap-2 text-sm text-muted">
                <span className="h-2 w-2 animate-pulse rounded-full bg-danger" />
                {voice.heard ? `“${voice.heard}”` : "Listening…"}
              </p>
            )}
          </div>

          <div className="border-t border-hairline px-3 py-3">
            <ErrorNote message={error ?? (withVoice ? voice.problem : null)} />
            <form
              className="flex items-center gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                void send(typed);
              }}
            >
              <label className="min-w-0 flex-1">
                <span className="sr-only">Message</span>
                <input
                  ref={box}
                  className="field"
                  value={listening && voice.heard ? voice.heard : typed}
                  onChange={(e) => setTyped(e.target.value)}
                  placeholder={listening ? "Listening…" : placeholder}
                  maxLength={400}
                  disabled={busy || listening}
                />
              </label>
              {/* Absent, not disabled, where the browser cannot listen. */}
              {withVoice && voice.supported && (
                <button
                  type="button"
                  className={listening ? "btn-danger btn-compact" : "btn-quiet btn-compact"}
                  onClick={listening ? voice.stop : voice.start}
                  disabled={busy}
                  aria-pressed={listening}
                  aria-label={listening ? "Stop listening" : "Speak instead of typing"}
                >
                  <Icon name="mic" className="h-4 w-4" />
                </button>
              )}
              <button type="submit" className="btn-primary btn-compact" disabled={busy || !typed.trim()}>
                Send
              </button>
            </form>
            {(footnote || (withVoice && speechOutSupported())) && (
              <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                {footnote && <p className="text-caption text-muted">{footnote}</p>}
                {withVoice && speechOutSupported() && (
                  <label className="flex items-center gap-2 text-caption text-muted">
                    <input type="checkbox" checked={speaksBack} onChange={toggleSpeech} />
                    Read answers aloud
                  </label>
                )}
              </div>
            )}
          </div>
        </section>
      )}

      <button
        type="button"
        onClick={() => setOpen((was) => !was)}
        className={`fixed right-4 z-nav flex h-14 w-14 items-center justify-center rounded-full bg-brick text-white shadow-[0_10px_28px_#25262040] transition-transform duration-color hover:scale-105 sm:right-6 ${lift}`}
        aria-expanded={open}
        aria-label={open ? `Close ${title}` : `Open ${title}`}
      >
        <Icon name={open ? "close" : "chat"} className="h-6 w-6" />
      </button>
    </>
  );
}
