import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Empty, ErrorNote, Loading } from "@/components/common/Feedback";
import { Icon } from "@/components/common/icons";
import { PageTitle } from "@/components/layout/Shell";
import { AdminShell } from "@/features/admin/components/AdminShell";
import {
  useCreateOfferMutation,
  useOffersQuery,
  useRetireOfferMutation,
  useUpdateOfferMutation,
  type AdminOffer,
} from "@/features/admin/adminApi";
import { errorMessage } from "@/services/apiClient";

/**
 * One restaurant's offers: what its storefront is advertising, and when.
 *
 * A platform screen, for the reason coupons and rewards are. An offer
 * carries no discount and changes no price -- it is the poster in the
 * window -- which is exactly why it can be written by somebody who is not
 * allowed to move a price. A promotion that takes money off is a coupon,
 * one tab over.
 *
 * RUNNING FIRST, because the question on opening this page is almost always
 * "what is a customer seeing right now". Whether an offer is running is the
 * server's answer (`is_live`), not a comparison repeated here: the storefront
 * and this list must not disagree about what the dates mean.
 */
export function AdminOffersPage() {
  const { id = "" } = useParams<{ id: string }>();
  const offers = useOffersQuery(id);

  const [retire] = useRetireOfferMutation();
  const [update] = useUpdateOfferMutation();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [announcement, setAnnouncement] = useState("");

  const rows = offers.data?.offers ?? [];
  const live = rows.filter((offer) => offer.is_live);
  const resting = rows.filter((offer) => !offer.is_live);

  async function act(offer: AdminOffer, what: () => Promise<unknown>, said: string) {
    setBusy(offer.id);
    setError(null);
    try {
      await what();
      setAnnouncement(`${offer.title} ${said}.`);
    } catch (e) {
      setError(errorMessage(e));
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
          <span className="block text-caption text-muted">{window(offer)}</span>
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
                  id,
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
          onClick={() => act(offer, () => retire({ id, offerId: offer.id }).unwrap(), "retired")}
        >
          Retire
        </button>
      </li>
    );
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
        title={offers.data?.restaurant_name ?? "…"}
        subtitle="What this restaurant's storefront advertises, and for how long"
      />

      <ErrorNote message={error ?? (offers.error ? errorMessage(offers.error) : null)} />
      <p className="sr-only" aria-live="polite">
        {announcement}
      </p>

      <p className="mb-5 max-w-prose text-caption text-muted">
        An offer is a notice on the storefront. It takes nothing off a bill —
        for that, use a coupon.
      </p>

      {!adding && (
        <button type="button" className="btn-primary mb-7" onClick={() => setAdding(true)}>
          Write an offer
        </button>
      )}
      {adding && (
        <OfferForm
          restaurantId={id}
          onDone={(title) => {
            setAdding(false);
            if (title) setAnnouncement(`${title} added.`);
          }}
        />
      )}

      {offers.isLoading ? (
        <Loading />
      ) : rows.length === 0 ? (
        <Empty>Nothing advertised yet. The storefront simply shows no offers.</Empty>
      ) : (
        <>
          {live.length > 0 && (
            <section className="mb-7">
              <h2 className="mb-2 text-sm font-semibold">Showing now</h2>
              <ul className="flex flex-col">{live.map(card)}</ul>
            </section>
          )}
          {resting.length > 0 && (
            <section className="mb-7">
              <h2 className="mb-2 text-sm font-semibold">Not showing</h2>
              <ul className="flex flex-col">{resting.map(card)}</ul>
            </section>
          )}
        </>
      )}
    </AdminShell>
  );
}

/** Why an offer is or is not on screen, in a sentence. */
function window(offer: AdminOffer): string {
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
      className="mb-7 rounded-panel border border-hairline bg-surface p-5"
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
