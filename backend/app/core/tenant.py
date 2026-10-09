"""Hostname to restaurant resolution.

Section 2.2: a restaurant subdomain is resolved by the application, not by
creating a DNS record per restaurant. Wildcard DNS points *.zenoeats.com at
the origin and we map the leading label to a restaurant.

The client may send a restaurant id. We never authorize from it.
"""

from app.config import settings

# Customer sessions may be minted on these sites, whichever one this process
# is deployed as. Production is zenoeats.com; staging is stg9.zenoeats.com.
# ROOT_DOMAIN is added as well, so local development (zenoeats.local) stays
# allowed without being listed here.
ALLOWED_ORIGIN_ROOTS = (
    "zenoeats.com",
    "stg9.zenoeats.com",
)


def allowed_origin_roots() -> tuple[str, ...]:
    """Roots a browser page of ours may be served on.

    A host is allowed when it equals one of these or is a subdomain of one.
    """
    seen: list[str] = []
    for root in (settings.ROOT_DOMAIN, *ALLOWED_ORIGIN_ROOTS):
        root = root.strip().lower().rstrip(".")
        if root and root not in seen:
            seen.append(root)
    return tuple(seen)


def host_on_allowed_origin(host: str) -> bool:
    """True when `host` is one of our roots or a subdomain of one."""
    name = host.strip().lower().rstrip(".")
    if not name:
        return False
    return any(name == root or name.endswith("." + root) for root in allowed_origin_roots())


RESERVED_SLUGS = {
    "www", "api", "admin", "app", "media", "static", "assets",
    "mail", "staging", "dev", "internal", "status", "docs",
    # Clerk's production instance lives on these subdomains of ROOT_DOMAIN:
    # its Frontend API, its hosted account pages, and the domain its sign-in
    # emails are sent from. A restaurant given one of these slugs would take
    # the DNS name customer sign-in depends on.
    "clerk", "accounts", "clkmail",
}


def admin_host() -> str:
    """The one hostname the platform portal is served on."""
    return f"admin.{settings.ROOT_DOMAIN.lower()}"


def host_of(header: str | None) -> str | None:
    """The hostname in a Host or Origin header, without scheme or port."""
    if not header:
        return None
    host = header.split("//")[-1].split("/")[0]
    return host.rsplit(":", 1)[0].strip().lower().rstrip(".") or None


def extract_slug(host_header: str | None) -> str | None:
    """Pull the restaurant slug out of a Host header.

    spicehouse.zenoeats.com          -> "spicehouse"
    spicehouse.zenoeats.local:8443   -> "spicehouse"
    zenoeats.com                     -> None (platform root)
    """
    if not host_header:
        return None

    host = host_header.split(":")[0].strip().lower().rstrip(".")
    root = settings.ROOT_DOMAIN.lower()

    if host == root or not host.endswith(f".{root}"):
        return None

    label = host[: -(len(root) + 1)]
    if "." in label or not label or label in RESERVED_SLUGS:
        return None
    return label


def platform_url_for(request) -> str:
    """The platform root, as the browser making this request would reach it.

    Derived from the request rather than from PLATFORM_URL_TEMPLATE, because
    the template is a second source of truth that silently disagrees with how
    the API is actually being reached. Development has two front doors --
    nginx on https://<slug>.zenoeats.local:8443, and the Vite server on
    http://<slug>.localhost:3000 for anyone without hosts entries -- and a
    template pinned to one produced an "All locations" link that pointed at
    the other, failing with ERR_SSL_UNRECOGNIZED_NAME_ALERT.

    The browser has already told us the scheme and port it is using, so the
    sibling host is the same two with the root domain in place of the
    restaurant's. nginx forwards both (X-Forwarded-Proto / -Host), and the
    plain Host header carries the port on the direct path.

    The template still governs anywhere there is no request to read -- an
    email, a worker -- where a configured absolute URL is the only option.
    """
    forwarded = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    _, _, port = forwarded.partition(":")
    scheme = (
        request.headers.get("x-forwarded-proto")
        or getattr(getattr(request, "url", None), "scheme", None)
        or "https"
    ).split(",")[0].strip()

    root = settings.ROOT_DOMAIN.strip().lower()
    return f"{scheme}://{root}:{port}" if port else f"{scheme}://{root}"


def storefront_url_for(request, slug: str) -> str:
    """A restaurant's storefront, as the browser making this request reaches it.

    The sibling of platform_url_for, and broken in the same way before it:
    the location picker built these from STOREFRONT_URL_TEMPLATE, pinned to
    https://<slug>.<root>:8443, so every card on the picker served at
    http://localhost:3000 linked to a host nginx has no certificate for.
    Clicking Jr's Corner answered ERR_SSL_UNRECOGNIZED_NAME_ALERT.

    Scheme and port come from the request; only the leading label changes.
    """
    base = platform_url_for(request)
    scheme, _, host_and_port = base.partition("://")
    return f"{scheme}://{slug}.{host_and_port}"
