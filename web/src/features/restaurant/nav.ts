import type { NavItem } from "@/components/layout/Shell";

/**
 * Who may open what in the restaurant portal.
 *
 * These mirror the API's role checks (MANAGE, ANY_STAFF, STAFF_ADMIN and the
 * four support lists in restaurant.py), which are what actually decide. They
 * exist so the portal offers each person the screens their role can use: a
 * kitchen login used to see every tab and meet "Not authorized" on three of
 * the four.
 */
// The floor: everyone who works in the restaurant. A driver is not one of
// them -- they see the orders assigned to them and nothing else of the portal
// -- and neither is IT support, which works on the restaurant rather than in
// it. Both appear only in the lists that name them.
export const ALL_STAFF_ROLES = ["ADMIN", "MANAGER", "KITCHEN", "CASHIER"];
export const MANAGER_ROLES = ["ADMIN", "MANAGER"];
export const ADMIN_ROLES = ["ADMIN"];
export const DELIVERY_ROLES = ["ADMIN", "MANAGER", "DRIVER"];

/**
 * IT support, in the same four lists the API keeps.
 *
 * Two of them open a screen read-only. A tab this role can reach is not a tab
 * it can act on, so the pages behind FLOOR_VIEW_ROLES and MENU_VIEW_ROLES ask
 * `canActOnOrders` and `canEditMenu` before offering a control -- otherwise
 * support would be shown buttons the API answers 403 to.
 */
export const FLOOR_VIEW_ROLES = [...ALL_STAFF_ROLES, "IT_SUPPORT"];
export const MENU_VIEW_ROLES = [...MANAGER_ROLES, "IT_SUPPORT"];
export const STOREFRONT_ROLES = [...MANAGER_ROLES, "IT_SUPPORT"];
export const SETTINGS_ROLES = [...ADMIN_ROLES, "IT_SUPPORT"];

export type ManageNavItem = NavItem & { roles: string[] };

export const MANAGE_NAV: ManageNavItem[] = [
  { href: "/manage", label: "Kitchen", icon: "kitchen", roles: FLOOR_VIEW_ROLES },
  { href: "/manage/deliveries", label: "Deliveries", icon: "bag", roles: DELIVERY_ROLES },
  { href: "/manage/stock", label: "Stock", icon: "stock", roles: FLOOR_VIEW_ROLES },
  { href: "/manage/menu", label: "Menu", icon: "menu", roles: MENU_VIEW_ROLES },
  { href: "/manage/staff", label: "Staff", icon: "team", roles: ADMIN_ROLES },
  { href: "/manage/reports", label: "Reports", icon: "report", roles: MANAGER_ROLES },
  { href: "/manage/storefront", label: "Storefront", icon: "store", roles: STOREFRONT_ROLES },
  { href: "/manage/settings", label: "Settings", icon: "edit", roles: SETTINGS_ROLES },
];

/** The tabs this role can use. No role yet -- the guard has not answered --
 *  shows only the two screens every role but a driver's can open, so nothing
 *  flashes and disappears. */
export function navFor(roleCode: string | null, storefrontEnabled = false): NavItem[] {
  if (!roleCode) return MANAGE_NAV.filter((item) => item.roles === FLOOR_VIEW_ROLES);
  return MANAGE_NAV.filter((item) => item.roles.includes(roleCode) && (item.href !== "/manage/storefront" || storefrontEnabled));
}

/** Where this role starts, which for a driver is not the kitchen board. */
export function homeFor(roleCode: string | null): string {
  return navFor(roleCode)[0]?.href ?? "/manage";
}

export function isDriver(roleCode: string | null): boolean {
  return roleCode === "DRIVER";
}

export function canManage(roleCode: string | null): boolean {
  return !!roleCode && MANAGER_ROLES.includes(roleCode);
}

/** Whether this role may act on a live order or flip an item sold out, as
 *  opposed to only reading the board and the stock list. Acting includes
 *  handing an order over without its PIN and cancelling a paid one. IT
 *  support reads both to work out what a customer is seeing and changes
 *  neither. */
export function canActOnOrders(roleCode: string | null): boolean {
  return !!roleCode && ALL_STAFF_ROLES.includes(roleCode);
}

/** Whether this role may edit the menu, as opposed to only reading it. The
 *  builder's four editing tabs are hidden without it, leaving the preview. */
export function canEditMenu(roleCode: string | null): boolean {
  return canManage(roleCode);
}
