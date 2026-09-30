/**
 * A restaurant's storefront address, worked out from where this page is open.
 *
 * The portal runs at admin.<root> and every storefront at <slug>.<root>, over
 * the same scheme and port. Reading both off the current address means the
 * link is right in every environment with nothing to configure: in
 * development https://spicehouse.zenoeats.local:8443, in production
 * https://spicehouse.zenoeats.com.
 *
 * This replaced a link hard-coded to http://<slug>.<VITE_ROOT_DOMAIN>:3000 --
 * the dev server's port over plain HTTP, with a domain fixed when the bundle
 * was built -- which in production pointed every restaurant at a dead
 * address.
 */
/** Production, then staging, then local. Longer names first so
 *  spicehouse.stg9.zenoeats.com is staging, not a subdomain of production. */
const PUBLIC_ROOTS = ["stg9.zenoeats.com", "zenoeats.com", "zenoeats.local"];

/** `<slug>.<root>`, including a dev port. The root is whichever of the
 *  public sites this page is open on. */
export function restaurantHost(
  slug: string,
  from: Pick<Location, "hostname" | "port"> = window.location,
): string {
  const host = from.hostname.replace(/^admin\./, "");
  const root = PUBLIC_ROOTS.find((name) => host === name || host.endsWith(`.${name}`));
  const base = root ?? host.split(".").slice(1).join(".");
  const port = from.port ? `:${from.port}` : "";
  return `${slug}.${base}${port}`;
}

export function storefrontUrl(
  slug: string,
  from: Pick<Location, "protocol" | "hostname" | "port"> = window.location,
): string {
  // Only the portal's own "admin." label is removed. Opened on the root
  // domain itself, the hostname already is the root.
  const root = from.hostname.replace(/^admin\./, "");
  const port = from.port ? `:${from.port}` : "";
  return `${from.protocol}//${slug}.${root}${port}`;
}
