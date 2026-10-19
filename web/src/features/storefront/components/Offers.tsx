import { MenuImage } from "@/components/common/MenuImage";
import { useOffersQuery } from "@/features/storefront/storefrontApi";

/**
 * What the restaurant is advertising, above the menu.
 *
 * A poster in the window: a title, a few words, a picture. No price and no
 * code -- a promotion that takes money off is a coupon, and the server only
 * ever sends the offers inside their window, so this component has no rules
 * about dates to get wrong.
 *
 * Renders nothing at all when there is nothing on. An empty "Offers" heading
 * is worse than no heading: it tells a customer the restaurant has stopped
 * bothering.
 */
export function Offers() {
  const { data } = useOffersQuery();
  const offers = data?.offers ?? [];
  if (offers.length === 0) return null;

  return (
    <section className="mt-8" aria-labelledby="offers-heading">
      <h2
        id="offers-heading"
        className="mb-3 font-display text-[22px] leading-[1.2] tracking-[-.4px]"
      >
        {offers.length === 1 ? "What's on" : "What's on right now"}
      </h2>
      {/* A row that scrolls on a phone and wraps into a grid from sm up:
          offers are few, and three across is a poster wall rather than a
          list to work through. */}
      <ul className="-mx-1 flex snap-x gap-3 overflow-x-auto px-1 pb-1 sm:mx-0 sm:grid sm:grid-cols-2 sm:overflow-visible sm:px-0 lg:grid-cols-3">
        {offers.map((offer) => (
          <li
            key={offer.id}
            className="flex min-w-[78%] snap-start flex-col overflow-hidden rounded-panel border border-hairline bg-surface sm:min-w-0"
          >
            {offer.image_url && (
              <MenuImage
                src={offer.image_url}
                className="h-[132px] w-full bg-paper object-cover"
              />
            )}
            <div className="flex flex-1 flex-col p-4">
              <h3 className="text-base font-bold leading-[1.25] tracking-[-.3px]">
                {offer.title}
              </h3>
              {offer.body && (
                <p className="mt-1 text-caption text-muted">{offer.body}</p>
              )}
              {/* Only where there is an end to name. "Until further notice"
                  is not a deadline and reads as filler under one that is. */}
              {offer.ends_at && (
                <p className="mt-auto pt-3 text-caption font-[650] text-brick">
                  Until {formatEnds(offer.ends_at)}
                </p>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The last day it runs, in the customer's own locale.
 *
 *  Date only: an offer that ends at midnight is an offer that ends on a day,
 *  and printing a time invites somebody to arrive at 23:58.
 */
function formatEnds(iso: string): string {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return "further notice";
  return when.toLocaleDateString(undefined, { day: "numeric", month: "long" });
}
