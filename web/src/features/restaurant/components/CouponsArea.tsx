import { SettingsHeading } from "@/components/common/Feedback";
import { CouponGroups } from "@/features/admin/components/CouponGroups";
import { CouponRedemptions } from "@/features/admin/components/CouponRedemptions";
import { SignupOffer } from "@/features/admin/components/SignupOffer";
import { PlainCoupons } from "@/features/admin/components/PlainCoupons";

/**
 * The restaurant's own promotions, on its own settings page.
 *
 * These used to be the platform's alone: a restaurant wanting "20% off
 * Tuesdays" filed a support request, because the manage portal had no coupon
 * screen and the API had no endpoint behind one. It is the restaurant's
 * margin, so it is now the restaurant's decision.
 *
 * THE SAME COMPONENTS THE PLATFORM SCREEN USES, told whose coupons to show.
 * A copy of each for the manage portal would be four screens to keep in step
 * with four others, and the first thing to drift would be what a discount
 * means. The one difference is the scope: the platform names a restaurant in
 * the URL, and here the restaurant is whoever is signed in.
 *
 * Under Settings rather than a tab of its own because this is configuration
 * -- the same kind of thing as the delivery rings and the tax rate, set once
 * and left running -- and because it belongs beside them in the one place a
 * manager already goes to change how their restaurant works.
 */
export function CouponsArea() {
  return (
    <section
      id="settings-coupons"
      className="card mb-6 scroll-mt-6"
      aria-labelledby="settings-coupons-heading"
    >
      <SettingsHeading
        id="settings-coupons-heading"
        title="Coupons"
        subtitle="Codes your customers can type at checkout."
      />
      <div className="mt-6">
        {/* "mine" rather than a restaurant id: the endpoints behind these
            take their tenant from the session, so there is nothing to name. */}
        <PlainCoupons scope="mine" />
        <CouponGroups scope="mine" />
        <SignupOffer scope="mine" />
        <CouponRedemptions scope="mine" />
      </div>
    </section>
  );
}
