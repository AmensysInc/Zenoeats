import { useState } from "react";
import { ErrorNote, Loading } from "@/components/common/Feedback";
import {
  useRetireSignupOfferMutation,
  useSetSignupOfferMutation,
  useSignupOfferQuery,
  type MenuHeading,
  type SignupOffer as Offer,
  type SignupOfferDraft,
} from "@/features/admin/adminApi";
import { cents, count } from "@/features/admin/offerFields";
import { errorMessage } from "@/services/apiClient";
import { money } from "@/utils/format";

/**
 * The welcome offer: one code per new customer, earned by proving a mobile
 * number.
 *
 * A SINGLETON, not a list. Two live welcome offers would both fire on the
 * same sign-up and hand one account two codes, so this section edits the one
 * there is or creates it.
 *
 * WHAT IT NEEDS THAT NOTHING ELSE HERE DOES is an SMS vendor. Verifying a
 * number means texting a code to it, and this deployment has none yet, so
 * the panel says so plainly rather than letting somebody switch on an offer
 * whose only path to being claimed cannot run.
 *
 * EDITING DOES NOT TOUCH CODES ALREADY ISSUED. A customer was given theirs
 * on the terms running at the time, and rewriting those afterwards would
 * change what somebody was promised. An edit decides what the next customer
 * gets.
 */
export function SignupOffer({ restaurantId }: { restaurantId: string }) {
  const state = useSignupOfferQuery(restaurantId);
  const [retire] = useRetireSignupOfferMutation();
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");

  const offer = state.data?.offer ?? null;
  const currency = state.data?.currency ?? "USD";
  const canSend = state.data?.sms_configured ?? false;

  return (
    <section aria-labelledby="signup-offer-heading" className="mt-10 border-t border-hairline pt-8">
      <h2 id="signup-offer-heading" className="text-lg font-semibold">
        Sign-on coupon
      </h2>
      <p className="mt-1 max-w-[70ch] text-caption text-muted">
        One code for each new customer, given when they add a mobile number and
        verify it. The code belongs to them — nobody they pass it to can use it.
      </p>

      <ErrorNote
        message={error ?? (state.error ? errorMessage(state.error) : null)}
        className="mt-4"
      />
      <p className="sr-only" aria-live="polite">
        {announcement}
      </p>

      {!canSend && (
        // The honest state of the deployment, said where somebody is about
        // to switch the offer on rather than discovered by a customer whose
        // code never arrives.
        <p className="note-warning mt-4">
          Text messaging is not configured here, so nobody can verify a number
          and no code can be claimed. The offer can be set up now and will
          start working once an SMS provider is connected — which for US
          numbers also needs A2P 10DLC registration.
        </p>
      )}

      {state.isLoading ? (
        <Loading />
      ) : editing || !offer ? (
        <OfferForm
          restaurantId={restaurantId}
          currency={currency}
          headings={state.data?.item_types ?? []}
          offer={offer}
          onDone={(saved) => {
            setEditing(false);
            if (saved) setAnnouncement("Sign-on coupon saved.");
          }}
        />
      ) : (
        <article className="mt-5 rounded-[14px] border border-hairline p-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0">
              <p className="font-mono text-[17px] font-semibold">
                {offer.code_prefix}
                <span className="text-muted">······</span>
              </p>
              <p className="mt-1 text-caption text-muted">
                {worth(offer, currency)}
                {offer.valid_days ? ` · ${offer.valid_days} days to use it` : ""}
                {offer.is_active ? "" : " · paused"}
              </p>
            </div>
            <div className="text-right">
              <p className="tnum text-[17px] font-semibold">
                {offer.used} of {offer.issued} used
              </p>
              <p className="tnum mt-1 text-caption text-muted">
                {money(offer.discounted_minor, currency)} given away
              </p>
            </div>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-4">
            <button type="button" className="link" onClick={() => setEditing(true)}>
              Edit
            </button>
            <button
              type="button"
              className="link text-danger"
              onClick={async () => {
                setError(null);
                try {
                  await retire({ id: restaurantId }).unwrap();
                  setAnnouncement("Sign-on coupon ended.");
                } catch (e) {
                  setError(errorMessage(e));
                }
              }}
            >
              End this offer
            </button>
          </div>
          <p className="mt-3 text-caption text-muted">
            Ending it stops the codes already handed out, the same as retiring
            any other coupon. Orders that used one still read correctly.
          </p>
        </article>
      )}
    </section>
  );
}

