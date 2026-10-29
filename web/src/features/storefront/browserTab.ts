/**
 * What the browser tab says, and what it wears.
 *
 * Every page used to carry the one title baked into index.html, so a
 * customer with the menu, their cart and an order open had three tabs
 * reading "Order pickup" and no way to tell them apart. A tab is a
 * navigation control on a phone as much as on a laptop, and it was
 * answering none of the questions asked of it.
 *
 * TITLE IS "PAGE - RESTAURANT". The page comes first because that is what
 * differs between a customer's open tabs; the restaurant follows because
 * several of them may be open at once. A restaurant whose name is not known
 * yet gets the page alone rather than a placeholder standing in for it.
 *
 * THE ICON IS THE RESTAURANT'S OWN where it has one. The storefront belongs
 * to the restaurant, not to ZenoEats, so the platform mark is the fallback
 * and never the first choice -- the same rule the header follows.
 *
 * Free of React so the plain-HTML sign-in pages can use it too, the way
 * utils/restaurantName.ts is.
 */

/** Pages whose path is enough to name them. */
const EXACT: Record<string, string> = {
  "/": "Menu",
  "/cart": "Cart",
  "/checkout": "Checkout",
  "/profile": "Your account",
  "/account/profile": "Your account",
};

/** Pages carrying an id, named by what they are rather than which one. */
const PREFIXES: [string, string][] = [
  // Before /checkout, which would otherwise match it first.
  ["/checkout/pay/", "Payment"],
  ["/orders/", "Your order"],
  ["/manage", "Manage"],
  ["/admin", "Admin"],
];

/**
 * What to call the page at this path, or null where there is nothing useful
 * to say -- a 404, or a route nobody has named yet. Null means the tab shows
 * the restaurant alone, which is still better than the wrong page name.
 */
export function pageName(pathname: string): string | null {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  if (path in EXACT) return EXACT[path] ?? null;
  for (const [prefix, name] of PREFIXES) {
    if (path === prefix || path.startsWith(prefix)) return name;
  }
  return null;
}

/**
 * The whole title.
 *
 * Falls back to the platform name only when there is no restaurant at all --
 * the location picker on the root domain, which belongs to no restaurant.
 * On a storefront the restaurant's name is known or soon will be, and a tab
 * briefly reading "Menu" is better than one reading somebody else's brand.
 */
export function documentTitle(path: string, restaurant: string | null): string {
  const page = pageName(path);
  const name = restaurant?.trim();
  if (name && page) return `${page} - ${name}`;
  if (name) return name;
  if (page) return page;
  return "ZenoEats";
}

/** The tab icon: a restaurant's own logo, or the platform mark. */
export const FALLBACK_ICON = "/favicon.svg";

/**
 * Point the tab at an icon, making the link element if the document has
 * none.
 *
 * Rewrites the href of a single link rather than appending another: browsers
 * differ on which of several they honour, and a page that accumulated one
 * per navigation would eventually pick at random.
 */
export function setFavicon(href: string): void {
  if (typeof document === "undefined") return;
  let link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
  if (!link) {
    link = document.createElement("link");
    link.rel = "icon";
    document.head.appendChild(link);
  }
  // A logo is whatever the restaurant uploaded -- webp, png, jpeg -- so the
  // type is left for the browser to sniff rather than asserted wrongly.
  if (href.endsWith(".svg")) link.type = "image/svg+xml";
  else link.removeAttribute("type");
  if (link.href !== href) link.href = href;
}
