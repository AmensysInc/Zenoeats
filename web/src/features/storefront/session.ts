import type { CustomerSession } from "@/types";

/**
 * The two things every page does with a customer session, in one place.
 *
 * Both were copied into each component that needed them -- the sign-in link
 * into AccountPanel and FavouriteButton, ending a session into AccountPanel
 * and CustomerAccountBar -- byte for byte. Copies of an auth detail are the
 * expensive kind: when the sign-out call changed from Clerk's signOut to this
 * API's own logout, every copy had to be found, and the one that was missed
 * is what put "set CLERK_PUBLISHABLE_KEY" in front of a customer.
 */

/** Where "Sign in" goes, carrying the page to come back to afterwards. */
export function signInHref(): string {
  const next = window.location.pathname + window.location.search;
  return `/account/sign-in?next=${encodeURIComponent(next)}`;
}

/**
 * End whichever kind of session this is.
 *
 * Both are cookies this API set and clears, so the only branch is which
 * endpoint clears which -- there is no identity provider to call. The caller
 * passes the two mutations because hooks belong to components, not to this.
 */
export async function endSession(
  session: Pick<CustomerSession, "is_guest"> | null | undefined,
  calls: { endGuest: () => Promise<unknown>; logout: () => Promise<unknown> },
): Promise<void> {
  if (session?.is_guest) await calls.endGuest();
  else await calls.logout();
}
