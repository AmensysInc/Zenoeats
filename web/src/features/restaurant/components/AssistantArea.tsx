import { useCallback, useEffect, useRef, useState } from "react";
import { ErrorNote, SettingsHeading, Spinner } from "@/components/common/Feedback";
import { Icon } from "@/components/common/icons";
import {
  useAssistantAskMutation,
  useAssistantDoMutation,
  type AssistantReply,
} from "@/features/restaurant/restaurantApi";
import { speak, speechOutSupported, useSpeech } from "@/features/restaurant/useSpeech";
import { errorMessage } from "@/services/apiClient";

/**
 * Say what you want doing, by typing or out loud, and confirm it before it
 * happens.
 *
 * NOTHING HAPPENS UNTIL THE BUTTON IS PRESSED. A question answers straight
 * away; anything that would change the menu comes back as a sentence and a
 * Yes, and anything that cannot be undone says so on a red one. That is the
 * whole safety story, and it is why speaking to it is safe to offer at all:
 * a misheard word costs a line on a screen, not a price customers pay.
 *
 * VOICE IS THE BROWSER'S OWN -- no key, no vendor, no audio sent anywhere we
 * chose. The person who knows the fryer is empty has their hands full, and
 * "86 the fries" said out loud is the difference between updating the menu
 * now and updating it after the rush. Where the browser has no recognition
 * there is simply no microphone button, never one that does nothing.
 *
 * THE TRANSCRIPT SCROLLS, the composer does not. A kitchen tablet is short,
 * and a conversation that pushed the box off the bottom of the card would
 * be a conversation you could not continue.
 */

type Turn = {
  id: number;
  mine: boolean;
  text: string;
  proposal?: Record<string, unknown> | null;
  destructive?: boolean;
  /** Set once acted on, so the buttons go and the outcome reads as history. */
  settled?: string;
};

let nextId = 1;

/** Shown once, on an empty panel. Chips rather than prose: the fastest way
 *  to learn what a box like this understands is to press one. */
const EXAMPLES = [
  "show me the menu",
  "check inventory",
  "we're out of fries",
  "change the price of fries to 2.50",
];

const SPEAK_BACK_KEY = "ze:assistant-speaks";

