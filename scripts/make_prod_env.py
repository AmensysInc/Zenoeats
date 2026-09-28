"""Write a production .env with every secret freshly generated, or check one.

    python3 scripts/make_prod_env.py --domain zenoeats.com --release v1.0.0
    python3 scripts/make_prod_env.py --staging --domain staging.zenoeats.com --release v1.0.0
    python3 scripts/make_prod_env.py --check .env

Run it on the server, in the repository, once. Standard library only: a fresh
VM needs nothing installed to run it.

Writing: starts from .env.example, so every setting keeps its explanation.
  - Generated: SESSION_SECRET, FIELD_ENCRYPTION_KEY, POSTGRES_PASSWORD, the
    three database role passwords and the two Redis passwords.
  - Set for production: ENV, the domain, image tags, email links, Sentry
    environment, two API workers.
  - Commented out: the development URLs. docker-compose.prod.yml builds every
    database and Redis URL from the passwords above.
  - Left EMPTY, marked "# FILL IN:": what only your accounts can supply
    (Clerk, Stripe, SendGrid, Sentry, ADMIN_USERS). Empty on purpose: the API
    refuses to start without the required ones, where a placeholder word
    would count as set and get through.

It never overwrites a file. A second run over a live .env would replace
FIELD_ENCRYPTION_KEY, and every pickup PIN encrypted with the old one would be
unreadable from then on.

Checking: lists every required value still empty, any test key, development
password or .local domain -- what the API's startup check and the
production compose file would refuse, found before a deploy rather than
during one.
"""

import argparse
import base64
import os
import re
import secrets
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / ".env.example"
OWNER = "haswanth13901"

# Hex, because these are spliced into connection URLs unescaped.
PASSWORDS = (
    "POSTGRES_PASSWORD",
    "ZENOEATS_MIGRATE_PASSWORD",
    "ZENOEATS_APP_PASSWORD",
    "ZENOEATS_SYSTEM_PASSWORD",
    "REDIS_BROKER_PASSWORD",
    "REDIS_RUNTIME_PASSWORD",
)

# Built by docker-compose.prod.yml from the passwords; a value here is ignored
# by the production containers and would only mislead whoever reads the file.
DEV_ONLY = re.compile(
    r"^(DATABASE_URL_(APP|SYSTEM|MIGRATE)|CELERY_BROKER_URL|CELERY_RESULT_BACKEND"
    r"|REDIS_RUNTIME_URL|DOCKER_\w+)$"
)

# key -> where it comes from. Empty until filled; `--check` reports them.
FROM_ACCOUNTS = {
    "CLERK_SECRET_KEY": "Clerk production instance > API keys (sk_live_...)",
    "CLERK_JWKS_URL": "Clerk > API keys > JWKS URL",
    "CLERK_ISSUER": "Clerk > API keys > Frontend API URL",
    "CLERK_WEBHOOK_SECRET": "Clerk > Webhooks > the endpoint's signing secret",
    "VITE_CLERK_PUBLISHABLE_KEY": "Clerk > API keys (pk_live_...)",
    "STRIPE_SECRET_KEY": "Stripe live mode > Developers > API keys (sk_live_...)",
    "STRIPE_PUBLISHABLE_KEY": "Stripe live mode > Developers > API keys (pk_live_...)",
    "STRIPE_CONNECT_WEBHOOK_SECRET": "Stripe > Webhooks, CONNECT endpoint > signing secret",
    "ADMIN_USERS": "docker run --rm -it <API_IMAGE> python scripts/hash_password.py you@example.com",
    "SENDGRID_API_KEY": "SendGrid > Settings > API Keys (Mail Send), once the sender is verified",
    "SENTRY_DSN": "Sentry > project > Client Keys (DSN)",
}
# Without these the API refuses to start, or checkout cannot take a payment.
REQUIRED = (
    "ROOT_DOMAIN", "SESSION_SECRET", "FIELD_ENCRYPTION_KEY", *PASSWORDS,
    "API_IMAGE", "WEB_IMAGE",
    "CLERK_SECRET_KEY", "CLERK_JWKS_URL", "CLERK_ISSUER", "VITE_CLERK_PUBLISHABLE_KEY",
    "STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY", "STRIPE_CONNECT_WEBHOOK_SECRET",
    "ADMIN_USERS",
)
# Wanted for a credible launch; reported, but not as blockers.
RECOMMENDED = ("CLERK_WEBHOOK_SECRET", "SENDGRID_API_KEY", "SENTRY_DSN")

LINE = re.compile(r"^(#\s*)?([A-Z][A-Z0-9_]*)=(.*)$")
# A setting, live or commented out with a single "# ". Examples inside
# explanatory comments are indented further and do not match.
SETTING = re.compile(r"^(# )?([A-Z][A-Z0-9_]*)=(.*)$")


