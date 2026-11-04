import { useRef, useState } from "react";
import { ErrorNote, SettingsHeading, Spinner } from "@/components/common/Feedback";
import {
  useAssistantAskMutation,
  useAssistantDoMutation,
  type AssistantReply,
} from "@/features/restaurant/restaurantApi";
import { errorMessage } from "@/services/apiClient";

/**
 * Say what you want doing, and confirm it before it happens.
 *
 * "add a cheeseburger to Burgers for 5.99", "we're out of fries", "check
 * inventory" -- the things an admin would rather type than click through
 * four screens for.
 *
 * NOTHING HAPPENS UNTIL THE BUTTON IS PRESSED. A question answers straight
 * away; anything that would change the menu comes back as a sentence and a
 * Yes. That is the whole safety story, and it is why this is usable at all:
 * the assistant can misread, and misreading costs a line on a screen rather
 * than a price customers pay.
 *
 * IT SHOWS THE RESOLVED DISH, not the word that was typed -- "Jr's French
 * Fries" where the admin said "fries" -- because that is where a wrong
 * match gets noticed.
 *
 * No model and no API key: the sentence is read by the server
 * (services/assistant/language.py). An unrecognised one says so and lists
 * what it does know, which beats a confident wrong answer.
 */

type Turn = {
  id: number;
  mine: boolean;
  text: string;
  proposal?: Record<string, unknown> | null;
  destructive?: boolean;
  /** Set once a proposal has been acted on, so the button goes away and the
   *  outcome reads as history rather than as something still pending. */
  settled?: string;
};

let nextId = 1;

export function AssistantArea() {
  const [ask, asking] = useAssistantAskMutation();
  const [confirm, confirming] = useAssistantDoMutation();

  const [turns, setTurns] = useState<Turn[]>([]);
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLInputElement>(null);

  function say(turn: Omit<Turn, "id">) {
    setTurns((all) => [...all, { ...turn, id: nextId++ }]);
  }

  async function send(event: React.FormEvent) {
    event.preventDefault();
    const text = typed.trim();
    if (!text) return;

    setError(null);
    setTyped("");
    say({ mine: true, text });
    try {
      const reply: AssistantReply = await ask({ text }).unwrap();
      say({
        mine: false,
        text: reply.text,
        proposal: reply.proposal,
        destructive: reply.destructive,
      });
    } catch (e) {
      setError(errorMessage(e));
    }
    box.current?.focus();
  }

  async function agree(turn: Turn) {
    setError(null);
    try {
      const done = await confirm({ proposal: turn.proposal! }).unwrap();
      setTurns((all) =>
        all.map((one) => (one.id === turn.id ? { ...one, settled: done.text } : one)),
      );
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  function decline(turn: Turn) {
    setTurns((all) =>
      all.map((one) => (one.id === turn.id ? { ...one, settled: "Left as it was." } : one)),
    );
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
        subtitle="Tell it what to do, in your own words."
      />

      <div className="mt-6">
        <ErrorNote message={error} />

        {turns.length === 0 ? (
          <p className="text-sm text-muted">
            Try <em>add a cheeseburger to Burgers for 5.99</em>, <em>we&rsquo;re out
            of fries</em>, or <em>check inventory</em>. Nothing changes until you
            say yes.
          </p>
        ) : (
          <ol className="flex flex-col gap-3" aria-live="polite">
            {turns.map((turn) => (
              <li key={turn.id}>
                <div
                  className={
                    turn.mine
                      ? "ml-auto max-w-[80%] rounded-panel bg-ink px-4 py-2.5 text-sm text-white"
                      : "max-w-[90%] rounded-panel border border-hairline bg-surface px-4 py-3 text-sm"
                  }
                >
                  {/* The server sends plain text with real newlines -- a menu
                      listing is a list. Rendered as pre-wrap rather than as
                      markup, because none of it is markup. */}
                  <span className="whitespace-pre-wrap">{turn.text}</span>
                </div>

                {turn.proposal && !turn.settled && (
                  <div className="mt-2 flex flex-wrap items-center gap-3">
                    {/* Red and blunt for anything that cannot be taken
                        back, so agreeing to a cancellation never looks
                        like agreeing to a price change. */}
                    <button
                      type="button"
                      className={
                        turn.destructive
                          ? "btn-primary btn-compact !bg-danger"
                          : "btn-primary btn-compact"
                      }
                      disabled={busy}
                      onClick={() => agree(turn)}
                    >
                      {confirming.isLoading && <Spinner />}
                      {turn.destructive ? "Yes, do it — it cannot be undone" : "Yes, do it"}
                    </button>
                    <button
                      type="button"
                      className="link"
                      disabled={busy}
                      onClick={() => decline(turn)}
                    >
                      No
                    </button>
                  </div>
                )}

                {turn.settled && (
                  <p className="mt-2 text-caption text-muted">{turn.settled}</p>
                )}
              </li>
            ))}
          </ol>
        )}

        <form onSubmit={send} className="mt-5 flex flex-wrap items-end gap-3">
          <label className="min-w-[220px] flex-1">
            <span className="sr-only">What would you like to do?</span>
            <input
              ref={box}
              className="field"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder="we're out of fries"
              maxLength={400}
              disabled={busy}
            />
          </label>
          <button type="submit" className="btn-quiet" disabled={busy || !typed.trim()}>
            {asking.isLoading ? "Reading…" : "Send"}
          </button>
        </form>

        <p className="mt-3 text-caption text-muted">
          Refunds and staff changes stay on their own screens, and nothing is
          done in bulk.
        </p>
      </div>
    </section>
  );
}
