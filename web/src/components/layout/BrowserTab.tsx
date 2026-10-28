import { useEffect } from "react";
import { useLocation, Outlet, ScrollRestoration } from "react-router-dom";
import { usePortalQuery } from "@/features/storefront/storefrontApi";
import { documentTitle, FALLBACK_ICON, setFavicon } from "@/features/storefront/browserTab";
import { rememberedRestaurantName } from "@/utils/restaurantName";

/**
 * Keeps the browser tab honest: the page you are on, the restaurant you are
 * on it at, the restaurant's own icon -- and where the page opens scrolled
 * to.
 *
 * A layout route wrapping everything rather than a hook each page calls.
 * Every page needs this and a page that forgot would quietly show the title
 * of whichever page you were on before -- the kind of bug nobody files and
 * everybody notices.
 *
 * THE NAME COMES FROM TWO PLACES, and the order matters. localStorage has
 * it the instant the document parses, so a returning customer never sees a
 * wrong title at all; /portal is the authority and corrects it a moment
 * later if the restaurant has been renamed. That is the arrangement
 * utils/restaurantName.ts already set up for the sign-in pages, used here
 * for the same reason.
 *
 * The portal is not asked for on the operator portals. They are a different
 * surface, served on hostnames where /portal resolves to no restaurant, and
 * a request that can only 404 is not worth making to title a tab.
 *
 * SCROLL IS A TAB-LEVEL CONCERN TOO, and it was nobody's. A client
 * navigation changes the DOM without touching the scroll offset, so tapping
 * the cart from halfway down a long menu opened the cart halfway down -- on
 * a phone, below the fold, looking like an empty page with a footer. One
 * ScrollRestoration puts new navigations at the top and gives back and
 * forward the position they left, which is the pair of behaviours a browser
 * would have had if the pages were documents.
 */
export function BrowserTab() {
  const { pathname } = useLocation();
  const operator = pathname.startsWith("/manage") || pathname.startsWith("/admin");
  const { data } = usePortalQuery(undefined, { skip: operator });

  // The API's answer, or what the last visit left behind. Null on the
  // location picker, which belongs to no restaurant.
  const restaurant = operator ? null : data?.name ?? rememberedRestaurantName();
  const logo = operator ? null : data?.brand?.logo_url ?? null;

  useEffect(() => {
    document.title = documentTitle(pathname, restaurant);
  }, [pathname, restaurant]);

  useEffect(() => {
    setFavicon(logo || FALLBACK_ICON);
  }, [logo]);

  return (
    <>
      <ScrollRestoration />
      <Outlet />
    </>
  );
}
