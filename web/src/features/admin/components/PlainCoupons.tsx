import { useState, type ReactNode } from "react";
import { Empty, ErrorNote, Loading } from "@/components/common/Feedback";
import {
  useCouponsQuery,
  useCreateCouponMutation,
  useRetireCouponMutation,
  useUpdateCouponMutation,
  type Coupon,
  type CouponDraft,
  type CouponScope,
  type MenuHeading,
} from "@/features/admin/adminApi";
import { cents, centsOrZero, count } from "@/features/admin/offerFields";
import { DiscountField } from "@/features/admin/components/DiscountField";
import type { DiscountKind } from "@/types";
import { errorMessage } from "@/services/apiClient";
import { money } from "@/utils/format";

/**
 * The plain coupons: one code each, typed by whoever was given it.
 *
 * Lifted out of the platform's coupon page so the restaurant's own settings
 * screen can show the same thing. Which coupons depends only on the scope
 * passed in -- the platform names a restaurant, the restaurant names
 * nothing and gets its own.
 *
 * Two columns of information per coupon, answering different questions. The
 * left is the offer -- the code a customer types and what it takes off. The
 * right is the outcome: how many orders used it and how much was given away,
 * which is the only honest way to judge a promotion. A code redeemed forty
 * times for ten cents each is not the same promotion as one redeemed twice
 * for fifty dollars, and a dashboard showing only the count would make them
 * look identical.
 *
 * WHAT CANNOT BE EDITED is the code. It has been given to customers, printed
 * on something, or read out over the phone; changing it would break every
 * copy of it in the world while the coupon went on existing. A new code is a
 * new coupon, and the row offers "Retire" for the old one.
 *
 * RETIRING IS NOT DELETING. Orders point at a coupon and keep their own
 * snapshot of the code and the amount, so a receipt from last month still
 * reads the same. Retiring takes it off this page and frees the code to be
 * created again -- which is how a seasonal promotion comes back.
 */
