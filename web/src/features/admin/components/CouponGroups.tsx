import { useState } from "react";
import { Empty, ErrorNote, Loading } from "@/components/common/Feedback";
import {
  useCouponGroupsQuery,
  useCreateCouponGroupMutation,
  useRetireCouponGroupMutation,
  useUpdateCouponGroupMutation,
  type CouponGroup,
  type CouponGroupDraft,
  type GroupCode,
  type MenuHeading,
} from "@/features/admin/adminApi";
import { cents, count } from "@/features/admin/offerFields";
import { errorMessage } from "@/services/apiClient";
import { money } from "@/utils/format";

/**
 * One offer, handed out as numbered codes.
 *
 * "MORE" with ten uses mints MORE1 through MORE10: ten codes carrying the
 * same discount, each good once, each given to one person.
 *
 * WHY NOT ONE CODE WITH A LIMIT OF TEN. Because that cannot answer "which
 * ten people". Everybody types the same thing and the orders are
 * indistinguishable, where a numbered code is traceable to whoever was
 * handed it -- which is the column on the right of every row below, and the
 * reason this section exists at all.
 *
 * WHAT CANNOT BE CHANGED afterwards is the prefix and the count. Both have
 * left the building: the codes are printed, texted or read out, so renaming
 * them would break every copy, and lowering the count would have to decide
 * which holders to cancel. Pausing and retiring work on the whole group,
 * codes included, because the codes are what customers actually hold.
 */
