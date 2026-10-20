import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Empty, ErrorNote, Loading } from "@/components/common/Feedback";
import { Icon } from "@/components/common/icons";
import { PageTitle } from "@/components/layout/Shell";
import { AdminShell } from "@/features/admin/components/AdminShell";
import {
  useCreateRewardMutation,
  useRetireRewardMutation,
  useRewardsQuery,
  useUpdateRewardMutation,
  type MenuDish,
  type Reward,
} from "@/features/admin/adminApi";
import { errorMessage } from "@/services/apiClient";
import { money } from "@/utils/format";

/**
 * One restaurant's reward ladder: which dishes are free, and from how many
 * points.
 *
 * A platform screen, because a reward is a Zenoeats decision rather than a
 * tenant's -- the restaurant's own portal has no rewards tab, and no
 * endpoint behind one. Reached from the restaurant's row on /admin.
 *
 * GROUPED BY THRESHOLD, because that is how a customer meets it: ten points
 * buys a choice of these, twenty a choice of those. A flat list sorted by
 * dish would hide the shape of the thing being designed.
 *
 * WHAT IS NOT HERE is any way to grant or adjust somebody's points. A
 * balance is their completed orders, counted; an adjust button would be a
 * second truth about the same fact. An operator who wants to change what a
 * customer has earned changes their orders, which is the only honest way to
 * say it.
 */
export function AdminRewardsPage() {
  const { id = "" } = useParams<{ id: string }>();
  const rewards = useRewardsQuery(id);

  const [retire] = useRetireRewardMutation();
  const [update] = useUpdateRewardMutation();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [announcement, setAnnouncement] = useState("");

  const rows = rewards.data?.rewards ?? [];
  const dishes = rewards.data?.items ?? [];
  const currency = rewards.data?.currency ?? "USD";

  // The rungs, lowest first. The server already sorts by threshold, so this
  // only has to group what arrives in order.
  const rungs: { points: number; items: Reward[] }[] = [];
  for (const reward of rows) {
    const last = rungs[rungs.length - 1];
    if (last && last.points === reward.points_required) last.items.push(reward);
    else rungs.push({ points: reward.points_required, items: [reward] });
  }

  async function act(reward: Reward, what: () => Promise<unknown>, said: string) {
    setBusy(reward.id);
    setError(null);
    try {
      await what();
      setAnnouncement(`${reward.item_name} ${said}.`);
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
        title={rewards.data?.restaurant_name ?? "…"}
        subtitle="Dishes a returning customer can have free, and from how many points"
      />

      <ErrorNote message={error ?? (rewards.error ? errorMessage(rewards.error) : null)} />
      <p className="sr-only" aria-live="polite">
        {announcement}
      </p>

      <p className="mb-5 max-w-prose text-caption text-muted">
        A customer earns one point per completed order. Reaching a number of
        points unlocks everything listed at it and below, for good, and they
        may take one free dish per order.
      </p>

      {!adding && (
        <button type="button" className="btn-primary mb-7" onClick={() => setAdding(true)}>
          Add a reward
        </button>
      )}
      {adding && (
        <RewardForm
          restaurantId={id}
          dishes={dishes}
          currency={currency}
          onDone={(name) => {
            setAdding(false);
            if (name) setAnnouncement(`${name} added.`);
          }}
        />
      )}

      {rewards.isLoading ? (
        <Loading />
      ) : rows.length === 0 ? (
        <Empty>
          No rewards yet. Add one and this restaurant starts a loyalty program;
          until then its customers are told it is not running one.
        </Empty>
      ) : (
        rungs.map((rung) => (
          <section key={rung.points} className="mb-7">
            <h2 className="mb-2 text-sm font-semibold">
              <span className="tnum">{rung.points}</span> points
            </h2>
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
                            id,
                            rewardId: reward.id,
                            patch: { is_active: !reward.is_active },
                          }).unwrap(),
                        reward.is_active ? "switched off" : "switched on",
                      )
                    }
                  >
                    {reward.is_active ? "Switch off" : "Switch on"}
                  </button>
                  {/* No confirmation: it is a soft delete, orders that used
                      it keep their own lines, and it can be added again. */}
                  <button
                    type="button"
                    className="btn-quiet btn-compact"
                    disabled={busy === reward.id}
                    onClick={() =>
                      act(
                        reward,
                        () => retire({ id, rewardId: reward.id }).unwrap(),
                        "removed from the ladder",
                      )
                    }
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))
      )}
    </AdminShell>
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
      // ladder at that threshold, and retyping the whole form to find that
      // out would be the wrong lesson.
      setError(errorMessage(e));
    }
  }

  return (
    <form
      onSubmit={submit}
      className="mb-7 rounded-panel border border-hairline bg-surface p-5"
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