export function PlainCoupons({ scope }: { scope: CouponScope }) {
  const coupons = useCouponsQuery(scope);
  const currency = coupons.data?.currency ?? "USD";

  const [retire] = useRetireCouponMutation();
  const [update] = useUpdateCouponMutation();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [announcement, setAnnouncement] = useState("");

  const rows = coupons.data?.coupons ?? [];
  // Live promotions first: the question on opening this is almost always
  // "what is running right now". Not memoised -- two passes over at most a
  // couple of hundred rows, and the list is a new array on every render
  // anyway, which would make a memo on it a memo that never hits.
  const running = rows.filter(isRunning);
  const paused = rows.filter((coupon) => !isRunning(coupon));

  async function act(coupon: Coupon, what: () => Promise<unknown>, said: string) {
    setBusy(coupon.id);
    setError(null);
    try {
      await what();
      setAnnouncement(`${coupon.code} ${said}.`);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section aria-labelledby="coupons-heading">
      <h2 id="coupons-heading" className="sr-only">
        Coupons
      </h2>
      <ErrorNote message={error ?? (coupons.error ? errorMessage(coupons.error) : null)} />
      <p className="sr-only" aria-live="polite">
        {announcement}
      </p>

      {!adding && (
        <button type="button" className="btn-primary mb-7" onClick={() => setAdding(true)}>
          New coupon
        </button>
      )}
      {adding && (
        <CouponForm
          scope={scope}
          currency={currency}
          headings={coupons.data?.item_types ?? []}
          onDone={(code) => {
            setAdding(false);
            if (code) setAnnouncement(`${code} created.`);
          }}
        />
      )}

      {coupons.isLoading ? (
        <Loading />
      ) : !rows.length ? (
        <Empty>No coupons yet. Create one and customers can use it straight away.</Empty>
      ) : (
        <>
          <Section title="Running" count={running.length}>
            {!running.length ? (
              <Empty>Nothing is running right now.</Empty>
            ) : (
              running.map((coupon) => (
                <Row
                  key={coupon.id}
                  coupon={coupon}
                  currency={currency}
                  busy={busy === coupon.id}
                  onPause={() =>
                    act(
                      coupon,
                      () =>
                        update({ scope, couponId: coupon.id, patch: { is_active: false } }).unwrap(),
                      "paused",
                    )
                  }
                  onRetire={() =>
                    act(coupon, () => retire({ scope, couponId: coupon.id }).unwrap(), "retired")
                  }
                />
              ))
            )}
          </Section>

          {paused.length > 0 && (
            <Section title="Not running" count={paused.length}>
              {paused.map((coupon) => (
                <Row
                  key={coupon.id}
                  coupon={coupon}
                  currency={currency}
                  busy={busy === coupon.id}
                  onResume={
                    coupon.is_active
                      ? undefined
                      : () =>
                          act(
                            coupon,
                            () =>
                              update({
                                scope,
                                couponId: coupon.id,
                                patch: { is_active: true },
                              }).unwrap(),
                            "switched back on",
                          )
                  }
                  onRetire={() =>
                    act(coupon, () => retire({ scope, couponId: coupon.id }).unwrap(), "retired")
                  }
                />
              ))}
            </Section>
          )}
        </>
      )}
    </section>
  );
}

/** Whether a customer typing this code right now would be given the discount.
 *
 *  Three things have to be true, and the screen has to agree with the server
 *  about all of them or a manager reads "running" beside a code nobody can
 *  use: it is switched on, it has started, and it has not ended. A fully
 *  redeemed coupon is deliberately still "running" -- it is doing exactly what
 *  it was set up to do, and the count beside it says so. */
function isRunning(coupon: Coupon): boolean {
  const now = Date.now();
  if (!coupon.is_active) return false;
  if (coupon.starts_at && new Date(coupon.starts_at).getTime() > now) return false;
  if (coupon.ends_at && new Date(coupon.ends_at).getTime() <= now) return false;
  return true;
}

/** What the coupon takes off, in the words a manager would use. */
function offer(coupon: Coupon, currency: string): string {
  const amount =
    coupon.discount_kind === "PERCENT"
      ? `${trim(coupon.discount_value / 100)}% off`
      : `${money(coupon.discount_value, currency)} off`;
  const ceiling =
    coupon.discount_kind === "PERCENT" && coupon.max_discount_minor
      ? `, up to ${money(coupon.max_discount_minor, currency)}`
      : "";
  return amount + ceiling;
}

/** 10 rather than 10.00, and 12.5 rather than 12.50. */
function trim(value: number): string {
  return String(Number(value.toFixed(2)));
}

/** The limits, as a sentence each. Only the ones that are set: a list of
 *  "no minimum, no cap, no end date" tells an operator nothing. */
function limits(coupon: Coupon, currency: string): string[] {
  const out: string[] = [];
  // First, because it changes what the percentage above it even means: 20%
  // off is 20% of the burgers, not of the order.
  if (coupon.applies_to.length > 0) {
    out.push(`On ${coupon.applies_to.map((heading) => heading.name).join(", ")}`);
  }
  if (coupon.min_subtotal_minor > 0) {
    out.push(`Orders of ${money(coupon.min_subtotal_minor, currency)} or more`);
  }
  if (coupon.max_redemptions !== null) {
    const left = Math.max(coupon.max_redemptions - coupon.redemptions, 0);
    out.push(`${coupon.max_redemptions} uses in total (${left} left)`);
  }
  if (coupon.max_per_customer !== null) {
    out.push(
      coupon.max_per_customer === 1
        ? "Once per customer"
        : `${coupon.max_per_customer} per customer`,
    );
  }
  if (coupon.starts_at) out.push(`From ${when(coupon.starts_at)}`);
  if (coupon.ends_at) out.push(`Until ${when(coupon.ends_at)}`);
  return out;
}

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
  });
}

function Section({ title, count, children }: { title: string; count: number; children: ReactNode }) {
  return (
    <section className="mb-[30px]">
      <h2 className="text-lg font-semibold">
        {title} <span className="tnum font-normal text-muted">({count})</span>
      </h2>
      <div>{children}</div>
    </section>
  );
}

