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
  type MenuDish,
  type Reward,
} from "@/features/admin/adminApi";
import { errorMessage } from "@/services/apiClient";
import { money } from "@/utils/format";

/**
 * One restaurant's promotions: the reward ladder, and the offers on its
 * storefront.
 *
 * A platform screen, because both are Zenoeats decisions rather than a
 * tenant's -- the restaurant's own portal has neither tab and no endpoint
 * behind one. Reached from the restaurant's row on /admin, beside Coupons.
 *
 * TWO SECTIONS RATHER THAN TWO PAGES. They are different shapes -- a ladder
 * tied to the menu, and dated copy tied to nothing -- but an operator
 * setting up a restaurant's promotions is doing one job, and the third thing
 * they need, the coupons, is one click away. Splitting them put two entries
 * on the restaurant row for what reads as a single errand.
 *
 * WHAT IS DELIBERATELY ABSENT is any way to grant or adjust somebody's
 * points. A balance is their completed orders, counted; a button here would
 * be a second truth about the same fact. An operator who wants to change
 * what a customer earned changes their orders, which is the only honest way
 * to say it.
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

      <RewardsSection
        restaurantId={id}
        loading={rewards.isLoading}
        rewards={rewards.data?.rewards ?? []}
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

// ----------------------------------------------------------------- rewards

/**
 * The ladder, grouped by threshold -- which is how a customer meets it: ten
 * points buys a choice of these, twenty a choice of those. A flat list
 * sorted by dish would hide the shape of the thing being designed.
 */
function RewardsSection({
  restaurantId,
  loading,
  rewards,
  dishes,
  currency,
  onError,
  onSay,
}: {
  restaurantId: string;
  loading: boolean;
  rewards: Reward[];
  dishes: MenuDish[];
  currency: string;
  onError: (message: string | null) => void;
  onSay: (message: string) => void;
}) {
  const [retire] = useRetireRewardMutation();
  const [update] = useUpdateRewardMutation();
  const [busy, setBusy] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  // The server already sorts by threshold, so this only has to group what
  // arrives in order.
  const rungs: { points: number; items: Reward[] }[] = [];
  for (const reward of rewards) {
    const last = rungs[rungs.length - 1];
    if (last && last.points === reward.points_required) last.items.push(reward);
    else rungs.push({ points: reward.points_required, items: [reward] });
  }

  async function act(reward: Reward, what: () => Promise<unknown>, said: string) {
    setBusy(reward.id);
    onError(null);
    try {
      await what();
      onSay(`${reward.item_name} ${said}.`);
    } catch (e) {
      onError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section aria-labelledby="rewards-heading">
      <h2
        id="rewards-heading"
        className="font-display text-[24px] leading-[1.2] tracking-[-.5px]"
      >
        Rewards
      </h2>
      <p className="mb-4 mt-1 max-w-prose text-caption text-muted">
        A customer earns one point per completed order. Reaching a number of
        points unlocks everything listed at it and below, for good, and they may
        take one free dish per order.
      </p>

      {!adding && (
        <button type="button" className="btn-quiet mb-6" onClick={() => setAdding(true)}>
          Add a reward
        </button>
      )}
      {adding && (
        <RewardForm
          restaurantId={restaurantId}
          dishes={dishes}
          currency={currency}
          onDone={(name) => {
            setAdding(false);
            if (name) onSay(`${name} added.`);
          }}
        />
      )}

      {loading ? (
        <Loading />
      ) : rewards.length === 0 ? (
        <Empty>
          No rewards yet. Add one and this restaurant starts a loyalty program;
          until then its customers are told it is not running one.
        </Empty>
      ) : (
        rungs.map((rung) => (
          <div key={rung.points} className="mb-6">
            <h3 className="mb-2 text-sm font-semibold">
              <span className="tnum">{rung.points}</span> points
            </h3>
            <ul className="flex flex-col">
              {rung.items.map((reward) => (
                <li
                  key={reward.id}
                  className="flex flex-wrap items-center gap-3 border-t border-hairline py-4"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-[650]">{reward.item_name}</span>
                    <span className="block text-caption text-muted">
                      {!reward.is_active
                        ? "Switched off — not offered to anybody"
                        : !reward.item_is_available
                          ? "On the ladder, but the dish is sold out"
                          : "Offered"}
                    </span>
                  </span>
                  <button
                    type="button"
                    className="btn-quiet btn-compact"
                    disabled={busy === reward.id}
                    onClick={() =>
                      act(
                        reward,
                        () =>
                          update({
                            id: restaurantId,
                            rewardId: reward.id,
                            patch: { is_active: !reward.is_active },
                          }).unwrap(),
                        reward.is_active ? "switched off" : "switched on",
                      )
                    }
                  >
                    {reward.is_active ? "Switch off" : "Switch on"}
                  </button>
                  {/* No confirmation: a soft delete, orders that used it keep
                      their own lines, and it can be added again. */}
                  <button
                    type="button"
                    className="btn-quiet btn-compact"
                    disabled={busy === reward.id}
                    onClick={() =>
                      act(
                        reward,
                        () => retire({ id: restaurantId, rewardId: reward.id }).unwrap(),
                        "removed from the ladder",
                      )
                    }
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))
      )}
    </section>
  );
}

/** Put one dish on the ladder at one threshold. */
function RewardForm({
  restaurantId,
  dishes,
  currency,
  onDone,
}: {
  restaurantId: string;
  dishes: MenuDish[];
  currency: string;
  onDone: (name?: string) => void;
}) {
  const [create, { isLoading }] = useCreateRewardMutation();
  const [itemId, setItemId] = useState("");
  const [points, setPoints] = useState("10");
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    const value = Number(points);
    if (!itemId) return setError("Choose the dish this reward gives away.");
    if (!Number.isInteger(value) || value < 1) {
      return setError("Points has to be a whole number, 1 or more.");
    }
    try {
      await create({
        id: restaurantId,
        draft: { item_id: itemId, points_required: value },
      }).unwrap();
      onDone(dishes.find((d) => d.id === itemId)?.name);
    } catch (e) {
      // Stays open with the reason: the server refuses a dish already on the
      // ladder at that threshold, and retyping the form to find that out
      // would be the wrong lesson.
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
      <div className="flex flex-wrap items-end gap-4">
        <label className="min-w-[220px] flex-1">
          <span className="label">Dish</span>
          <select
            className="field mt-[7px]"
            value={itemId}
            onChange={(e) => setItemId(e.target.value)}
          >
            <option value="">Choose a dish…</option>
            {dishes.map((dish) => (
              <option key={dish.id} value={dish.id}>
                {dish.name} · {money(dish.base_price_minor, currency)}
              </option>
            ))}
          </select>
        </label>
        <label className="w-[140px]">
          <span className="label">Points</span>
          <input
            className="field mt-[7px]"
            inputMode="numeric"
            value={points}
            onChange={(e) => setPoints(e.target.value)}
          />
        </label>
        <button type="submit" className="btn-primary" disabled={isLoading}>
          {isLoading ? "Adding…" : "Add"}
        </button>
        <button type="button" className="link" onClick={() => onDone()}>
          Cancel
        </button>
      </div>
    </form>
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
