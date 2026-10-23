import type { ReactNode } from "react";
import { Shell, type NavItem } from "@/components/layout/Shell";
import { AdminSignOut } from "./AdminSignOut";

export const ADMIN_NAV: NavItem[] = [{ href: "/admin", label: "Restaurants", icon: "store" }];

/**
 * The platform portal's header, which is the same on every admin page.
 *
 * Pages used to pass their own title and action to Shell, so the orders page
 * carried a "Back" link where every other page carried "Sign out" -- an
 * operator two pages deep had no way out of the portal. The chrome is fixed
 * here instead: the wordmark and the nav both lead to /admin, and signing out
 * is always in the same place.
 *
 * One tab, so it stays in the header at every width: a phone gets the link
 * itself, never an empty hamburger.
 *
 * Only the login pages are exempt, and they are outside React entirely --
 * separate HTML entry points served by nginx, so they never mount this.
 */
export function AdminShell({ children }: { children: ReactNode }) {
  return (
    <Shell
      title="ZenoEats"
      brandMark
      titleHref="/admin"
      nav={ADMIN_NAV}
      mobileNav="inline"
      action={<AdminSignOut />}
    >
      {children}
    </Shell>
  );
}