function worth(offer: Offer, currency: string): string {
  const off =
    offer.discount_kind === "PERCENT"
      ? `${offer.discount_value / 100}% off`
      : `${money(offer.discount_value, currency)} off`;
  const scope = offer.applies_to.length
    ? ` ${offer.applies_to.map((heading) => heading.name).join(", ")}`
    : "";
  return `${off}${scope}`;
}

function OfferForm({
  restaurantId,
  currency,
  headings,
  offer,
  onDone,
}: {
  restaurantId: string;
  currency: string;
  headings: MenuHeading[];
  offer: Offer | null;
  onDone: (saved: boolean) => void;
}) {
  const [save, { isLoading }] = useSetSignupOfferMutation();
  const [error, setError] = useState<string | null>(null);

  const [prefix, setPrefix] = useState(offer?.code_prefix ?? "WELCOME");
  const [description, setDescription] = useState(offer?.description ?? "");
  const [percent, setPercent] = useState(
    offer?.discount_kind === "PERCENT" ? String(offer.discount_value / 100) : "",
  );
  const [amount, setAmount] = useState(
    offer?.discount_kind === "AMOUNT" ? String(offer.discount_value / 100) : "",
  );
  const [ceiling, setCeiling] = useState(
    offer?.max_discount_minor ? String(offer.max_discount_minor / 100) : "",
  );
  const [minimum, setMinimum] = useState(
    offer?.min_subtotal_minor ? String(offer.min_subtotal_minor / 100) : "",
  );
  const [validDays, setValidDays] = useState(
    offer?.valid_days ? String(offer.valid_days) : "30",
  );
  const [appliesTo, setAppliesTo] = useState<string[]>(
    offer?.applies_to.map((heading) => heading.id) ?? [],
  );

  const hasPercent = percent.trim() !== "";
  const hasAmount = amount.trim() !== "";

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    if (!hasPercent && !hasAmount) {
      setError("Fill in either a percentage off or an amount off.");
      return;
    }
    if (hasPercent && hasAmount) {
      setError(
        "Fill in a percentage off or an amount off, not both. " +
          "For a percentage with a limit, set the ceiling instead.",
      );
      return;
    }

    const draft: SignupOfferDraft = {
      code_prefix: prefix,
      description: description.trim() || null,
      percent_off_bps: hasPercent ? cents(percent) : null,
      amount_off_minor: hasAmount ? cents(amount) : null,
      max_discount_minor: hasPercent ? cents(ceiling) : null,
      min_subtotal_minor: cents(minimum) ?? 0,
      valid_days: count(validDays),
      applies_to: appliesTo,
      is_active: true,
    };

    try {
      await save({ id: restaurantId, draft }).unwrap();
      onDone(true);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <form onSubmit={submit} className="mt-5 rounded-[14px] border border-hairline p-5 sm:p-6" noValidate>
      <h3 className="text-lg font-semibold">
        {offer ? "Edit the sign-on coupon" : "Set up a sign-on coupon"}
      </h3>
      <ErrorNote message={error} />

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="label">Code starts with</span>
          <input
            className="field mt-[7px] font-mono uppercase"
            value={prefix}
            onChange={(e) => setPrefix(e.target.value)}
            maxLength={24}
            autoCapitalize="characters"
            required
          />
          {/* Random rather than numbered: WELCOME7 would tell its holder
              they were the seventh customer and invite a guess at WELCOME8. */}
          <span className="mt-1 block text-caption text-muted">
            Each customer gets this plus six random characters.
          </span>
        </label>

        <label className="block">
          <span className="label">Days to use it</span>
          <input
            className="field tnum mt-[7px]"
            value={validDays}
            onChange={(e) => setValidDays(e.target.value)}
            inputMode="numeric"
            placeholder="30"
          />
          <span className="mt-1 block text-caption text-muted">
            Counted from the day each customer earns theirs. Empty means no expiry.
          </span>
        </label>

        <label className="block sm:col-span-2">
          <span className="label">What it is for</span>
          <input
            className="field mt-[7px]"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Welcome to Jr's"
            maxLength={160}
          />
          <span className="mt-1 block text-caption text-muted">
            Shown to the customer on the offer and when the code is accepted.
          </span>
        </label>

        <label className="block">
          <span className="label">Percentage off</span>
          <div className="mt-[7px] flex gap-2">
            <input
              className={`field tnum ${hasAmount ? "opacity-50" : ""}`}
              value={percent}
              onChange={(e) => setPercent(e.target.value)}
              inputMode="decimal"
              placeholder="15"
              aria-label="Percent off"
            />
            <span className="self-center text-[15px] text-muted">%</span>
          </div>
        </label>

        <label className="block">
          <span className="label">Amount off</span>
          <div className="mt-[7px] flex gap-2">
            <input
              className={`field tnum ${hasPercent ? "opacity-50" : ""}`}
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              inputMode="decimal"
              placeholder="5.00"
              aria-label={`Amount off in ${currency}`}
            />
            <span className="self-center text-[15px] text-muted">{currency}</span>
          </div>
        </label>

        {hasPercent && (
          <label className="block">
            <span className="label">Most it can take off</span>
            <div className="mt-[7px] flex gap-2">
              <input
                className="field tnum"
                value={ceiling}
                onChange={(e) => setCeiling(e.target.value)}
                inputMode="decimal"
                placeholder="Optional"
                aria-label={`Ceiling in ${currency}`}
              />
              <span className="self-center text-[15px] text-muted">{currency}</span>
            </div>
          </label>
        )}

        <label className="block">
          <span className="label">Minimum spend</span>
          <div className="mt-[7px] flex gap-2">
            <input
              className="field tnum"
              value={minimum}
              onChange={(e) => setMinimum(e.target.value)}
              inputMode="decimal"
              placeholder="Optional"
              aria-label={`Minimum spend in ${currency}`}
            />
            <span className="self-center text-[15px] text-muted">{currency}</span>
          </div>
        </label>

        {headings.length > 0 && (
          <label className="block sm:col-span-2">
            <span className="label">Applies to</span>
            <div className="mt-[7px] flex flex-wrap gap-2">
              {headings.map((heading) => {
                const on = appliesTo.includes(heading.id);
                return (
                  <button
                    key={heading.id}
                    type="button"
                    aria-pressed={on}
                    onClick={() =>
                      setAppliesTo((chosen) =>
                        on ? chosen.filter((id) => id !== heading.id) : [...chosen, heading.id],
                      )
                    }
                    className={`rounded-status border px-3 py-[7px] text-caption transition-colors duration-color ${
                      on
                        ? "border-ink bg-ink text-white"
                        : "border-hairline text-ink hover:border-ink"
                    }`}
                  >
                    {heading.name}
                  </button>
                );
              })}
            </div>
            <span className="mt-1 block text-caption text-muted">
              {appliesTo.length === 0
                ? "Nothing chosen: it comes off the whole order."
                : "Only lines under these headings are discounted."}
            </span>
          </label>
        )}
      </div>

      {offer && (
        <p className="mt-4 text-caption text-muted">
          Codes already handed out keep the terms they were given on. This
          decides what the next customer gets.
        </p>
      )}

      <div className="mt-5 flex flex-wrap gap-3">
        <button type="submit" className="btn-primary" disabled={isLoading}>
          {isLoading ? "Saving…" : offer ? "Save changes" : "Set it up"}
        </button>
        {offer && (
          <button type="button" className="btn-quiet" onClick={() => onDone(false)}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
