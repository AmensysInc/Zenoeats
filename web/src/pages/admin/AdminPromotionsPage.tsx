import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Empty, ErrorNote, Loading } from "@/components/common/Feedback";
import { Icon } from "@/components/common/icons";
import { PageTitle } from "@/components/layout/Shell";
import { AdminShell } from "@/features/admin/components/AdminShell";
import {
  useCreateOfferMutation,
  useCreateRewardMutation,
  useOffersQuery,
  useRetireOfferMutation,
  useRetireRewardMutation,
  useRewardsQuery,
  useUpdateOfferMutation,
  useUpdateRewardMutation,
  type AdminOffer,
  type LoyaltyProgram,
  type MenuDish,
  type MenuHeading,
} from "@/features/admin/adminApi";
import { errorMessage } from "@/services/apiClient";
import { money } from "@/utils/format";

/**
 * One restaurant's promotions: its loyalty programs, and the offers on its
 * storefront.
 *
 * A platform screen, because both are ZenoEats decisions rather than a
 * tenant's -- the restaurant's own portal has neither tab and no endpoint
 * behind one. Reached from the restaurant's row on /admin, beside Coupons.
 *
 * TWO SECTIONS RATHER THAN TWO PAGES. They are different shapes -- a stamp
 * card tied to the menu, and dated copy tied to nothing -- but an operator
 * setting up a restaurant's promotions is doing one job, and the third thing
 * they need, the coupons, is one click away. Splitting them put two entries
 * on the restaurant row for what reads as a single errand.
 *
 * WHAT IS DELIBERATELY ABSENT is any way to grant or adjust a customer's
 * stamps. The ledger is written by orders completing and refunds reversing
 * them; a button here would be a second way to move the same number, and the
 * two would disagree the first time somebody used it.
 */
export function AdminPromotionsPage() {
  const { id = "" } = useParams<{ id: string }>();
  const rewards = useRewardsQuery(id);
  const offers = useOffersQuery(id);

  // One error line and one announcement for the page: two sections doing the
  // same kind of work, and a message about either belongs in the same place
  // a reader is already looking.
  const [error, setError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");

  const loadError =
    (rewards.error ? errorMessage(rewards.error) : null) ??
    (offers.error ? errorMessage(offers.error) : null);

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
        title={rewards.data?.restaurant_name ?? offers.data?.restaurant_name ?? "…"}
        subtitle="What this restaurant gives away, and what it advertises"
      />

      <ErrorNote message={error ?? loadError} />
      <p className="sr-only" aria-live="polite">
        {announcement}
      </p>

      <ProgramsSection
        restaurantId={id}
        loading={rewards.isLoading}
        programs={(rewards.data?.programs ?? []).filter(
          (p) => p.purchase_type !== "REFERRAL",
        )}
        dishes={rewards.data?.items ?? []}
        headings={rewards.data?.headings ?? []}
        currency={rewards.data?.currency ?? "USD"}
        onError={setError}
        onSay={setAnnouncement}
      />

      <hr className="my-9 border-hairline" />

      <ReferralSection
        restaurantId={id}
        loading={rewards.isLoading}
        offer={(rewards.data?.programs ?? []).find(
          (p) => p.purchase_type === "REFERRAL",
        )}
        dishes={rewards.data?.items ?? []}
        currency={rewards.data?.currency ?? "USD"}
        onError={setError}
        onSay={setAnnouncement}
      />

      <hr className="my-9 border-hairline" />

      <OffersSection
        restaurantId={id}
        loading={offers.isLoading}
        offers={offers.data?.offers ?? []}
        onError={setError}
        onSay={setAnnouncement}
      />
    </AdminShell>
  );
}

// ---------------------------------------------------------------- programs

/**
 * The loyalty programs this restaurant runs.
 *
 * A program is a stamp card: what counts, how many, and what is won. The
 * rule reads back as a sentence the server builds, so this list and any
 * later digest cannot describe the same program two ways.
 *
 * WHAT IS NOT HERE is any way to grant or adjust a customer's stamps. The
 * ledger is written by orders completing and refunds reversing them; a
 * button here would be a second way to move the same number.
 */
