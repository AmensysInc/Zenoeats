/**
 * The name of the restaurant this address belongs to, remembered in the
 * browser so a page can show it before it has asked the API.
 *
 * The customer sign-in pages are plain HTML served by nginx, which knows
 * nothing about restaurants. They used to render the platform's "ZenoEats"
 * wordmark and swap in the restaurant's name once /portal answered, so every
 * customer saw the wrong name first -- for a second on a laptop, longer on a
 * phone on restaurant wifi. Almost everyone reaches those pages from the
 * storefront, which has already loaded the name, so it is kept here on the
 * way past and read back on arrival.
 *
 * A convenience, not a source of truth: the pages still ask /portal and
 * repaint with its answer, so a renamed restaurant is corrected in a moment.
 * localStorage is per origin, and every restaurant is its own subdomain, so
 * one restaurant's name is never shown on another's pages. Every access is
 * guarded -- a private window or blocked storage just means no head start.
 */

const KEY = "zenoeats.restaurant-name";

export function rememberedRestaurantName(): string | null {
  try {
    const name = window.localStorage.getItem(KEY);
    return name && name.trim() ? name : null;
  } catch {
    return null;
  }
}

export function rememberRestaurantName(name: string | null | undefined): void {
  if (!name || !name.trim()) return;
  try {
    window.localStorage.setItem(KEY, name);
  } catch {
    /* storage unavailable: the next page simply asks the API */
  }
}

/*
 * The restaurant's logo and name lettering, remembered alongside the name for
 * the same reason: so the sign-in pages show the restaurant's own mark first
 * rather than its initial. Read back through brandFrom, which drops anything
 * that is not a plain image path.
 */

const BRAND_KEY = "zenoeats.restaurant-brand";

export function rememberedBrand(): unknown {
  try {
    const raw = window.localStorage.getItem(BRAND_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function rememberBrand(brand: object | null | undefined): void {
  if (!brand) return;
  try {
    window.localStorage.setItem(BRAND_KEY, JSON.stringify(brand));
  } catch {
    /* storage unavailable: the next page simply asks the API */
  }
}