function Row({
  coupon,
  currency,
  busy,
  onPause,
  onResume,
  onRetire,
}: {
  coupon: Coupon;
  currency: string;
  busy: boolean;
  onPause?: () => void;
  onResume?: () => void;
  onRetire: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const rules = limits(coupon, currency);

  return (
    <div className="flex flex-col gap-4 border-b border-hairline py-[18px] last:border-b-0 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {/* Monospaced, because a code is read character by character and
              typed back: an l and a 1 have to look different. */}
          <span className="font-mono text-[15px] font-bold tracking-[.5px]">{coupon.code}</span>
          <span className="text-[15px] font-semibold">{offer(coupon, currency)}</span>
          {coupon.applies_to.length > 0 && (
            <span className="rounded-full border border-hairline px-2 py-[2px] text-[11px] uppercase tracking-[.8px] text-muted">
              Part of the menu
            </span>
          )}
          {!coupon.is_active && (
            <span className="rounded-full bg-[rgb(var(--ze-shortcut-ground))] px-2 py-[2px] text-[11px] uppercase tracking-[.8px] text-muted">
              Paused
            </span>
          )}
        </p>
        {coupon.description && (
          <p className="mt-1 text-caption text-muted">{coupon.description}</p>
        )}
        {rules.length > 0 && (
          <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-caption text-muted">
            {rules.map((rule) => (
              <li key={rule}>{rule}</li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-5 sm:flex-col sm:items-end sm:gap-1">
        {/* What it has actually cost. The count alone would make a promotion
            used forty times for pennies look like a success. */}
        <p className="tnum text-caption text-muted">
          {coupon.redemptions === 0
            ? "Not used yet"
            : `${coupon.redemptions} ${coupon.redemptions === 1 ? "order" : "orders"} · ${money(
                coupon.discounted_minor,
                currency,
              )} given`}
        </p>

        <div className="flex items-center gap-3">
          {onResume && (
            <button type="button" className="btn-quiet" disabled={busy} onClick={onResume}>
              Switch on
            </button>
          )}
          {onPause && (
            <button type="button" className="btn-quiet" disabled={busy} onClick={onPause}>
              Pause
            </button>
          )}
          {/* Two taps, because retiring stops a code customers may be
              holding. Not a modal: the confirmation replaces the button it
              came from, where the eye already is. */}
          {confirming ? (
            <>
              <button type="button" className="btn-danger" disabled={busy} onClick={onRetire}>
                Retire {coupon.code}
              </button>
              <button type="button" className="btn-quiet" onClick={() => setConfirming(false)}>
                Keep
              </button>
            </>
          ) : (
            <button type="button" className="btn-quiet" onClick={() => setConfirming(true)}>
              Retire
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/** The new-coupon form.
 *
 *  Money is typed in the units a person says out loud -- "5" for five dollars,
 *  "10" for ten percent -- and converted here. A manager typing 500 into a
 *  field labelled "$" and creating a five-hundred-dollar coupon is the one
 *  mistake this screen could make that costs real money, so the fields say
 *  what they take and the conversion happens in one place.
 */
function CouponForm({
  scope,
  currency,
  headings,
  onDone,
}: {
  scope: CouponScope;
  currency: string;
  headings: MenuHeading[];
  onDone: (code: string | null) => void;
}) {
  const [create, { isLoading }] = useCreateCouponMutation();
  const [error, setError] = useState<string | null>(null);

  const [code, setCode] = useState("");
  const [description, setDescription] = useState("");
  // A switch and one box. Two boxes side by side, fill in one, read to the
  // restaurants as two discounts at once; a switch says plainly that it is
  // one or the other. Dollars first: "$2 off" is what most of them run.
  //
  // Strings, not numbers: an empty number input is the difference between
  // "not set" and zero, and a number state cannot hold that difference.
  const [kind, setKind] = useState<DiscountKind>("AMOUNT");
  const [value, setValue] = useState("");
  const [ceiling, setCeiling] = useState("");
  const [minimum, setMinimum] = useState("");
  const [total, setTotal] = useState("");
  const [perCustomer, setPerCustomer] = useState("");
  const [endsAt, setEndsAt] = useState("");
  // Which headings it applies to. Empty is the common case: the whole cart.
  const [appliesTo, setAppliesTo] = useState<string[]>([]);

  const isPercent = kind === "PERCENT";

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);

    // Said here so the answer arrives before the round trip. The API is
    // still the one that decides.
    const worth = isPercent ? cents(value) : centsOrZero(value);
    if (worth === null) {
      setError(isPercent ? "Enter the percentage off, like 10." : "Enter the amount off, like 2.");
      return;
    }
    if (isPercent && worth > 10_000) {
      setError("A percentage off can be at most 100.");
      return;
    }

    const draft: CouponDraft = {
      code,
      description: description.trim() || null,
      // Percent as basis points, an amount as minor units -- the units the
      // database and every other discount in the system use. Converted in
      // one place so the two readings cannot drift apart.
      percent_off_bps: isPercent ? worth : null,
      amount_off_minor: isPercent ? null : worth,
      // A ceiling means nothing on a flat amount, and the API refuses one.
      max_discount_minor: isPercent ? cents(ceiling) : null,
      min_subtotal_minor: cents(minimum) ?? 0,
      applies_to: appliesTo,
      max_redemptions: count(total),
      max_per_customer: count(perCustomer),
      // A local datetime from the browser, sent as an instant.
      ends_at: endsAt ? new Date(endsAt).toISOString() : null,
    };

    try {
      const created = await create({ scope, draft }).unwrap();
      onDone(created.code);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <form
      onSubmit={submit}
      className="mb-8 rounded-[14px] border border-hairline p-5 sm:p-6"
      // Stops a half-typed code being lost to the browser's own validation
      // bubbles, which say less than the messages above do.
      noValidate
    >
      <h2 className="text-lg font-semibold">New coupon</h2>
      <ErrorNote message={error} />

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="label">Code</span>
          <input
            className="field mt-[7px] font-mono uppercase"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="WELCOME10"
            maxLength={32}
            autoCapitalize="characters"
            required
          />
          <span className="mt-1 block text-caption text-muted">
            Letters and numbers. Customers can type it in any case.
          </span>
        </label>

        <label className="block">
          <span className="label">What it is for</span>
          <input
            className="field mt-[7px]"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Launch offer"
            maxLength={160}
          />
          <span className="mt-1 block text-caption text-muted">
            Shown to the customer when the code is accepted.
          </span>
        </label>

        <DiscountField
          className="sm:col-span-2"
          kind={kind}
          onKindChange={setKind}
          value={value}
          onValueChange={setValue}
          currency={currency}
        />

        {/* What it comes off. Empty is the common case and the default: a
            launch offer is meant to be spent on anything. Naming headings
            makes it "20% off burgers", and only those lines are discounted. */}
        {headings.length > 0 && (
          <label className="block sm:col-span-2">
            <span className="label">Which items</span>
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
                ? "Tap the menu categories it works on. Nothing chosen: it comes off the whole order."
                : "Only items in these categories are discounted."}
            </span>
          </label>
        )}

        {/* Only a percentage can grow with the order, so only a percentage
            needs a cap. A dollar amount is its own cap. */}
        {isPercent && (
          <label className="block">
            <span className="label">Maximum discount (optional)</span>
            <input
              className="field tnum mt-[7px]"
              value={ceiling}
              onChange={(e) => setCeiling(e.target.value)}
              inputMode="decimal"
              placeholder="No maximum"
            />
            <span className="mt-1 block text-caption text-muted">
              In {currency}. For example 20% off, but never more than 10.
            </span>
          </label>
        )}

        <label className="block">
          <span className="label">Minimum order total (optional)</span>
          <input
            className="field tnum mt-[7px]"
            value={minimum}
            onChange={(e) => setMinimum(e.target.value)}
            inputMode="decimal"
            placeholder="No minimum"
          />
          <span className="mt-1 block text-caption text-muted">
            In {currency}. The code only works on an order at least this big.
          </span>
        </label>

        <label className="block">
          <span className="label">How many times in total (optional)</span>
          <input
            className="field tnum mt-[7px]"
            value={total}
            onChange={(e) => setTotal(e.target.value)}
            inputMode="numeric"
            placeholder="Unlimited"
          />
        </label>

        <label className="block">
          <span className="label">How many times per customer (optional)</span>
          <input
            className="field tnum mt-[7px]"
            value={perCustomer}
            onChange={(e) => setPerCustomer(e.target.value)}
            inputMode="numeric"
            placeholder="Unlimited"
          />
        </label>

        <label className="block">
          <span className="label">End date (optional)</span>
          <input
            className="field mt-[7px]"
            type="datetime-local"
            value={endsAt}
            onChange={(e) => setEndsAt(e.target.value)}
          />
          <span className="mt-1 block text-caption text-muted">
            Leave empty to run until you pause it.
          </span>
        </label>
      </div>

      <div className="mt-5 flex items-center gap-3">
        <button type="submit" className="btn-primary" disabled={isLoading}>
          {isLoading ? "Creating…" : "Create coupon"}
        </button>
        <button type="button" className="btn-quiet" onClick={() => onDone(null)}>
          Cancel
        </button>
      </div>
    </form>
  );
}