export function CouponGroups({ restaurantId }: { restaurantId: string }) {
  const groups = useCouponGroupsQuery(restaurantId);
  const currency = groups.data?.currency ?? "USD";

  const [update] = useUpdateCouponGroupMutation();
  const [retire] = useRetireCouponGroupMutation();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [announcement, setAnnouncement] = useState("");

  const rows = groups.data?.groups ?? [];

  async function act(group: CouponGroup, what: () => Promise<unknown>, said: string) {
    setBusy(group.id);
    setError(null);
    try {
      await what();
      setAnnouncement(`${group.code_prefix} ${said}.`);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section aria-labelledby="groups-heading" className="mt-10 border-t border-hairline pt-8">
      <h2 id="groups-heading" className="text-lg font-semibold">
        Group coupons
      </h2>
      <p className="mt-1 max-w-[70ch] text-caption text-muted">
        One offer handed out as numbered codes. A prefix of MORE with ten uses
        gives MORE1 to MORE10 — each good once, each for one person, so you can
        see which of them came back.
      </p>

      <ErrorNote
        message={error ?? (groups.error ? errorMessage(groups.error) : null)}
        className="mt-4"
      />
      <p className="sr-only" aria-live="polite">
        {announcement}
      </p>

      {!adding && (
        <button type="button" className="btn-primary mb-6 mt-5" onClick={() => setAdding(true)}>
          New group coupon
        </button>
      )}
      {adding && (
        <GroupForm
          restaurantId={restaurantId}
          currency={currency}
          headings={groups.data?.item_types ?? []}
          onDone={(prefix) => {
            setAdding(false);
            if (prefix) setAnnouncement(`${prefix} created.`);
          }}
        />
      )}

      {groups.isLoading ? (
        <Loading />
      ) : !rows.length ? (
        <Empty>No group coupons yet.</Empty>
      ) : (
        rows.map((group) => (
          <GroupRow
            key={group.id}
            group={group}
            currency={currency}
            busy={busy === group.id}
            onToggle={() =>
              act(
                group,
                () =>
                  update({
                    id: restaurantId,
                    groupId: group.id,
                    patch: { is_active: !group.is_active },
                  }).unwrap(),
                group.is_active ? "paused" : "switched back on",
              )
            }
            onRetire={() =>
              act(
                group,
                () => retire({ id: restaurantId, groupId: group.id }).unwrap(),
                "retired",
              )
            }
          />
        ))
      )}
    </section>
  );
}

function GroupRow({
  group,
  currency,
  busy,
  onToggle,
  onRetire,
}: {
  group: CouponGroup;
  currency: string;
  busy: boolean;
  onToggle: () => void;
  onRetire: () => void;
}) {
  // Codes collapsed by default: a group of five hundred would otherwise be
  // five hundred rows between this group and the next one.
  const [open, setOpen] = useState(false);

  return (
    <article className="mb-4 rounded-[14px] border border-hairline p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="font-mono text-[17px] font-semibold">
            {group.code_prefix}1–{group.code_prefix}
            {group.uses}
          </p>
          <p className="mt-1 text-caption text-muted">
            {worth(group, currency)}
            {group.description ? ` · ${group.description}` : ""}
            {group.is_active ? "" : " · paused"}
          </p>
        </div>

        {/* The outcome, which is what judging a promotion needs: a count on
            its own cannot tell forty ten-cent redemptions from two fifty
            dollar ones. */}
        <div className="text-right">
          <p className="tnum text-[17px] font-semibold">
            {group.used} of {group.issued} used
          </p>
          <p className="tnum mt-1 text-caption text-muted">
            {money(group.discounted_minor, currency)} given away
          </p>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-4">
        <button
          type="button"
          className="link"
          aria-expanded={open}
          onClick={() => setOpen((shown) => !shown)}
        >
          {open ? "Hide codes" : `Show ${group.issued} codes`}
        </button>
        <button type="button" className="link" disabled={busy} onClick={onToggle}>
          {group.is_active ? "Pause" : "Switch on"}
        </button>
        <button type="button" className="link text-danger" disabled={busy} onClick={onRetire}>
          Retire
        </button>
      </div>

      {open && <CodeTable codes={group.codes} currency={currency} />}
    </article>
  );
}

/** Every code, and who spent it.
 *
 *  A table rather than a list: four facts per row that line up down the
 *  page, which is what makes "which of these went out and came back"
 *  answerable at a glance. In its own scroll container because a long
 *  customer name must not widen the page on a phone. */
function CodeTable({ codes, currency }: { codes: GroupCode[]; currency: string }) {
  return (
    <div className="mt-4 overflow-x-auto">
      <table className="w-full min-w-[460px] border-collapse text-sm">
        <thead>
          <tr className="border-b border-hairline text-left text-caption text-muted">
            <th scope="col" className="py-2 pr-4 font-medium">Code</th>
            <th scope="col" className="py-2 pr-4 font-medium">Used</th>
            <th scope="col" className="py-2 pr-4 font-medium">By</th>
            <th scope="col" className="py-2 text-right font-medium">Took off</th>
          </tr>
        </thead>
        <tbody>
          {codes.map((code) => (
            <tr key={code.id} className="border-b border-hairline/60">
              <td className="py-2 pr-4 font-mono">{code.code}</td>
              <td className="py-2 pr-4">
                {code.redemptions ? day(code.redeemed_at) : <span className="text-muted">—</span>}
              </td>
              <td className="py-2 pr-4">
                {/* A guest orders with no account behind it, so there is
                    genuinely nobody to name rather than a lookup that
                    failed. Said as "a guest" rather than left blank. */}
                {code.redemptions
                  ? code.redeemed_by ?? <span className="text-muted">a guest</span>
                  : <span className="text-muted">—</span>}
              </td>
              <td className="tnum py-2 text-right">
                {code.redemptions ? money(code.discounted_minor, currency) : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function day(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/** What one code takes off, in the operator's own words. */
function worth(group: CouponGroup, currency: string): string {
  const off =
    group.discount_kind === "PERCENT"
      ? `${group.discount_value / 100}% off`
      : `${money(group.discount_value, currency)} off`;
  const scope = group.applies_to.length
    ? ` ${group.applies_to.map((heading) => heading.name).join(", ")}`
    : "";
  return `${off}${scope}`;
}

function GroupForm({
  restaurantId,
  currency,
  headings,
  onDone,
}: {
  restaurantId: string;
  currency: string;
  headings: MenuHeading[];
  onDone: (prefix: string | null) => void;
}) {
  const [create, { isLoading }] = useCreateCouponGroupMutation();
  const [error, setError] = useState<string | null>(null);

  const [prefix, setPrefix] = useState("");
  const [uses, setUses] = useState("");
  const [description, setDescription] = useState("");
  const [percent, setPercent] = useState("");
  const [amount, setAmount] = useState("");
  const [ceiling, setCeiling] = useState("");
  const [minimum, setMinimum] = useState("");
  const [startsAt, setStartsAt] = useState("");
  const [endsAt, setEndsAt] = useState("");
  const [appliesTo, setAppliesTo] = useState<string[]>([]);

  const hasPercent = percent.trim() !== "";
  const hasAmount = amount.trim() !== "";
  const howMany = count(uses);
  const clean = prefix.replace(/\s+/g, "").toUpperCase();

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
    if (!howMany) {
      setError("Say how many codes to make.");
      return;
    }

    const draft: CouponGroupDraft = {
      code_prefix: clean,
      uses: howMany,
      description: description.trim() || null,
      percent_off_bps: hasPercent ? cents(percent) : null,
      amount_off_minor: hasAmount ? cents(amount) : null,
      max_discount_minor: hasPercent ? cents(ceiling) : null,
      min_subtotal_minor: cents(minimum) ?? 0,
      applies_to: appliesTo,
      starts_at: startsAt ? new Date(startsAt).toISOString() : null,
      ends_at: endsAt ? new Date(endsAt).toISOString() : null,
    };

    try {
      const created = await create({ id: restaurantId, draft }).unwrap();
      onDone(created.code_prefix);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <form
      onSubmit={submit}
      className="mb-8 rounded-[14px] border border-hairline p-5 sm:p-6"
      noValidate
    >
      <h3 className="text-lg font-semibold">New group coupon</h3>
      <ErrorNote message={error} />

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="label">Prefix</span>
          <input
            className="field mt-[7px] font-mono uppercase"
            value={prefix}
            onChange={(e) => setPrefix(e.target.value)}
            placeholder="MORE"
            maxLength={24}
            autoCapitalize="characters"
            required
          />
          <span className="mt-1 block text-caption text-muted">
            Letters and numbers, not ending in a number — the codes end in one.
          </span>
        </label>

        <label className="block">
          <span className="label">How many codes</span>
          <input
            className="field tnum mt-[7px]"
            value={uses}
            onChange={(e) => setUses(e.target.value)}
            inputMode="numeric"
            placeholder="10"
            required
          />
          {/* Shown rather than described: "MORE1 to MORE10" answers the
              question the two fields above raise, before the save. */}
          <span className="mt-1 block text-caption text-muted">
            {clean && howMany
              ? `Makes ${clean}1 to ${clean}${howMany}, each good once.`
              : "Each code is good once, so this is how many people it is for."}
          </span>
        </label>

        <label className="block sm:col-span-2">
          <span className="label">What it is for</span>
          <input
            className="field mt-[7px]"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Street team handout"
            maxLength={160}
          />
          <span className="mt-1 block text-caption text-muted">
            Shown to the customer when a code is accepted.
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
              placeholder="10"
              aria-label="Percent off"
            />
            <span className="self-center text-[15px] text-muted">%</span>
          </div>
          <span className="mt-1 block text-caption text-muted">
            {hasAmount ? "Clear the amount to use a percentage." : "Or fill in an amount."}
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
            {hasPercent ? "Clear the percentage to use a flat amount." : "One of these two."}
          </span>
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
            <span className="mt-1 block text-caption text-muted">
              20% off is a different offer on a $10 order and a $400 one.
            </span>
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

        <label className="block">
          <span className="label">Starts</span>
          <input
            type="datetime-local"
            className="field mt-[7px]"
            value={startsAt}
            onChange={(e) => setStartsAt(e.target.value)}
          />
          <span className="mt-1 block text-caption text-muted">
            Leave empty to start as soon as it is saved.
          </span>
        </label>

        <label className="block">
          <span className="label">Ends</span>
          <input
            type="datetime-local"
            className="field mt-[7px]"
            value={endsAt}
            onChange={(e) => setEndsAt(e.target.value)}
          />
          <span className="mt-1 block text-caption text-muted">
            Leave empty to run until it is paused.
          </span>
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
                ? "Nothing chosen: the codes come off the whole order."
                : "Only lines under these headings are discounted."}
            </span>
          </label>
        )}
      </div>

      <div className="mt-5 flex flex-wrap gap-3">
        <button type="submit" className="btn-primary" disabled={isLoading}>
          {isLoading ? "Making codes…" : "Create and mint codes"}
        </button>
        <button type="button" className="btn-quiet" onClick={() => onDone(null)}>
          Cancel
        </button>
      </div>
    </form>
  );
}
