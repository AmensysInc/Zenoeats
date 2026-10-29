import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { Empty, ErrorNote, Loading } from "@/components/common/Feedback";
import { Icon } from "@/components/common/icons";
import { PageTitle } from "@/components/layout/Shell";
import { AdminShell } from "@/features/admin/components/AdminShell";
import {
  useCouponsQuery,
  useCreateCouponMutation,
  useRetireCouponMutation,
  useUpdateCouponMutation,
  type Coupon,
  type CouponDraft,
  type MenuHeading,
} from "@/features/admin/adminApi";
import { errorMessage } from "@/services/apiClient";
import { money } from "@/utils/format";
import { cents, count } from "@/features/admin/offerFields";
import { CouponGroups } from "@/features/admin/components/CouponGroups";

/**
 * One restaurant's promotion codes: what exists, what it is worth, and what
 * it has cost.
 *
 * A platform screen, because a coupon is a ZenoEats decision rather than a
 * tenant's -- the restaurant's own portal has no coupon tab, and no endpoint
 * behind one. Reached from the restaurant's row on /admin.
 *
 * Two columns of information per coupon, and they answer different
 * questions. The left is the offer -- the code a customer types and what it
 * takes off. The right is the outcome: how many orders used it and how much
 * was given away, which is the only honest way to judge a promotion. A code
 * redeemed forty times for ten cents each is not the same promotion as one
 * redeemed twice for fifty dollars, and a dashboard that showed only the
 * count would make them look identical.
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
 *
 * Every write here is audited to the operator who made it: a discount is
 * money, and "who set this up" is a question that gets asked afterwards.
 */
