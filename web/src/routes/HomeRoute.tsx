import { Booting } from "@/components/layout/guardParts";
import { CustomerSurface } from "@/components/layout/CustomerSurface";
import { LocationsPage } from "@/pages/storefront/LocationsPage";
import { StorefrontPage } from "@/pages/storefront/StorefrontPage";
import { usePortalQuery } from "@/features/storefront/storefrontApi";
import { ApiError } from "@/services/apiClient";

/**
 * What "/" is depends on the hostname it was asked for.
 *
 *   spicehouse.zenoeats.com   a restaurant's storefront
 *   zenoeats.com              the platform root: which location?
 *
 * The server decides, not the browser. Resolving a subdomain means knowing
 * the root domain, which labels are reserved and which slugs exist -- all of
 * which live in backend/app/core/tenant.py. Re-deriving any of that from
 * `location.hostname` would be a second implementation to keep in step, and
 * it would be wrong first on exactly the cases that matter (www, a reserved
 * label, a slug that was archived).
 *
 * So the question is asked the way every other page asks it: fetch the
 * portal. A host with a restaurant answers with one; the platform root
 * answers RESTAURANT_NOT_FOUND, and that is the signal. No extra request --
 * the storefront loads the portal on this route anyway.
 *
 * An unknown subdomain lands on the picker too. Someone who mistyped a
 * storefront is better served by the list of real ones than by an error.
 */
export function HomeRoute() {
  const portal = usePortalQuery();

  // Distinguished from "no data yet" deliberately: a transient failure must
  // not flash the location picker at someone who asked for a storefront. The
  // base query already retries a read twice before the error reaches here.
  const noRestaurant =
    portal.error instanceof ApiError && portal.error.code === "RESTAURANT_NOT_FOUND";

  if (noRestaurant) return <LocationsPage />;

  // Still waiting, and not yet known to be the platform root. The storefront
  // renders its own loading and error states from the same query, so hand off
  // as soon as there is anything to hand off -- this covers only the first
  // paint before the query has settled either way.
  if (portal.isLoading) return <Booting />;

  return (
    <CustomerSurface>
      <StorefrontPage />
    </CustomerSurface>
  );
}
