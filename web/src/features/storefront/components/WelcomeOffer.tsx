import { useState } from "react";
import { ErrorNote } from "@/components/common/Feedback";
import { Icon } from "@/components/common/icons";
import {
  useStartPhoneVerificationMutation,
  useVerifyPhoneMutation,
  useWelcomeCodesQuery,
  useWelcomeOfferQuery,
  type WelcomeCode,
} from "@/features/storefront/storefrontApi";
import { formatPhone } from "@/features/storefront/contact";
import { errorMessage } from "@/services/apiClient";
import { money } from "@/utils/format";

/**
 * "Add your number and we'll send you a welcome offer."
 *
 * Shown to a signed-in customer who has not claimed one, on the restaurant's
 * own terms: the restaurant decides whether there is an offer at all, and
 * this panel is absent where there is not.
 *
 * WHY A CODE TO THE HANDSET and not just a number typed in a box. An offer
 * given for typing a number is an offer given for typing anything, and the
 * tenth made-up number costs the restaurant ten discounts. What the code
 * proves is that somebody is holding the phone.
 *
 * WHEN IT CANNOT BE SENT the panel says so rather than offering a button
 * that fails: this deployment has no SMS vendor yet (backend
 * services/sms.py), and a verification code that never arrives looks to the
 * customer exactly like a broken site.
 *
 * The code, once earned, belongs to this customer alone -- it is refused to
 * anybody else at the checkout -- so it is shown in full here, which is the
 * only place it exists.
 */
export function WelcomeOffer() {
  const offer = useWelcomeOfferQuery();
  const codes = useWelcomeCodesQuery();

  const held = codes.data ?? [];
  // Nothing to say: no offer running, or this is a guest.
  if (!offer.data?.headline && held.length === 0) return null;

  return (
    <section aria-labelledby="welcome-heading" className="mb-5">
      <h2 id="welcome-heading" className="sr-only">
        Your welcome offer
      </h2>
      {held.map((code) => (
        <HeldCode key={code.code} code={code} />
      ))}
      {offer.data?.can_earn && <Claim offer={offer.data} />}
    </section>
  );
}

/** A code this customer holds, shown in full: this is the only place it
 *  exists, and it is useless to anybody else. */
function HeldCode({ code }: { code: WelcomeCode }) {
  const off =
    code.discount_kind === "PERCENT"
      ? `${code.discount_value / 100}% off`
      : `${money(code.discount_value, "USD")} off`;

  return (
    <div
      className={`mb-4 rounded-panel border p-5 sm:p-6 ${
        code.spent ? "border-hairline bg-surface" : "border-success bg-successSoft"
      }`}
    >
      <p className="eyebrow text-muted">{code.description ?? "Your welcome offer"}</p>
      <p className="mt-2 font-mono text-[26px] font-bold tracking-[1px]">{code.code}</p>
      <p className="mt-2 text-sm text-muted">
        {code.spent ? (
          // Said rather than hidden: somebody looking for the code they were
          // given wants to be told they used it, not left wondering.
          "Used — thanks for your order."
        ) : (
          <>
            {off}
            {code.min_subtotal_minor > 0
              ? ` on orders over ${money(code.min_subtotal_minor, "USD")}`
              : ""}
            . Type it at checkout.
            {code.ends_at ? ` Good until ${day(code.ends_at)}.` : ""}
          </>
        )}
      </p>
    </div>
  );
}

function day(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

/** The two steps: a number, then the code texted to it. */
function Claim({ offer }: { offer: { headline: string | null; sms_available: boolean } }) {
  const [start, starting] = useStartPhoneVerificationMutation();
  const [verify, verifying] = useVerifyPhoneMutation();

  // Which half of the flow we are in. "sent" rather than a step number: there
  // are two states and naming them is clearer than counting them.
  const [sent, setSent] = useState(false);
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function askForCode(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await start({ phone }).unwrap();
      setSent(true);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function submitCode(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await verify({ code }).unwrap();
      // The list above refetches on its own: verifying invalidates the tag.
      setDone(true);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  if (done) return null;

  return (
    <div className="rounded-panel border border-hairline bg-surface p-5 sm:p-6">
      <p className="flex items-start gap-2.5 font-semibold">
        <Icon name="tag" className="mt-0.5 h-4 w-4 shrink-0" />
        {offer.headline}
      </p>

      {!offer.sms_available ? (
        // Honest about the deployment rather than offering a button that
        // cannot work. The reason is ours, so the sentence says so.
        <p className="mt-2 text-sm text-muted">
          We can’t send verification codes just now, so this offer isn’t
          available yet. Nothing for you to fix — check back soon.
        </p>
      ) : (
        <>
          <p className="mt-2 text-sm text-muted">
            Add your mobile number and verify it, and the code is yours.
          </p>
          <ErrorNote message={error} className="mt-3" />

          {!sent ? (
            <form onSubmit={askForCode} className="mt-4 flex flex-wrap items-end gap-3" noValidate>
              <label className="min-w-[200px] flex-1">
                <span className="label">Mobile number</span>
                <input
                  className="field mt-[7px]"
                  value={phone}
                  onChange={(e) => setPhone(formatPhone(e.target.value))}
                  type="tel"
                  autoComplete="tel"
                  inputMode="tel"
                  placeholder="+1 (580) 226-7925"
                  required
                />
              </label>
              <button type="submit" className="btn-primary" disabled={starting.isLoading}>
                {starting.isLoading ? "Sending…" : "Text me a code"}
              </button>
            </form>
          ) : (
            <form onSubmit={submitCode} className="mt-4 flex flex-wrap items-end gap-3" noValidate>
              <label className="min-w-[160px] flex-1">
                <span className="label">The 6-digit code</span>
                <input
                  className="field tnum mt-[7px] tracking-[3px]"
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                  // Lets both phone platforms offer the code from the message
                  // itself, which is most of why anybody finishes this flow.
                  autoComplete="one-time-code"
                  inputMode="numeric"
                  maxLength={6}
                  required
                  autoFocus
                />
              </label>
              <button type="submit" className="btn-primary" disabled={verifying.isLoading}>
                {verifying.isLoading ? "Checking…" : "Verify"}
              </button>
              <button
                type="button"
                className="link"
                onClick={() => {
                  setSent(false);
                  setCode("");
                  setError(null);
                }}
              >
                Use a different number
              </button>
            </form>
          )}
        </>
      )}
    </div>
  );
}
