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