function ProgramsSection({
  restaurantId,
  loading,
  programs,
  dishes,
  headings,
  currency,
  onError,
  onSay,
}: {
  restaurantId: string;
  loading: boolean;
  programs: LoyaltyProgram[];
  dishes: MenuDish[];
  headings: MenuHeading[];
  currency: string;
  onError: (message: string | null) => void;
  onSay: (message: string) => void;
}) {
  const [retire] = useRetireRewardMutation();
  const [update] = useUpdateRewardMutation();
  const [busy, setBusy] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  async function act(program: LoyaltyProgram, what: () => Promise<unknown>, said: string) {
    setBusy(program.id);
    onError(null);
    try {
      await what();
      onSay(`${program.name} ${said}.`);
    } catch (e) {
      onError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section aria-labelledby="programs-heading">
      <h2
        id="programs-heading"
        className="font-display text-[24px] leading-[1.2] tracking-[-.5px]"
      >
        Loyalty programs
      </h2>
      <p className="mb-4 mt-1 max-w-prose text-caption text-muted">
        A stamp card. Qualifying purchases fill it; filling it wins the
        customer one of the dishes you choose, and the card starts again.
      </p>

      {!adding && (
        <button type="button" className="btn-quiet mb-6" onClick={() => setAdding(true)}>
          Create program
        </button>
      )}
      {adding && (
        <ProgramForm
          restaurantId={restaurantId}
          dishes={dishes}
          headings={headings}
          currency={currency}
          onDone={(name) => {
            setAdding(false);
            if (name) onSay(`${name} created.`);
          }}
        />
      )}

      {loading ? (
        <Loading />
      ) : programs.length === 0 ? (
        <Empty>
          No programs yet. Create one and this restaurant starts a loyalty
          scheme; until then its customers are told it is not running one.
        </Empty>
      ) : (
        <ul className="flex flex-col">
          {programs.map((program) => (
            <li
              key={program.id}
              className="flex flex-wrap items-center gap-3 border-t border-hairline py-4"
            >
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-[650]">{program.name}</span>
                {/* The rule in words, from the server. */}
                <span className="block max-w-prose text-caption text-muted">
                  {program.summary}
                </span>
                {!program.is_active && (
                  <span className="block text-caption text-muted">
                    Switched off — nobody is collecting
                  </span>
                )}
                {program.choices.some((c) => !c.is_available) && (
                  <span className="block text-caption text-muted">
                    Some reward dishes are sold out and cannot be claimed today
                  </span>
                )}
              </span>
              <button
                type="button"
                className="btn-quiet btn-compact"
                disabled={busy === program.id}
                onClick={() =>
                  act(
                    program,
                    () =>
                      update({
                        id: restaurantId,
                        rewardId: program.id,
                        patch: { is_active: !program.is_active },
                      }).unwrap(),
                    program.is_active ? "switched off" : "switched on",
                  )
                }
              >
                {program.is_active ? "Switch off" : "Switch on"}
              </button>
              {/* Soft, and the ledger is untouched: stamps already earned
                  stay earned and rewards already won stay spendable. */}
              <button
                type="button"
                className="btn-quiet btn-compact"
                disabled={busy === program.id}
                onClick={() =>
                  act(
                    program,
                    () => retire({ id: restaurantId, rewardId: program.id }).unwrap(),
                    "retired",
                  )
                }
              >
                Retire
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Create one program: what counts, how many, and what is won. */
function ProgramForm({
  restaurantId,
  dishes,
  headings,
  currency,
  onDone,
}: {
  restaurantId: string;
  dishes: MenuDish[];
  headings: MenuHeading[];
  currency: string;
  onDone: (name?: string) => void;
}) {
  const [create, { isLoading }] = useCreateRewardMutation();
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"COMPLETED_ORDERS" | "MENU_ITEM">("COMPLETED_ORDERS");
  const [count, setCount] = useState("9");
  const [minSpend, setMinSpend] = useState("");
  const [qualifier, setQualifier] = useState("");
  const [choices, setChoices] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  function toggle(itemId: string) {
    setChoices((prev) =>
      prev.includes(itemId) ? prev.filter((i) => i !== itemId) : [...prev, itemId],
    );
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    const required = Number(count);
    if (!name.trim()) return setError("Give the program a name.");
    if (!Number.isInteger(required) || required < 1) {
      return setError("Purchases required has to be a whole number, 1 or more.");
    }
    if (kind === "MENU_ITEM" && !qualifier) {
      return setError("Choose the dish or heading that counts.");
    }
    if (choices.length === 0) {
      return setError("Choose at least one dish the reward can be taken as.");
    }
    // "dish:<id>" or "heading:<id>", so one control can carry either.
    const [which, id] = qualifier.split(":");
    const spend = minSpend.trim() ? Math.round(Number(minSpend) * 100) : 0;
    if (minSpend.trim() && (!Number.isFinite(spend) || spend < 0)) {
      return setError("Enter the minimum order as a plain amount, like 10.00.");
    }
    try {
      await create({
        id: restaurantId,
        draft: {
          name: name.trim(),
          purchase_type: kind,
          required_count: required,
          minimum_spend_minor: kind === "COMPLETED_ORDERS" ? spend : 0,
          qualifying_item_id: kind === "MENU_ITEM" && which === "dish" ? id : null,
          qualifying_item_type_id:
            kind === "MENU_ITEM" && which === "heading" ? id : null,
          choices,
        },
      }).unwrap();
      onDone(name.trim());
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <form
      onSubmit={submit}
      className="mb-6 rounded-panel border border-hairline bg-surface p-5"
      noValidate
    >
      <ErrorNote message={error} />
      <label className="block">
        <span className="label">Program name</span>
        <input
          className="field mt-[7px]"
          maxLength={120}
          value={name}
          placeholder="Meal Loyalty"
          onChange={(e) => setName(e.target.value)}
        />
      </label>

      <fieldset className="mt-4">
        <legend className="label">Qualifying purchase</legend>
        <div className="mt-[7px] flex flex-wrap gap-4">
          {([
            ["COMPLETED_ORDERS", "Completed orders"],
            ["MENU_ITEM", "Specific dish or heading"],
          ] as const).map(([value, label]) => (
            <label key={value} className="flex items-center gap-2 text-caption">
              <input
                type="radio"
                name="purchase-type"
                className="h-4 w-4"
                checked={kind === value}
                onChange={() => setKind(value)}
              />
              {label}
            </label>
          ))}
        </div>
      </fieldset>

      <div className="mt-4 flex flex-wrap items-end gap-4">
        <label className="w-[170px]">
          <span className="label">Purchases required</span>
          <input
            className="field mt-[7px]"
            inputMode="numeric"
            value={count}
            onChange={(e) => setCount(e.target.value)}
          />
        </label>

        {kind === "COMPLETED_ORDERS" ? (
          <label className="w-[190px]">
            <span className="label">Minimum order</span>
            <input
              className="field mt-[7px]"
              inputMode="decimal"
              placeholder="10.00"
              value={minSpend}
              onChange={(e) => setMinSpend(e.target.value)}
            />
          </label>
        ) : (
          <label className="min-w-[240px] flex-1">
            <span className="label">What counts</span>
            <select
              className="field mt-[7px]"
              value={qualifier}
              onChange={(e) => setQualifier(e.target.value)}
            >
              <option value="">Choose…</option>
              <optgroup label="Any dish under a heading">
                {headings.map((h) => (
                  <option key={h.id} value={`heading:${h.id}`}>
                    {h.name}
                  </option>
                ))}
              </optgroup>
              <optgroup label="One dish only">
                {dishes.map((d) => (
                  <option key={d.id} value={`dish:${d.id}`}>
                    {d.name}
                  </option>
                ))}
              </optgroup>
            </select>
          </label>
        )}
      </div>

      <fieldset className="mt-4">
        <legend className="label">Free reward — the customer picks one</legend>
        <div className="mt-[7px] flex flex-wrap gap-2">
          {dishes.map((dish) => {
            const on = choices.includes(dish.id);
            return (
              <button
                key={dish.id}
                type="button"
                aria-pressed={on}
                className="chip"
                onClick={() => toggle(dish.id)}
              >
                {dish.name}
                <span className="tnum opacity-70">
                  · {money(dish.base_price_minor, currency)}
                </span>
              </button>
            );
          })}
        </div>
      </fieldset>

      <div className="mt-5 flex flex-wrap items-center gap-4">
        <button type="submit" className="btn-primary" disabled={isLoading}>
          {isLoading ? "Creating…" : "Create program"}
        </button>
        <button type="button" className="link" onClick={() => onDone()}>
          Cancel
        </button>
      </div>
    </form>
  );
}

// --------------------------------------------------------------- referrals

/**
 * Bring a friend, get something free.
 *
 * A block of its own rather than a row in the loyalty list, because an
 * operator thinks of it as its own offer and because there is at most one:
 * two running at once would pay twice for the same friend with no way to
 * say which was meant. Underneath it is the same engine -- a program whose
 * qualifying purchase is a person -- which is why what it pays out in is
 * chosen exactly as a stamp card's is.
 *
 * NOTHING HERE SENDS A MESSAGE. The customer shares their own link from
 * their own phone; there is no SMS in this stack and none is wanted. The
 * copy below says so, because an operator switching this on will otherwise
 * reasonably expect the server to text people.
 */
function ReferralSection({
  restaurantId,
  loading,
  offer,
  dishes,
  currency,
  onError,
  onSay,
}: {
  restaurantId: string;
  loading: boolean;
  offer: LoyaltyProgram | undefined;
  dishes: MenuDish[];
  currency: string;
  onError: (message: string | null) => void;
  onSay: (message: string) => void;
}) {
  const [create, { isLoading: creating }] = useCreateRewardMutation();
  const [update] = useUpdateRewardMutation();
  const [retire] = useRetireRewardMutation();
  const [busy, setBusy] = useState(false);
  const [friends, setFriends] = useState("1");
  const [choices, setChoices] = useState<string[]>([]);

  function toggle(itemId: string) {
    setChoices((prev) =>
      prev.includes(itemId) ? prev.filter((i) => i !== itemId) : [...prev, itemId],
    );
  }

  async function start(event: React.FormEvent) {
    event.preventDefault();
    onError(null);
    const required = Number(friends);
    if (!Number.isInteger(required) || required < 1) {
      return onError("Friends required has to be a whole number, 1 or more.");
    }
    if (choices.length === 0) {
      return onError("Choose at least one dish the reward can be taken as.");
    }
    setBusy(true);
    try {
      await create({
        id: restaurantId,
        draft: {
          name: "Refer a friend",
          purchase_type: "REFERRAL",
          required_count: required,
          choices,
        },
      }).unwrap();
      onSay("Referral offer started.");
    } catch (e) {
      onError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function act(what: () => Promise<unknown>, said: string) {
    setBusy(true);
    onError(null);
    try {
      await what();
      onSay(said);
    } catch (e) {
      onError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="referrals-heading">
      <h2
        id="referrals-heading"
        className="font-display text-[24px] leading-[1.2] tracking-[-.5px]"
      >
        Referrals
      </h2>
      <p className="mb-4 mt-1 max-w-prose text-caption text-muted">
        A customer shares their own link. When somebody who has never ordered
        here uses it and buys something, the customer who shared it wins a free
        dish. The link is sent by the customer from their own phone — nothing
        is texted or emailed from here.
      </p>

      {loading ? (
        <Loading />
      ) : offer ? (
        <div className="flex flex-wrap items-center gap-3 border-t border-hairline py-4">
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-[650]">{offer.name}</span>
            <span className="block max-w-prose text-caption text-muted">
              {offer.summary}
            </span>
            {!offer.is_active && (
              <span className="block text-caption text-muted">
                Switched off — no new referrals are being counted
              </span>
            )}
          </span>
          <button
            type="button"
            className="btn-quiet btn-compact"
            disabled={busy}
            onClick={() =>
              act(
                () =>
                  update({
                    id: restaurantId,
                    rewardId: offer.id,
                    patch: { is_active: !offer.is_active },
                  }).unwrap(),
                offer.is_active ? "Referrals switched off." : "Referrals switched on.",
              )
            }
          >
            {offer.is_active ? "Switch off" : "Switch on"}
          </button>
          {/* Rewards already won stay spendable: ending a promotion is not
              the same as taking back what it gave. */}
          <button
            type="button"
            className="btn-quiet btn-compact"
            disabled={busy}
            onClick={() =>
              act(
                () => retire({ id: restaurantId, rewardId: offer.id }).unwrap(),
                "Referral offer retired.",
              )
            }
          >
            Retire
          </button>
        </div>
      ) : (
        <form onSubmit={start} noValidate>
          <div className="flex flex-wrap items-end gap-4">
            <label className="w-[170px]">
              <span className="label">Friends required</span>
              <input
                className="field mt-[7px]"
                inputMode="numeric"
                value={friends}
                onChange={(e) => setFriends(e.target.value)}
              />
            </label>
          </div>
          <fieldset className="mt-4">
            <legend className="label">Free reward — the customer picks one</legend>
            <div className="mt-[7px] flex flex-wrap gap-2">
              {dishes.map((dish) => {
                const on = choices.includes(dish.id);
                return (
                  <button
                    key={dish.id}
                    type="button"
                    aria-pressed={on}
                    className="chip"
                    onClick={() => toggle(dish.id)}
                  >
                    {dish.name}
                    <span className="tnum opacity-70">
                      · {money(dish.base_price_minor, currency)}
                    </span>
                  </button>
                );
              })}
            </div>
          </fieldset>
          <button type="submit" className="btn-primary mt-5" disabled={creating || busy}>
            {creating ? "Starting…" : "Start referral offer"}
          </button>
        </form>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ offers

/**
 * What the storefront is advertising, running first -- the question on
 * opening this is almost always "what is a customer seeing right now".
 *
 * Whether an offer is running is the server's answer (`is_live`), not a
 * comparison repeated here: this list and the storefront must not come to
 * disagree about what the dates mean.
 */
function OffersSection({
  restaurantId,
  loading,
  offers,
  onError,
  onSay,
}: {
  restaurantId: string;
  loading: boolean;
  offers: AdminOffer[];
  onError: (message: string | null) => void;
  onSay: (message: string) => void;
}) {
  const [retire] = useRetireOfferMutation();
  const [update] = useUpdateOfferMutation();
  const [busy, setBusy] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const live = offers.filter((offer) => offer.is_live);
  const resting = offers.filter((offer) => !offer.is_live);

  async function act(offer: AdminOffer, what: () => Promise<unknown>, said: string) {
    setBusy(offer.id);
    onError(null);
    try {
      await what();
      onSay(`${offer.title} ${said}.`);
    } catch (e) {
      onError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  function card(offer: AdminOffer) {
    return (
      <li
        key={offer.id}
        className="flex flex-wrap items-center gap-3 border-t border-hairline py-4"
      >
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-[650]">{offer.title}</span>
          {offer.body && (
            <span className="block max-w-prose text-caption text-muted">{offer.body}</span>
          )}
          <span className="block text-caption text-muted">{windowOf(offer)}</span>
        </span>
        <button
          type="button"
          className="btn-quiet btn-compact"
          disabled={busy === offer.id}
          onClick={() =>
            act(
              offer,
              () =>
                update({
                  id: restaurantId,
                  offerId: offer.id,
                  patch: { is_active: !offer.is_active },
                }).unwrap(),
              offer.is_active ? "switched off" : "switched on",
            )
          }
        >
          {offer.is_active ? "Switch off" : "Switch on"}
        </button>
        <button
          type="button"
          className="btn-quiet btn-compact"
          disabled={busy === offer.id}
          onClick={() =>
            act(offer, () => retire({ id: restaurantId, offerId: offer.id }).unwrap(), "retired")
          }
        >
          Retire
        </button>
      </li>
    );
  }

  return (
    <section aria-labelledby="offers-heading">
      <h2
        id="offers-heading"
        className="font-display text-[24px] leading-[1.2] tracking-[-.5px]"
      >
        Offers
      </h2>
      <p className="mb-4 mt-1 max-w-prose text-caption text-muted">
        A notice on the storefront. It takes nothing off a bill — for that, use
        a coupon.
      </p>

      {!adding && (
        <button type="button" className="btn-quiet mb-6" onClick={() => setAdding(true)}>
          Write an offer
        </button>
      )}
      {adding && (
        <OfferForm
          restaurantId={restaurantId}
          onDone={(title) => {
            setAdding(false);
            if (title) onSay(`${title} added.`);
          }}
        />
      )}

      {loading ? (
        <Loading />
      ) : offers.length === 0 ? (
        <Empty>Nothing advertised yet. The storefront simply shows no offers.</Empty>
      ) : (
        <>
          {live.length > 0 && (
            <div className="mb-6">
              <h3 className="mb-2 text-sm font-semibold">Showing now</h3>
              <ul className="flex flex-col">{live.map(card)}</ul>
            </div>
          )}
          {resting.length > 0 && (
            <div className="mb-6">
              <h3 className="mb-2 text-sm font-semibold">Not showing</h3>
              <ul className="flex flex-col">{resting.map(card)}</ul>
            </div>
          )}
        </>
      )}
    </section>
  );
}

/** Why an offer is or is not on screen, in a sentence. */
function windowOf(offer: AdminOffer): string {
  if (!offer.is_active) return "Switched off";
  const from = offer.starts_at ? day(offer.starts_at) : null;
  const to = offer.ends_at ? day(offer.ends_at) : null;
  if (from && to) return `${from} to ${to}`;
  if (from) return `From ${from}`;
  if (to) return `Until ${to}`;
  return "No dates — on until switched off";
}

function day(iso: string): string {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return "an unreadable date";
  return when.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/** Write one offer. Dates optional, and both ends independent. */
function OfferForm({
  restaurantId,
  onDone,
}: {
  restaurantId: string;
  onDone: (title?: string) => void;
}) {
  const [create, { isLoading }] = useCreateOfferMutation();
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [starts, setStarts] = useState("");
  const [ends, setEnds] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    if (!title.trim()) return setError("An offer needs a title.");
    if (starts && ends && new Date(ends) <= new Date(starts)) {
      return setError("The offer has to end after it starts.");
    }
    try {
      await create({
        id: restaurantId,
        draft: {
          title: title.trim(),
          body: body.trim() || null,
          // A date input gives a local day; sent as an instant so the server
          // stores what the operator meant by it.
          starts_at: starts ? new Date(starts).toISOString() : null,
          ends_at: ends ? new Date(ends).toISOString() : null,
        },
      }).unwrap();
      onDone(title.trim());
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <form
      onSubmit={submit}
      className="mb-6 rounded-panel border border-hairline bg-surface p-5"
      noValidate
    >
      <ErrorNote message={error} />
      <label className="block">
        <span className="label">Title</span>
        <input
          className="field mt-[7px]"
          maxLength={120}
          value={title}
          placeholder="Two for Tuesday"
          onChange={(e) => setTitle(e.target.value)}
        />
      </label>
      <label className="mt-4 block">
        <span className="label">Details</span>
        <textarea
          className="field mt-[7px]"
          maxLength={500}
          value={body}
          placeholder="Any two burgers for the price of one, all day Tuesday."
          onChange={(e) => setBody(e.target.value)}
        />
      </label>
      <div className="mt-4 flex flex-wrap items-end gap-4">
        <label className="w-[190px]">
          <span className="label">Starts</span>
          <input
            type="date"
            className="field mt-[7px]"
            value={starts}
            onChange={(e) => setStarts(e.target.value)}
          />
        </label>
        <label className="w-[190px]">
          <span className="label">Ends</span>
          <input
            type="date"
            className="field mt-[7px]"
            value={ends}
            onChange={(e) => setEnds(e.target.value)}
          />
        </label>
        <button type="submit" className="btn-primary" disabled={isLoading}>
          {isLoading ? "Saving…" : "Save"}
        </button>
        <button type="button" className="link" onClick={() => onDone()}>
          Cancel
        </button>
      </div>
      <p className="field-hint">
        Leave the dates empty to show it from now until you switch it off.
      </p>
    </form>
  );
}