export function AdminCouponsPage() {
  const { id = "" } = useParams<{ id: string }>();
  const coupons = useCouponsQuery(id);
  const currency = coupons.data?.currency ?? "USD";

  const [retire] = useRetireCouponMutation();
  const [update] = useUpdateCouponMutation();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [announcement, setAnnouncement] = useState("");

  const rows = coupons.data?.coupons ?? [];
  // Live promotions first: the question on opening this page is almost always
  // "what is running right now". Not memoised -- it is two passes over at
  // most a couple of hundred rows, and the list is a new array on every
  // render anyway, which would make a memo on it a memo that never hits.
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
    <AdminShell>
      <Link
        to="/admin"
        className="mb-4 inline-flex min-h-[40px] items-center gap-2 text-caption text-muted hover:text-ink"
      >
        <Icon name="back" className="h-4 w-4" />
        Restaurants
      </Link>
      <PageTitle
        title={coupons.data?.restaurant_name ?? "…"}
        subtitle="Codes a customer can type at this restaurant's checkout"
      />

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
          restaurantId={id}
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
                        update({
                          id,
                          couponId: coupon.id,
                          patch: { is_active: false },
                        }).unwrap(),
                      "paused",
                    )
                  }
                  onRetire={() =>
                    act(coupon, () => retire({ id, couponId: coupon.id }).unwrap(), "retired")
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
                                id,
                                couponId: coupon.id,
                                patch: { is_active: true },
                              }).unwrap(),
                            "switched back on",
                          )
                  }
                  onRetire={() =>
                    act(coupon, () => retire({ id, couponId: coupon.id }).unwrap(), "retired")
                  }
                />
              ))}
            </Section>
          )}
        </>
      )}

      {/* One offer handed out as numbered codes. Its own section rather than
          more rows above: a group is one promotion however many codes it
          minted, and listing five hundred of them beside the hand-made
          coupons would bury both. */}
      <CouponGroups restaurantId={id} />
    </AdminShell>
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
  restaurantId,
  currency,
  headings,
  onDone,
}: {
  restaurantId: string;
  currency: string;
  headings: MenuHeading[];
  onDone: (code: string | null) => void;
}) {
  const [create, { isLoading }] = useCreateCouponMutation();
  const [error, setError] = useState<string | null>(null);

  const [code, setCode] = useState("");
  const [description, setDescription] = useState("");
  // Two fields rather than a kind and a value. An operator thinks in one or
  // the other, and asking which they mean before letting them type it is a
  // question about our data model.
  //
  // Strings, not numbers: an empty number input is the difference between
  // "not set" and zero, and a number state cannot hold that difference.
  const [percent, setPercent] = useState("");
  const [amount, setAmount] = useState("");
  const [ceiling, setCeiling] = useState("");
  const [minimum, setMinimum] = useState("");
  const [total, setTotal] = useState("");
  const [perCustomer, setPerCustomer] = useState("");
  const [endsAt, setEndsAt] = useState("");
  // Which headings it applies to. Empty is the common case: the whole cart.
  const [appliesTo, setAppliesTo] = useState<string[]>([]);

  const hasPercent = percent.trim() !== "";
  const hasAmount = amount.trim() !== "";

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);

    // The same two rules the API enforces, said here so the answer arrives
    // before the round trip. The API is still the one that decides.
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

    const draft: CouponDraft = {
      code,
      description: description.trim() || null,
      // Percent as basis points, an amount as minor units -- the units the
      // database and every other discount in the system use. Converted in
      // one place so the two readings cannot drift apart.
      percent_off_bps: hasPercent ? cents(percent) : null,
      amount_off_minor: hasAmount ? cents(amount) : null,
      // A ceiling means nothing on a flat amount, and the API refuses one.
      max_discount_minor: hasPercent ? cents(ceiling) : null,
      min_subtotal_minor: cents(minimum) ?? 0,
      applies_to: appliesTo,
      max_redemptions: count(total),
      max_per_customer: count(perCustomer),
      // A local datetime from the browser, sent as an instant.
      ends_at: endsAt ? new Date(endsAt).toISOString() : null,
    };

    try {
      const created = await create({ id: restaurantId, draft }).unwrap();
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

        {/* Two fields, fill in one. Both are offered because an operator
            arrives thinking "10% off" or "$5 off" and should not have to
            answer a question about our data model first. Each dims while the
            other holds something, so the rule is visible before the save
            rather than explained by a refusal afterwards. */}
        <label className="block">
          <span className="label">Percentage off</span>
          <div className="mt-[7px] flex gap-2">
            <input
              className={`field tnum ${hasAmount ? "opacity-50" : ""}`}
              value={percent}
              onChange={(e) => setPercent(e.target.value)}
              inputMode="decimal"
              placeholder="10"
              aria-label="Percent off"
            />
            <span className="self-center text-[15px] text-muted">%</span>
          </div>
          <span className="mt-1 block text-caption text-muted">
            {hasAmount ? "Clear the amount to use a percentage." : "Or fill in an amount below."}
          </span>
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
          <span className="mt-1 block text-caption text-muted">
            {hasPercent
              ? "Clear the percentage to use a flat amount."
              : "One of these two is required."}
          </span>
        </label>

        {/* What it comes off. Empty is the common case and the default: a
            launch offer is meant to be spent on anything. Naming headings
            makes it "20% off burgers", and only those lines are discounted. */}
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
                ? "Nothing chosen: the code comes off the whole order."
                : "Only lines under these headings are discounted."}
            </span>
          </label>
        )}

        {!hasAmount && (
          <label className="block">
            <span className="label">Never more than</span>
            <input
              className="field tnum mt-[7px]"
              value={ceiling}
              onChange={(e) => setCeiling(e.target.value)}
              inputMode="decimal"
              placeholder="No ceiling"
            />
            <span className="mt-1 block text-caption text-muted">
              A percentage with no ceiling is a different offer on a large order.
            </span>
          </label>
        )}

        <label className="block">
          <span className="label">Minimum order</span>
          <input
            className="field tnum mt-[7px]"
            value={minimum}
            onChange={(e) => setMinimum(e.target.value)}
            inputMode="decimal"
            placeholder="No minimum"
          />
        </label>

        <label className="block">
          <span className="label">Total uses</span>
          <input
            className="field tnum mt-[7px]"
            value={total}
            onChange={(e) => setTotal(e.target.value)}
            inputMode="numeric"
            placeholder="Unlimited"
          />
        </label>

        <label className="block">
          <span className="label">Uses per customer</span>
          <input
            className="field tnum mt-[7px]"
            value={perCustomer}
            onChange={(e) => setPerCustomer(e.target.value)}
            inputMode="numeric"
            placeholder="Unlimited"
          />
        </label>

        <label className="block">
          <span className="label">Ends</span>
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
