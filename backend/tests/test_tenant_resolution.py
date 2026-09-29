"""Hostname to slug. This is the only thing that decides tenant identity."""

from app.core.tenant import extract_slug


def test_extracts_subdomain_slug():
    assert extract_slug("spicehouse.zenoeats.local") == "spicehouse"
    assert extract_slug("spicehouse.zenoeats.local:8443") == "spicehouse"
    assert extract_slug("SpiceHouse.ZenoEats.Local") == "spicehouse"


def test_root_domain_is_not_a_tenant():
    assert extract_slug("zenoeats.local") is None
    assert extract_slug("zenoeats.local:8443") is None


def test_reserved_labels_are_not_tenants():
    for reserved in ["www", "api", "admin", "media", "static"]:
        assert extract_slug(f"{reserved}.zenoeats.local") is None


def test_foreign_domains_rejected():
    assert extract_slug("spicehouse.attacker.com") is None
    assert extract_slug("zenoeats.local.attacker.com") is None
    assert extract_slug("") is None
    assert extract_slug(None) is None


def test_nested_subdomains_rejected():
    # a.b.zenoeats.local would be ambiguous. Refuse rather than guess.
    assert extract_slug("a.b.zenoeats.local") is None


def test_clerk_subdomains_never_resolve_to_a_restaurant():
    """Customer sign-in in production depends on these DNS names belonging to
    Clerk, so no restaurant can be created on them or resolved from them."""
    for name in ("clerk", "accounts", "clkmail"):
        assert extract_slug(f"{name}.zenoeats.local") is None
