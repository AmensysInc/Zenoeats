import { Route, Routes } from "react-router-dom";
import { RequireAdmin } from "@/components/layout/OperatorGuards";
import { AdminRestaurantsPage } from "@/pages/admin/AdminRestaurantsPage";
import { AdminOrdersPage } from "@/pages/admin/AdminOrdersPage";
import { AdminCouponsPage } from "@/pages/admin/AdminCouponsPage";
import { AdminRewardsPage } from "@/pages/admin/AdminRewardsPage";
import { AdminOffersPage } from "@/pages/admin/AdminOffersPage";
import { NotFound } from "@/routes/NotFound";

/**
 * The platform portal, and everything only it needs. Loaded on demand, for
 * the same reason as the restaurant portal: a handful of operators use this,
 * and every customer was downloading it.
 *
 * /admin/login is absent on purpose -- a separate HTML entry point served by
 * nginx, and only on admin.<root domain>, which is also the only hostname its
 * API answers on.
 */
export default function AdminArea() {
  return (
    <Routes>
      <Route index element={<RequireAdmin><AdminRestaurantsPage /></RequireAdmin>} />
      <Route
        path="restaurants/:id/orders"
        element={<RequireAdmin><AdminOrdersPage /></RequireAdmin>}
      />
      <Route
        path="restaurants/:id/coupons"
        element={<RequireAdmin><AdminCouponsPage /></RequireAdmin>}
      />
      <Route
        path="restaurants/:id/rewards"
        element={<RequireAdmin><AdminRewardsPage /></RequireAdmin>}
      />
      <Route
        path="restaurants/:id/offers"
        element={<RequireAdmin><AdminOffersPage /></RequireAdmin>}
      />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
