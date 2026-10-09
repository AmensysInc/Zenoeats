"""Configuration that must be right before the API may serve anything.

Checked once at import of app.main. In production a problem is fatal: an API
that boots with an empty SESSION_SECRET hands out admin sessions anyone can
forge, and one without Clerk configured turns every customer away -- both
are better discovered as a failed deploy than as an incident. Outside
production the same findings are logged, so development keeps working with a
partial .env.
"""

import logging

from cryptography.fernet import Fernet

from app.config import Settings

log = logging.getLogger(__name__)

# 32 bytes of randomness, base64 encoded, is 44 characters. Anything much
# shorter was typed by a person.
MIN_SESSION_SECRET_LENGTH = 32

# Secrets this repository publishes on purpose, for CI and the QA runner
# (.github/workflows/ci.yml, scripts/verify_redesign.py). The repository is
# public, so each is known to everyone; copied into a production .env, one
# makes every session forgeable or every pickup PIN readable. Long and
# well-formed, so no other check here would notice.
PUBLISHED_SECRETS = frozenset({
    "kZ0nQx3Yk8vJ9pL2mN7bR4tS6wU1cE5gH8jK0aD3fI4=",
    "ci-only-session-secret-not-used-anywhere-real-000",
    "redesign-isolated-tests-only-no-production-access",
})


def configuration_problems(settings: Settings) -> list[str]:
    """Everything wrong with a production configuration, worst first."""
    problems: list[str] = []

    if settings.AUTH_DEV_BYPASS:
        problems.append("AUTH_DEV_BYPASS must never be enabled in production.")

    if settings.DATABASE_URL_MIGRATE:
        problems.append(
            "DATABASE_URL_MIGRATE is set in a runtime process. It carries the schema "
            "owner's credentials; give it only to the migration job."
        )

    if len(settings.SESSION_SECRET) < MIN_SESSION_SECRET_LENGTH:
        problems.append(
            f"SESSION_SECRET must be at least {MIN_SESSION_SECRET_LENGTH} characters "
            "(generate one with: openssl rand -base64 32). Admin and staff session "
            "cookies are forgeable without it."
        )

    for name in ("SESSION_SECRET", "FIELD_ENCRYPTION_KEY"):
        if getattr(settings, name) in PUBLISHED_SECRETS:
            problems.append(
                f"{name} is a value published in this repository for CI; "
                "anyone can read it. Generate a new one (scripts/make_prod_env.py)."
            )

    for name in ("DATABASE_URL_APP", "DATABASE_URL_SYSTEM"):
        if "_dev_pw" in getattr(settings, name):
            problems.append(
                f"{name} uses a development password (*_dev_pw), which is in "
                "infra/postgres/01-roles.sql for anyone to read."
            )

    try:
        Fernet(settings.FIELD_ENCRYPTION_KEY.encode())
    except (ValueError, TypeError):
        problems.append(
            "FIELD_ENCRYPTION_KEY is not a valid Fernet key (generate one with: make key)."
        )

    # Clerk is optional now and this is no longer "nobody can sign in": the
    # API holds customer credentials itself (core/customer_auth.py), so email
    # and password and guest ordering both work with none of these set. What
    # is lost is the social sign-in Clerk brokers, so say that instead -- a
    # warning that overstates what is broken gets ignored along with the ones
    # that do not.
    #
    # Reported only when the set is half-filled, which is the configuration
    # that genuinely fails: a publishable key on the page and no secret behind
    # it means a Google button that leads nowhere.
    clerk = {name: bool(getattr(settings, name)) for name in
             ("CLERK_JWKS_URL", "CLERK_ISSUER", "CLERK_SECRET_KEY")}
    if any(clerk.values()) and not all(clerk.values()):
        missing = ", ".join(sorted(n for n, present in clerk.items() if not present))
        problems.append(
            f"Clerk is half-configured ({missing} missing); social sign-in will fail. "
            "Set all three or none -- email, password and guest ordering need none of them."
        )

    for name in ("STRIPE_SECRET_KEY", "STRIPE_CONNECT_WEBHOOK_SECRET"):
        value = getattr(settings, name)
        if not value or "replace_me" in value:
            problems.append(f"{name} is not set; no order can be paid or confirmed.")

    problems.extend(_key_mode_problems(settings))

    if not settings.ADMIN_USERS.strip():
        problems.append("ADMIN_USERS is empty; nobody can sign in to the super admin portal.")

    if settings.ROOT_DOMAIN.endswith(".local"):
        problems.append(
            f"ROOT_DOMAIN is {settings.ROOT_DOMAIN!r}, a development domain; "
            "storefronts would resolve to no restaurant."
        )

    return problems


def _key_mode(value: str) -> str | None:
    """'test' or 'live' from a Stripe or Clerk key's prefix; None if unset."""
    if "_test_" in value[:8]:
        return "test"
    if "_live_" in value[:8]:
        return "live"
    return None


def _key_mode_problems(settings: Settings) -> list[str]:
    keys = {
        name: _key_mode(getattr(settings, name))
        for name in ("STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY", "CLERK_SECRET_KEY")
    }
    problems: list[str] = []

    # A test publishable key beside a live secret key cannot confirm a single
    # payment: the browser and the server are talking to different Stripes.
    stripe_modes = {keys["STRIPE_SECRET_KEY"], keys["STRIPE_PUBLISHABLE_KEY"]} - {None}
    if len(stripe_modes) > 1:
        problems.append(
            "STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY are from different modes "
            "(one test, one live); checkout cannot confirm a payment."
        )

    on_test = sorted(name for name, mode in keys.items() if mode == "test")
    if on_test and not settings.ALLOW_TEST_KEYS:
        problems.append(
            f"{', '.join(on_test)} {'is a test key' if len(on_test) == 1 else 'are test keys'}; "
            "production would take orders and collect no money. Use live keys, "
            "or set ALLOW_TEST_KEYS=true on staging."
        )
    return problems


def enforce(settings: Settings) -> None:
    problems = configuration_problems(settings)
    if not problems:
        return

    if settings.ENV == "production":
        raise RuntimeError(
            "Refusing to start with an unsafe production configuration:\n  - "
            + "\n  - ".join(problems)
        )

    # Development routinely runs without Stripe or admin credentials. Say what
    # production would refuse, once, and carry on.
    log.info(
        "configuration would not pass production checks: %s",
        " | ".join(problems),
    )
