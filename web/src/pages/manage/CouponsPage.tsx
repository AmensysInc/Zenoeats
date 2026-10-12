import { useState, type ReactNode } from "react";
import { useAppSelector } from "@/app/hooks";
import { Empty, ErrorNote, Loading } from "@/components/common/Feedback";
import { PageTitle } from "@/components/layout/Shell";
import { selectSession } from "@/features/session/sessionSlice";
import { canEditMenu } from "@/features/restaurant/nav";
import { ManageShell } from "@/features/restaurant/components/ManageShell";
import {
  useCouponsQuery,
  useCreateCouponMutation,
  useRetireCouponMutation,
  useUpdateCouponMutation,
  type Coupon,
  type CouponDraft,
} from "@/features/restaurant/restaurantApi";
import { errorMessage } from "@/services/apiClient";
import { money } from "@/utils/format";

/**
 * Promotion codes: what exists, what it is worth, and what it has cost.
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
 * IT support reads this screen without the controls, the same split the menu
 * has: answering "why did this customer get a discount" needs the rows, and
 * inventing a discount is the restaurant's call.
 */
export function CouponsPage() {
  const coupons = useCouponsQuery();
  const currency = coupons.data?.currency ?? "USD";
  const { roleCode } = useAppSelector(selectSession);
  const canEdit = canEditMenu(roleCode);

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
    <ManageShell>
      <PageTitle
        title="Coupons"
        subtitle={
          canEdit
            ? "Codes a customer can type at checkout."
            : "The promotion codes this restaurant is running."
        }
      />

      <ErrorNote message={error ?? (coupons.error ? errorMessage(coupons.error) : null)} />
      <p className="sr-only" aria-live="polite">
        {announcement}
      </p>

      {canEdit && !adding && (
        <button type="button" className="btn-primary mb-7" onClick={() => setAdding(true)}>
          New coupon
        </button>
      )}
      {canEdit && adding && (
        <CouponForm
          currency={currency}
          onDone={(code) => {
            setAdding(false);
            if (code) setAnnouncement(`${code} created.`);
          }}
        />
      )}

      {coupons.isLoading ? (
        <Loading />
      ) : !rows.length ? (
        <Empty>
          No coupons yet.{canEdit ? " Create one and customers can use it straight away." : ""}
        </Empty>
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
                  canEdit={canEdit}
                  onPause={() =>
                    act(coupon, () => update({ id: coupon.id, patch: { is_active: false } }).unwrap(), "paused")
                  }
                  onRetire={() => act(coupon, () => retire(coupon.id).unwrap(), "retired")}
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
                  canEdit={canEdit}
                  onResume={
                    coupon.is_active
                      ? undefined
                      : () =>
                          act(
                            coupon,
                            () => update({ id: coupon.id, patch: { is_active: true } }).unwrap(),
                            "switched back on",
                          )
                  }
                  onRetire={() => act(coupon, () => retire(coupon.id).unwrap(), "retired")}
                />
              ))}
            </Section>
          )}
        </>
      )}
    </ManageShell>
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
 *  "no minimum, no cap, no end date" tells a manager nothing. */
function limits(coupon: Coupon, currency: string): string[] {
  const out: string[] = [];
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
  canEdit,
  onPause,
  onResume,
  onRetire,
}: {
  coupon: Coupon;
  currency: string;
  busy: boolean;
  canEdit: boolean;
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

        {canEdit && (
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
        )}
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
  currency,
  onDone,
}: {
  currency: string;
  onDone: (code: string | null) => void;
}) {
  const [create, { isLoading }] = useCreateCouponMutation();
  const [error, setError] = useState<string | null>(null);

  const [code, setCode] = useState("");
  const [description, setDescription] = useState("");
  const [kind, setKind] = useState<"PERCENT" | "AMOUNT">("PERCENT");
  // Strings, not numbers: an empty number input is the difference between
  // "no limit" and zero, and a number state cannot hold that difference.
  const [value, setValue] = useState("");
  const [ceiling, setCeiling] = useState("");
  const [minimum, setMinimum] = useState("");
  const [total, setTotal] = useState("");
  const [perCustomer, setPerCustomer] = useState("");
  const [endsAt, setEndsAt] = useState("");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);

    const amount = Number(value);
    if (!Number.isFinite(amount) || amount <= 0) {
      setError("Say how much the code takes off.");
      return;
    }

    const draft: CouponDraft = {
      code,
      description: description.trim() || null,
      discount_kind: kind,
      // Percent is sent as basis points, an amount as minor units. One place,
      // so the two readings cannot drift apart.
      discount_value: kind === "PERCENT" ? Math.round(amount * 100) : Math.round(amount * 100),
      max_discount_minor: kind === "PERCENT" ? cents(ceiling) : null,
      min_subtotal_minor: cents(minimum) ?? 0,
      max_redemptions: count(total),
      max_per_customer: count(perCustomer),
      // A local datetime from the browser, sent as an instant.
      ends_at: endsAt ? new Date(endsAt).toISOString() : null,
    };

    try {
      const created = await create(draft).unwrap();
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

        <label className="block">
          <span className="label">Takes off</span>
          <div className="mt-[7px] flex gap-2">
            <select
              className="field w-auto"
              value={kind}
              onChange={(e) => setKind(e.target.value as "PERCENT" | "AMOUNT")}
            >
              <option value="PERCENT">A percentage</option>
              <option value="AMOUNT">An amount</option>
            </select>
            <input
              className="field tnum"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              inputMode="decimal"
              placeholder={kind === "PERCENT" ? "10" : "5.00"}
              aria-label={kind === "PERCENT" ? "Percent off" : `Amount off in ${currency}`}
              required
            />
            <span className="self-center text-[15px] text-muted">
              {kind === "PERCENT" ? "%" : currency}
            </span>
          </div>
        </label>

        {kind === "PERCENT" && (
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

/** A typed amount as minor units, or null for an empty field. */
function cents(typed: string): number | null {
  const value = Number(typed.trim());
  if (!typed.trim() || !Number.isFinite(value) || value <= 0) return null;
  return Math.round(value * 100);
}

/** A typed whole number, or null for an empty field. */
function count(typed: string): number | null {
  const value = Number(typed.trim());
  if (!typed.trim() || !Number.isInteger(value) || value <= 0) return null;
  return value;
}