def generated() -> dict[str, str]:
    values = {name: secrets.token_hex(32) for name in PASSWORDS}
    values["SESSION_SECRET"] = base64.b64encode(secrets.token_bytes(32)).decode()
    # A Fernet key is 32 random bytes, url-safe base64: no dependency needed.
    values["FIELD_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    return values


def production_values(domain: str, release: str, staging: bool) -> dict[str, str]:
    image = f"ghcr.io/{OWNER}/zenoeats-mvp"
    return {
        "ENV": "production",
        "ROOT_DOMAIN": domain,
        "AUTH_DEV_BYPASS": "false",
        "ALLOW_TEST_KEYS": "true" if staging else "false",
        "STOREFRONT_URL_TEMPLATE": "https://{slug}.{root_domain}",
        "EMAIL_FROM": f"Zenoeats <orders@{domain}>" if domain else "",
        "SENTRY_ENVIRONMENT": "staging" if staging else "production",
        "RELEASE": release,
        "API_IMAGE": f"{image}/api:{release}" if release else "",
        "WEB_IMAGE": f"{image}/web:{release}" if release else "",
        "API_WORKERS": "2",
    }


def write(out: Path, domain: str, release: str, staging: bool) -> None:
    if out.exists():
        sys.exit(f"{out} exists; not overwriting it. Replacing FIELD_ENCRYPTION_KEY "
                 "would make every stored pickup PIN unreadable.")

    values = {**generated(), **production_values(domain, release, staging)}
    for key in FROM_ACCOUNTS:
        values[key] = ""
    template = TEMPLATE.read_text(encoding="utf-8").splitlines()

    # Where each value goes: the key's live setting line, or failing that a
    # commented-out setting ("# API_IMAGE=..."). Never an indented example
    # inside a comment ("#   EMAIL_FROM=..."), which only documents one.
    target: dict[str, int] = {}
    for index, raw in enumerate(template):
        match = SETTING.match(raw)
        if match and match.group(2) in values:
            key, live = match.group(2), not match.group(1)
            if key not in target or (live and template[target[key]].startswith("#")):
                target[key] = index
    at = {index: key for key, index in target.items()}

    lines: list[str] = []
    for index, raw in enumerate(template):
        match = SETTING.match(raw)
        live_key = match.group(2) if match and not match.group(1) else None
        if index in at:
            key = at[index]
            if key in FROM_ACCOUNTS:
                lines.append(f"# FILL IN: {FROM_ACCOUNTS[key]}")
            lines.append(f"{key}={values[key]}")
        elif live_key and DEV_ONLY.match(live_key):
            lines.append(f"# {live_key}= (built by docker-compose.prod.yml)")
        elif live_key in values:
            lines.append(f"# {raw}  (superseded)")
        else:
            lines.append(raw)

    missing = [k for k in values if k not in target]
    if missing:
        lines += ["", "# ---- Set by make_prod_env.py ----"]
        lines += [f"{k}={values[k]}" for k in missing]

    # Created 0600 from the first byte, not chmod-ed after writing.
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")

    print(f"Wrote {out} (readable by its owner only).")
    print("Escrow it now: FIELD_ENCRYPTION_KEY cannot be recovered from anywhere else.")
    report(read(out))


def read(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        match = LINE.match(raw)
        if match and not match.group(1):
            values[match.group(2)] = match.group(3).strip()
    return values


def problems(values: dict[str, str]) -> tuple[list[str], list[str]]:
    blocking: list[str] = []
    for key in REQUIRED:
        if not values.get(key):
            hint = FROM_ACCOUNTS.get(key, "")
            blocking.append(f"{key} is empty" + (f" -- {hint}" if hint else ""))

    if values.get("ENV") != "production":
        blocking.append("ENV is not production")
    if values.get("ROOT_DOMAIN", "").endswith(".local"):
        blocking.append("ROOT_DOMAIN is a development domain")
    if values.get("AUTH_DEV_BYPASS", "false").lower() != "false":
        blocking.append("AUTH_DEV_BYPASS is on")
    if values.get("DATABASE_URL_MIGRATE"):
        blocking.append("DATABASE_URL_MIGRATE is set; only the migrate job may hold it")
    for key, value in values.items():
        if value.endswith("_dev_pw") or "_dev_pw@" in value:
            blocking.append(f"{key} carries a development password")
    if values.get("ALLOW_TEST_KEYS", "false").lower() != "true":
        for key in ("STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY", "CLERK_SECRET_KEY",
                    "VITE_CLERK_PUBLISHABLE_KEY"):
            if "_test_" in values.get(key, "")[:8]:
                blocking.append(f"{key} is a test key")
    for key in PASSWORDS:
        if values.get(key) and not re.fullmatch(r"[0-9a-f]{32,}", values[key]):
            blocking.append(f"{key} is not long hex; it goes into a URL unescaped")

    advisory = [f"{key} is empty -- {FROM_ACCOUNTS[key]}" for key in RECOMMENDED
                if not values.get(key)]
    return blocking, advisory


def report(values: dict[str, str]) -> int:
    blocking, advisory = problems(values)
    if blocking:
        print("\nBefore this can run in production:")
        print("\n".join(f"  - {p}" for p in blocking))
    if advisory:
        print("\nRecommended for launch:")
        print("\n".join(f"  - {p}" for p in advisory))
    if not blocking and not advisory:
        print("\nNothing missing.")
    return 1 if blocking else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", type=Path, metavar="ENV_FILE", help="check a file instead")
    parser.add_argument("--domain", default="", help="the root domain, e.g. zenoeats.com")
    parser.add_argument("--release", default="", help="the release tag to run, e.g. v1.0.0")
    parser.add_argument("--staging", action="store_true",
                        help="a staging environment: allows test keys")
    parser.add_argument("--out", type=Path, default=REPO / ".env")
    args = parser.parse_args()

    if args.check:
        return report(read(args.check))
    write(args.out, args.domain.strip().lower(), args.release.strip(), args.staging)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
