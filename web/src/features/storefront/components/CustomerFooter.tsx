import { useAppSelector } from "@/app/hooks";
import { selectCartCount } from "@/features/cart/cartSlice";

/**
 * The foot of every customer page.
 *
 * Two jobs, and neither is decoration.
 *
 * The allergen line. Zenoeats does not cook the food, does not inspect the
 * kitchen and cannot verify an ingredient list, so the only honest thing to
 * say is "ask the restaurant" -- and it has to be said where someone about to
 * order will see it, not only inside a policy they will not open.
 *
 * The policy links. Google, Apple and Facebook all require a reachable
 * privacy policy before they will approve sign-in, and Facebook requires the
 * deletion page as well. They are plain <a> elements rather than <Link>
 * because the pages are files served outside this app, so React Router must
 * not try to resolve them.
 */
export function CustomerFooter() {
  // Only reserve room for the cart bar when there is a cart to review. The
  // bar is fixed to the bottom of the viewport and was covering the allergen
  // line -- the one thing here somebody with an allergy has to be able to
  // read -- but reserving the space unconditionally left a band of nothing
  // under every page that has no bar at all.
  const count = useAppSelector(selectCartCount);

  return (
    // No top border and a small gap: the page above this already ends in a
    // rule of its own, so a second line a few pixels below it read as two
    // footers with a hole between them rather than one.
    <footer
      // The same container as the page above it -- width, gutters and
      // all -- so this text starts on the same left edge as the address
      // and the restaurant name, rather than on one of its own.
      className={`mt-7 pt-6 text-caption text-muted ${
        count > 0 ? "pb-[132px]" : "pb-8"
      }`}
    >
      <div className="mx-auto w-full max-w-[1336px] space-y-3 px-[19px] sm:px-7 xl:px-10">
        <p>
          Allergies or intolerances? Food is prepared in kitchens that handle
          allergens, and cross-contact cannot be ruled out. Contact the
          restaurant before ordering.
        </p>
        <nav className="legal-links" aria-label="Policies">
          <a href="/legal/terms">Terms</a>
          <a href="/legal/privacy">Privacy</a>
          <a href="/legal/refunds">Refunds</a>
          <a href="/legal/data-deletion">Your data</a>
        </nav>
        <p>Ordering by Zenoeats.</p>
      </div>
    </footer>
  );
}
