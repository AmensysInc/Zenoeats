from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# The .env file lives at the repository root, not inside backend/. Resolving
# it from this file's location means alembic, uvicorn, celery and pytest all
# find the same configuration regardless of which directory they run from.
#
# Inside Docker this path resolves to a file that does not exist, which is
# correct: compose passes the same values as real environment variables via
# env_file:, and pydantic-settings reads those first.
REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    # --- Platform ---------------------------------------------------------
    ENV: str = "development"
    ROOT_DOMAIN: str = "zenoeats.local"
    LOG_LEVEL: str = "INFO"

    # --- Database ---------------------------------------------------------
    # Request-path role. Non-owner, NOBYPASSRLS, FORCE RLS applies.
    DATABASE_URL_APP: str
    # Narrow cross-tenant discovery role for workers and the webhook inbox.
    DATABASE_URL_SYSTEM: str
    # Alembic only. Owns the schema. Optional here because runtime processes
    # -- the API, the worker, beat -- must not be given it at all: a leaked
    # API container would otherwise hold credentials that can drop tables and
    # switch off row-level security. Only the migration job sets it, and a
    # production API refuses to start if it is present (core/startup_checks).
    DATABASE_URL_MIGRATE: str = ""

    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 10

    # --- Redis / Celery ---------------------------------------------------
    CELERY_BROKER_URL: str = "redis://redis-broker:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://redis-broker:6379/1"
    # Cache and rate limiting. A separate instance from the broker on
    # purpose: this one evicts under pressure, the broker never does.
    REDIS_RUNTIME_URL: str = "redis://redis-runtime:6379/0"

    # --- Proxies ----------------------------------------------------------
    # The networks our own reverse proxies connect from. Forwarding headers
    # (X-Real-IP, X-Forwarded-For) are believed only on connections from
    # these; from anywhere else they are ignored and the socket address is
    # the client. The default covers loopback and the private ranges Docker
    # and a VPC use. Narrow it in production to exactly where nginx runs.
    TRUSTED_PROXY_CIDRS: str = "127.0.0.1/32,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"

    # --- Clerk (customers only) --------------------------------------------
    # Verifies customer session tokens. Staff and platform admins never touch
    # Clerk.
    CLERK_JWKS_URL: str = ""
    CLERK_ISSUER: str = ""
    # Backend API key. Used to read a new customer's email and name the first
    # time they reach the API, so receipts have a real address without waiting
    # for a webhook -- which, in local development, never arrives at all.
    CLERK_SECRET_KEY: str = ""
    CLERK_WEBHOOK_SECRET: str = ""
    # Set true only for local development without a Clerk instance: the
    # Authorization: Bearer header is read as a bare Clerk user id. Startup
    # refuses to boot with it on in production.
    AUTH_DEV_BYPASS: bool = False

    # --- Platform administrators ------------------------------------------
    # "email:argon2hash" pairs separated by ";" -- not "," because an argon2
    # hash contains commas itself. Generate an entry with
    #   python scripts/hash_password.py you@example.com
    # Named rather than shared so platform_audit_logs.actor_user_id stays
    # meaningful. Empty means nobody can sign in to the super admin portal.
    ADMIN_USERS: str = ""
    # Signs platform-admin, restaurant staff and guest-customer session
    # cookies. Rotating it signs out every operator AND every customer, and
    # drops every guest back to an anonymous browser.
    SESSION_SECRET: str = ""
    ADMIN_SESSION_TTL_MINUTES: int = 480
    # Restaurant staff sessions. Longer than an admin session because it has
    # to outlast a shift on a kitchen tablet, shorter than a day so a device
    # left on the counter overnight is not still signed in.
    STAFF_SESSION_TTL_MINUTES: int = 720
    # Guest customers -- ordering without an account. Long, because this
    # cookie is the only thing that can find a guest's order again: losing it
    # loses the pickup PIN and the tracking page with it. Nothing is
    # authenticated here, so it grants no more than the orders it created.
    GUEST_SESSION_TTL_MINUTES: int = 43200  # 30 days
    # Signed-in customers (core/customer_auth.py). As long as a guest session
    # on purpose: someone who made an account should not be signed out more
    # often than someone who did not bother, and this cookie can be recovered
    # by signing in again, which a guest's cannot.
    CUSTOMER_SESSION_TTL_MINUTES: int = 43200  # 30 days

    # Sign in with Google. Optional: email and password and guest ordering all
    # work without it, and the button is simply not offered when it is unset.
    #
    # From the Google Cloud console, under APIs & Services > Credentials, as an
    # "OAuth client ID" of type "Web application". The authorised redirect URI
    # must be exactly the platform root's callback:
    #
    #   https://<ROOT_DOMAIN>/api/v1/customer/google/callback
    #
    # One URI, on the root domain, not one per restaurant. Google matches
    # redirect URIs exactly and a platform with wildcard subdomains cannot
    # register them all; core/google_oauth.py carries the storefront a
    # customer came from through the `state` instead.
    GOOGLE_OAUTH_CLIENT_ID: str = ""
    GOOGLE_OAUTH_CLIENT_SECRET: str = ""
    # How long an abandoned guest row is kept -- one that was created by
    # "continue as guest" and never reached an order. Comfortably longer than
    # the session above, so a guest who comes back to a live cookie still
    # finds their identity. Guests that did order are never swept. 0 disables.
    GUEST_RETENTION_DAYS: int = 45

    # --- Stripe -----------------------------------------------------------
    STRIPE_SECRET_KEY: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""
    STRIPE_CONNECT_WEBHOOK_SECRET: str = ""
    # Staging runs with ENV=production and test-mode Stripe and Clerk keys.
    # Production must not: an API on test keys starts, takes orders, and
    # collects no money. So a production start refuses test keys unless this
    # says the environment is meant to run on them. Staging only.
    ALLOW_TEST_KEYS: bool = False
    # Zenoeats' cut of each order, taken as a Stripe application fee on the
    # restaurant's direct charge: a percentage in basis points (250 = 2.5%)
    # plus a fixed amount in minor units (30 = $0.30). Both 0 means no fee.
    # Applied when the PaymentIntent is created, so a change affects orders
    # paid after it, never one already paid.
    PLATFORM_FEE_BPS: int = 0
    PLATFORM_FEE_FIXED_MINOR: int = 0

    # --- Crypto -----------------------------------------------------------
    # Fernet key. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    FIELD_ENCRYPTION_KEY: str

    # --- Order policy -----------------------------------------------------
    PENDING_PAYMENT_TTL_MINUTES: int = 30
    IDEMPOTENCY_TTL_HOURS: int = 24
    # How long a processed or ignored Stripe/Clerk webhook delivery is kept
    # before the retention sweep removes it. Unprocessed and failed rows are
    # never removed. 0 keeps everything forever.
    WEBHOOK_EVENT_RETENTION_DAYS: int = 365
    MAX_ITEMS_PER_ORDER: int = 50

    # --- Images -----------------------------------------------------------
    # Where uploaded menu images are written, and the directory /images/ is
    # served from. A folder on disk rather than a bucket: one node, one
    # place, nothing to sign. What goes in the database is a relative key,
    # never a URL, so moving to object storage later rewrites this writer and
    # leaves every row alone.
    #
    # The default is <repo>/images, which is what the API uses when it runs
    # natively on the host. Compose overrides it with the path that same
    # folder is bind-mounted at inside the container, so a containerised API
    # and a native one read and write exactly the same bytes.
    IMAGES_DIR: Path = REPO_ROOT / "images"
    # What an image key is appended to when the API hands a URL to a browser.
    # Relative by default: the storefront and portal reach the API's /images
    # through the same origin they load from. Moving to object storage later
    # means pointing this at the bucket's public domain, and nothing stored
    # in the database changes, because rows hold keys and never URLs.
    IMAGES_PUBLIC_BASE: str = "/images"

    # --- Email (SendGrid) -------------------------------------------------
    # Every email Zenoeats itself sends. Empty key means nothing is sent;
    # each message is logged (without its contents) instead.
    SENDGRID_API_KEY: str = ""
    # Must be a sender verified in SendGrid, e.g. "Zenoeats <orders@zenoeats.com>".
    EMAIL_FROM: str = "Zenoeats <no-reply@zenoeats.local>"
    EMAIL_REPLY_TO: str = ""
    # How links in emails reach a restaurant's storefront or portal. {slug}
    # and {root_domain} are filled in. Development behind nginx:
    # https://{slug}.{root_domain}:8443
    STOREFRONT_URL_TEMPLATE: str = "https://{slug}.{root_domain}"
    # The platform root -- the location picker, and "all locations" from a
    # storefront header. A sibling of the template above rather than
    # "https://" + ROOT_DOMAIN, because the scheme and port are deployment
    # facts, not constants: development serves the edge on :8443, so a URL
    # built without the port points at :443 and goes nowhere.
    PLATFORM_URL_TEMPLATE: str = "https://{root_domain}"

    # --- Geocoding (delivery) ---------------------------------------------
    # Turning a customer's address into a distance from the restaurant, to
    # find which delivery ring it falls in. Empty key means delivery cannot be
    # switched on: a fee guessed without a distance is a fee charged wrongly.
    #
    # Google's terms allow caching a result for about 30 days rather than
    # keeping it, so coordinates live in Redis with that TTL and only the
    # derived distance and fee are kept on an order.
    # "google" or "nominatim".
    #
    # nominatim is OpenStreetMap's own geocoder: no key, no account, no card.
    # Its usage policy is the cost instead -- roughly one request a second,
    # and a User-Agent that identifies this deployment and how to reach whoever
    # runs it. See services/geocoding.py, which enforces both.
    #
    # It is the right choice for a demo, a single location, or anyone who will
    # not attach a card to a Google project. It is a worse geocoder: weaker on
    # messy input, and no autocomplete at all, so checkout offers a plain
    # address field rather than suggestions.
    GEOCODING_PROVIDER: str = "google"
    # Who to contact about this deployment's geocoding traffic. Nominatim's
    # policy asks for it and blocks traffic that identifies nobody, so with
    # GEOCODING_PROVIDER=nominatim this is required rather than decorative.
    # Falls back to EMAIL_FROM, which is already an address someone reads.
    NOMINATIM_CONTACT: str = ""
    GOOGLE_MAPS_API_KEY: str = ""
    # Seconds. Google permits 30 days; shorter is always safe.
    GEOCODE_CACHE_TTL_SECONDS: int = 30 * 24 * 60 * 60
    # A lookup sits on the checkout path, so it fails fast rather than
    # holding a customer at a spinner.
    GEOCODE_TIMEOUT_SECONDS: float = 4.0

    # --- Live delivery tracking ---------------------------------------------
    # The map on a customer's order page. Unlike GOOGLE_MAPS_API_KEY, which
    # stays on the server, this key ships to every browser: restrict it in the
    # Google Cloud console to the Maps JavaScript API and to your storefront
    # domains. Empty means the page shows the timeline without a map.
    GOOGLE_MAPS_BROWSER_KEY: str = ""
    # No Map ID: the tracking map colours itself from a style the restaurant
    # chose, and Google ignores such a style whenever a Map ID is in use.
    # See services/maps.py.
    # A driver's position older than this is not shown: a phone that stopped
    # reporting must not look like a driver parked on the road.
    DRIVER_LOCATION_STALE_SECONDS: int = 120
    # Driving time to the customer, from Google's Routes API with the server
    # key. Asked at most this often per order, however many tabs are polling,
    # because each ask is billed.
    DELIVERY_ETA_REFRESH_SECONDS: int = 30
    ROUTES_TIMEOUT_SECONDS: float = 4.0

    # --- Error tracking ---------------------------------------------------
    # Sentry. Empty DSN means off. See core/observability for what is and is
    # not sent.
    SENTRY_DSN: str = ""
    # Defaults to ENV. Set it to tell staging and production apart when both
    # run with ENV=production.
    SENTRY_ENVIRONMENT: str = ""
    # Share of requests traced for performance. 0 sends errors only.
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0
    # Build identifier (a git tag or commit), attached to every event.
    RELEASE: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()