export function AssistantArea() {
  const [ask, asking] = useAssistantAskMutation();
  const [confirm, confirming] = useAssistantDoMutation();

  const [turns, setTurns] = useState<Turn[]>([]);
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [speaksBack, setSpeaksBack] = useState(() => {
    try {
      return window.localStorage.getItem(SPEAK_BACK_KEY) === "yes";
    } catch {
      return false;
    }
  });

  const box = useRef<HTMLInputElement>(null);
  const scroller = useRef<HTMLDivElement>(null);

  function say(turn: Omit<Turn, "id">) {
    setTurns((all) => [...all, { ...turn, id: nextId++ }]);
  }

  const send = useCallback(
    async (text: string) => {
      const sentence = text.trim();
      if (!sentence) return;
      setError(null);
      setTyped("");
      say({ mine: true, text: sentence });
      try {
        const reply: AssistantReply = await ask({ text: sentence }).unwrap();
        say({
          mine: false,
          text: reply.text,
          proposal: reply.proposal,
          destructive: reply.destructive,
        });
        // Read back only the first line: a menu listing read aloud in full
        // is a minute of somebody's life.
        if (speaksBack) speak(reply.text.split("\n")[0] ?? reply.text);
      } catch (e) {
        setError(errorMessage(e));
      }
    },
    [ask, speaksBack],
  );

  const voice = useSpeech(send);

  // Keep the newest turn in view without moving the page itself.
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns, voice.listening]);

  async function agree(turn: Turn) {
    setError(null);
    try {
      const done = await confirm({ proposal: turn.proposal! }).unwrap();
      setTurns((all) =>
        all.map((one) => (one.id === turn.id ? { ...one, settled: done.text } : one)),
      );
      if (speaksBack) speak(done.text);
    } catch (e) {
      setError(errorMessage(e));
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
        /* A browser with storage switched off still gets the setting for
           this visit; it simply will not be remembered. */
      }
      return next;
    });
  }

  const busy = asking.isLoading || confirming.isLoading;

  return (
    <section
      id="settings-assistant"
      className="card mb-6 scroll-mt-6"
      aria-labelledby="settings-assistant-heading"
    >
      <SettingsHeading
        id="settings-assistant-heading"
        title="Assistant"
        subtitle="Tell it what to do, typed or out loud."
      />

      <div className="mt-6">
        <ErrorNote message={error ?? voice.problem} />

        {/* The conversation. Fixed height so the composer never leaves the
            screen on a short tablet, and scrolled to the newest turn. */}
        <div
          ref={scroller}
          className="max-h-[360px] min-h-[120px] overflow-y-auto rounded-panel border border-hairline bg-paper px-4 py-4"
          role="log"
          aria-live="polite"
          aria-label="Conversation with the assistant"
        >
          {turns.length === 0 ? (
            <div>
              <p className="text-sm text-muted">
                Tell me what to do and I&rsquo;ll show you the change before
                making it.
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                {EXAMPLES.map((example) => (
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
                <li
                  key={turn.id}
                  className={turn.mine ? "flex justify-end" : "flex flex-col items-start"}
                >
                  <div
                    className={
                      turn.mine
                        ? "max-w-[80%] rounded-panel bg-ink px-4 py-2.5 text-sm text-white"
                        : `max-w-[92%] rounded-panel border px-4 py-3 text-sm ${
                            turn.destructive && !turn.settled
                              ? "border-danger bg-dangerSoft"
                              : "border-hairline bg-surface"
                          }`
                    }
                  >
                    {/* Plain text with real newlines -- a menu listing is a
                        list. Rendered pre-wrap because none of it is markup. */}
                    <span className="whitespace-pre-wrap">{turn.text}</span>
                  </div>

                  {turn.proposal && !turn.settled && (
                    <div className="mt-2 flex flex-wrap items-center gap-3">
                      <button
                        type="button"
                        className={
                          turn.destructive ? "btn-danger btn-compact" : "btn-primary btn-compact"
                        }
                        disabled={busy}
                        onClick={() => agree(turn)}
                      >
                        {confirming.isLoading && <Spinner />}
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
            </ol>
          )}

          {voice.listening && (
            <p className="mt-3 flex items-center gap-2 text-sm text-muted">
              <span className="h-2 w-2 animate-pulse rounded-full bg-danger" />
              {voice.heard ? `“${voice.heard}”` : "Listening…"}
            </p>
          )}
        </div>

        <form
          className="mt-3 flex flex-wrap items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void send(typed);
          }}
        >
          <label className="min-w-[200px] flex-1">
            <span className="sr-only">What would you like to do?</span>
            <input
              ref={box}
              className="field"
              value={voice.listening && voice.heard ? voice.heard : typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={voice.listening ? "Listening…" : "we're out of fries"}
              maxLength={400}
              disabled={busy || voice.listening}
            />
          </label>

          {/* Absent, not disabled, where the browser cannot listen. */}
          {voice.supported && (
            <button
              type="button"
              className={voice.listening ? "btn-danger btn-compact" : "btn-quiet btn-compact"}
              onClick={voice.listening ? voice.stop : voice.start}
              disabled={busy}
              aria-pressed={voice.listening}
              aria-label={voice.listening ? "Stop listening" : "Speak instead of typing"}
            >
              <Icon name="mic" className="h-4 w-4" />
              {voice.listening ? "Stop" : "Speak"}
            </button>
          )}

          <button type="submit" className="btn-primary btn-compact" disabled={busy || !typed.trim()}>
            {asking.isLoading ? "Reading…" : "Send"}
          </button>
        </form>

        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <p className="text-caption text-muted">
            Refunds and staff changes stay on their own screens, and nothing is
            done in bulk.
          </p>
          {speechOutSupported() && (
            <label className="flex items-center gap-2 text-caption text-muted">
              <input type="checkbox" checked={speaksBack} onChange={toggleSpeech} />
              Read answers aloud
            </label>
          )}
        </div>
      </div>
    </section>
  );
}
