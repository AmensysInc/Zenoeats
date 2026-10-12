import { Route, Routes } from "react-router-dom";
import { RequireStaff } from "@/components/layout/OperatorGuards";
import { KitchenBoardPage } from "@/pages/manage/KitchenBoardPage";
import { DeliveriesPage } from "@/pages/manage/DeliveriesPage";
import { StockPage } from "@/pages/manage/StockPage";
import { MenuPage } from "@/pages/manage/MenuPage";
import { StaffPage } from "@/pages/manage/StaffPage";
import { ReportsPage } from "@/pages/manage/ReportsPage";
import { ManageStorefrontPage } from "@/pages/manage/ManageStorefrontPage";
import { SettingsPage } from "@/pages/manage/SettingsPage";
import { NotFound } from "@/routes/NotFound";
import {
  ADMIN_ROLES,
  DELIVERY_ROLES,
  FLOOR_VIEW_ROLES,
  MANAGER_ROLES,
  MENU_VIEW_ROLES,
  SETTINGS_ROLES,
  STOREFRONT_ROLES,
} from "@/features/restaurant/nav";

/**
 * The restaurant portal, and everything only it needs.
 *
 * Loaded on demand. This is the largest area in the app -- the menu builder,
 * the board, reports, the storefront editor -- and none of it is any use to
 * the customer scanning a QR code, who was downloading all of it before
 * being shown a menu.
 *
 * Its own <Routes> rather than entries in the main table, because that is
 * what keeps the boundary in one place: the import graph below this file is
 * the chunk. A page listed in the main table would be pulled back into the
 * shell by the very act of listing it.
 *
 * /manage/login and /manage/change-password are absent on purpose. They are
 * separate HTML entry points served by nginx and never reach this router.
 */
export default function ManageArea() {
  return (
    <Routes>
      <Route
        index
        element={<RequireStaff roles={FLOOR_VIEW_ROLES}><KitchenBoardPage /></RequireStaff>}
      />
      <Route
        path="deliveries"
        element={<RequireStaff roles={DELIVERY_ROLES}><DeliveriesPage /></RequireStaff>}
      />
      <Route
        path="stock"
        element={<RequireStaff roles={FLOOR_VIEW_ROLES}><StockPage /></RequireStaff>}
      />
      <Route
        path="menu"
        element={<RequireStaff roles={MENU_VIEW_ROLES}><MenuPage /></RequireStaff>}
      />
      <Route
        path="staff"
        element={<RequireStaff roles={ADMIN_ROLES}><StaffPage /></RequireStaff>}
      />
      <Route
        path="reports"
        element={<RequireStaff roles={MANAGER_ROLES}><ReportsPage /></RequireStaff>}
      />
      <Route
        path="storefront"
        element={<RequireStaff roles={STOREFRONT_ROLES}><ManageStorefrontPage /></RequireStaff>}
      />
      <Route
        path="settings"
        element={<RequireStaff roles={SETTINGS_ROLES}><SettingsPage /></RequireStaff>}
      />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
