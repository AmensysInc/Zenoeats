import { useEffect, useLayoutEffect, type ReactNode } from "react";
import { usePortalQuery } from "@/features/storefront/storefrontApi";
import { CustomerFooter } from "@/features/storefront/components/CustomerFooter";
import { attachFont, themeVariables } from "@/features/storefront/theme";
import { rememberBrand, rememberRestaurantName } from "@/utils/restaurantName";
import { useReferralCode } from "@/features/storefront/useReferralCode";
import { Outlet } from "react-router-dom";
import { PageColumnProvider } from "@/components/layout/pageColumn";

/**
 * The customer surface: cream page and forest primary (globals.css).
 *
 * Set on <html> rather than on a wrapper element, because the item and combo
 * sheets render outside the page's own tree and would otherwise keep the
 * staff colours. A layout effect, so the first paint is already the right
 * palette. Removed on the way out, so a restaurant portal reached by client
 * navigation is never left forest green.
 */
export function CustomerSurface({ children }: { children?: ReactNode }) {
  const { data } = usePortalQuery();
  // Every customer page, because a referral link may point at any of them:
  // somebody shares the menu, or a dish, not only the front door.
  useReferralCode();
  const theme = data?.storefront?.theme ?? null;
  // Kept for the sign-in pages, which are served without the app and would
  // otherwise show the platform's name until they had asked for this one.
  const restaurantName = data?.name;
  useEffect(() => rememberRestaurantName(restaurantName), [restaurantName]);
  const brand = data?.brand;
  useEffect(() => rememberBrand(brand), [brand]);
  useLayoutEffect(() => {
    const root = document.documentElement;
    const vars = themeVariables(theme);
    for (const [key, value] of Object.entries(vars)) root.style.setProperty(key, String(value));
    const removeFont = attachFont(theme?.font_pair ?? "default");
    return () => {
      for (const key of Object.keys(vars)) root.style.removeProperty(key);
      removeFont();
    };
  }, [theme]);
  useLayoutEffect(() => {
    const root = document.documentElement;
    root.dataset.surface = "customer";
    return () => {
      delete root.dataset.surface;
    };
  }, []);

  return (
    // The provider wraps both halves on purpose: it is how the page tells the
    // footer which column to line its allergen notice up with.
    <PageColumnProvider>
      {/* `children` for the one route that cannot be an Outlet: "/" decides
          between a storefront and the platform picker, so it renders the
          storefront into this layout itself. Every other customer route is
          nested under it as usual. */}
      {children ?? <Outlet />}
      <CustomerFooter />
    </PageColumnProvider>
  );
}
