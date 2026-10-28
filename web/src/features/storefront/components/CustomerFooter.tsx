import { useAppSelector } from "@/app/hooks";
import { selectCartCount } from "@/features/cart/cartSlice";
import { usePortalQuery } from "@/features/storefront/storefrontApi";
import { usePageColumnClass } from "@/components/layout/pageColumn";

/**
 * The foot of every customer page.
 *
 * Two jobs, and neither is decoration.
 *
 * The allergen notice. ZenoEats does not cook the food, does not inspect the
 * kitchen and cannot verify an ingredient list, so the only honest thing to
 * say is "ask the restaurant" -- and it has to be said where someone about to
 * order will see it, not only inside a policy they will not open. It names
 * the common allergens rather than gesturing at "allergens", because a
 * person avoiding sesame or shellfish is looking for their own word.
 *
 * The policy links. Google, Apple and Facebook all require a reachable
 * privacy policy before they will approve sign-in, and Facebook requires the
 * deletion page as well -- which is why "Your data" is still here beside the
 * three named policies. They are plain <a> elements rather than <Link>
 * because the pages are files served outside this app, so React Router must
 * not try to resolve them.
 */
export function CustomerFooter() {
  // Only reserve room for the cart bar when there is a cart to review. The
  // bar is fixed to the bottom of the viewport and was covering the allergen
  // notice -- the one thing here somebody with an allergy has to be able to
  // read -- but reserving the space unconditionally left a band of nothing
  // under every page that has no bar at all.
  const count = useAppSelector(selectCartCount);
  // The same cached read every other customer component makes, so this costs
  // no request of its own. "Contact us" means the restaurant, not ZenoEats:
  // the notice above has just told them to ask about an ingredient, and the
  // kitchen is the only party that can answer.
  const { data } = usePortalQuery();
  const phone = data?.phone;
  // The column the page above is using, so this text starts on the same left
  // edge as the order it is talking about rather than on one of its own.
  const column = usePageColumnClass();

  return (
    // No top border and a small gap: the page above this already ends in a
    // rule of its own, so a second line a few pixels below it read as two
    // footers with a hole between them rather than one.
    <footer
      className={`mt-7 pt-6 text-caption text-muted ${
        count > 0 ? "pb-[132px]" : "pb-8"
      }`}
    >
      <div className={`${column} space-y-3`}>
        <section aria-labelledby="allergen-notice">
          <h2 id="allergen-notice" className="text-sm font-semibold text-ink">
            Food Allergies &amp; Dietary Restrictions
          </h2>
          <p className="mt-1 max-w-[75ch]">
            Our food is prepared in kitchens that handle common allergens,
            including milk, eggs, wheat, soy, peanuts, tree nuts, fish, and
            shellfish. While we take precautions, we cannot guarantee that any
            item is completely allergen-free. Please contact the restaurant
            before placing your order if you have food allergies or dietary
            restrictions.
          </p>
        </section>

        <nav className="legal-links" aria-label="Policies">
          <a href="/legal/terms">Terms of Service</a>
          <a href="/legal/privacy">Privacy Policy</a>
          <a href="/legal/refunds">Refund &amp; Cancellation Policy</a>
          <a href="/legal/data-deletion">Your data</a>
          {/* Only where the restaurant gave a number. A "Contact us" that
              goes nowhere is worse than no link at all, especially directly
              under a notice telling someone to get in touch. */}
          {phone && <a href={`tel:${phone.replace(/[^+\d]/g, "")}`}>Contact us</a>}
        </nav>

        {/* Centred as a pair. The notice and the policies above are read
            left to right like the rest of the page; these two are a colophon
            and sit under it, which is where a reader expects a sign-off
            rather than another paragraph. */}
        <div className="space-y-1 pt-1 text-center">
          {/* Read at render rather than written in: a hard-coded year is
              wrong every January and nobody notices until a customer does. */}
          <p>© {new Date().getFullYear()} ZenoEats. All rights reserved.</p>
          <p className="font-semibold text-ink">Powered by ZenoEats</p>
        </div>
      </div>
    </footer>
  );
}
