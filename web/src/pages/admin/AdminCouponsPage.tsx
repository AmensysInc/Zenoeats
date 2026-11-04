import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { Icon } from "@/components/common/icons";
import { PageTitle } from "@/components/layout/Shell";
import { AdminShell } from "@/features/admin/components/AdminShell";
import { CouponGroups } from "@/features/admin/components/CouponGroups";
import { CouponRedemptions } from "@/features/admin/components/CouponRedemptions";
import { PlainCoupons } from "@/features/admin/components/PlainCoupons";
import { SignupOffer } from "@/features/admin/components/SignupOffer";
import { useCouponsQuery, type CouponScope } from "@/features/admin/adminApi";

/**
 * One restaurant's promotions, as the platform sees them.
 *
 * The restaurant runs its own now, on its settings page -- see
 * features/restaurant/components/CouponsArea. This screen stays because
 * support still needs to see and fix a restaurant's promotion without asking
 * somebody to sign in, and because a platform-wide view of who is discounting
 * what is a question only this side can answer.
 *
 * Every section below is the same component the restaurant's own screen
 * renders, told whose coupons to show. Two copies would be the one thing
 * worth not having twice: what a discount means.
 */
export function AdminCouponsPage() {
  const { id = "" } = useParams<{ id: string }>();
  // The platform names the restaurant in the URL; the restaurant's own screen
  // does not need to, because its tenant is whoever is signed in.
  const scope = useMemo<CouponScope>(() => ({ restaurantId: id }), [id]);
  const coupons = useCouponsQuery(scope);

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
        title={coupons.data?.restaurant_name ?? "…"}
        subtitle="Codes a customer can type at this restaurant's checkout"
      />

      <PlainCoupons scope={scope} />
      <CouponGroups scope={scope} />
      <SignupOffer scope={scope} />
      <CouponRedemptions scope={scope} />
    </AdminShell>
  );
}
