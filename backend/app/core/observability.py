"""Error tracking (Sentry), for the API and the Celery worker.

Off unless SENTRY_DSN is set, so development and tests send nothing.

What leaves the server is deliberately little. This app handles passwords,
Customer and staff session tokens, Stripe client secrets, pickup PINs and
customers' emails, and none of that belongs in a third-party error tracker:

  * request bodies are never attached (they carry sign-in passwords, order
    notes and card-flow secrets);
  * stack-frame local variables are never attached (a variable named `body`
    or `payload` holds all of the above, under a name no denylist matches);
  * default PII (IP addresses, cookies, user identity) is not sent;
  * anything that still reaches an event -- extra data, breadcrumbs, headers
    -- is scrubbed by key against Sentry's denylist plus this app's own
    sensitive field names.
"""

import logging

from app.config import settings

log = logging.getLogger(__name__)

# Field names specific to this codebase, on top of Sentry's defaults
# (password, secret, token, authorization, cookie, session, ...).
APP_DENYLIST = [
    "pin", "pickup_pin", "pickup_pin_encrypted",
    "client_secret", "stripe_client_secret",
    "email", "receipt_email", "contact_email", "owner_email",
    "full_name", "customer_note", "item_note",
    "password_hash", "temporary_password", "current_password", "new_password",
    "idempotency-key", "idempotency_key",
    "stripe-signature", "svix-signature",
]

_enabled = False


def init_error_tracking(component: str, *, transport=None) -> bool:
    """Start Sentry for this process. Returns whether it is on.

    `transport` exists for tests, which capture events instead of sending
    them."""
    global _enabled
    if not settings.SENTRY_DSN:
        return False

    import sentry_sdk
    from sentry_sdk.scrubber import DEFAULT_DENYLIST, DEFAULT_PII_DENYLIST, EventScrubber

    options = dict(
        dsn=settings.SENTRY_DSN,
        environment=settings.SENTRY_ENVIRONMENT or settings.ENV,
        release=settings.RELEASE or None,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        send_default_pii=False,
        max_request_body_size="never",
        include_local_variables=False,
        event_scrubber=EventScrubber(
            denylist=DEFAULT_DENYLIST + APP_DENYLIST,
            pii_denylist=DEFAULT_PII_DENYLIST,
            recursive=True,
        ),
    )
    if transport is not None:
        options["transport"] = transport

    sentry_sdk.init(**options)
    sentry_sdk.set_tag("component", component)
    _enabled = True
    log.info("error tracking enabled for %s", component)
    return True


def capture(exc: BaseException, *, reference: str | None = None) -> None:
    """Report an exception that a handler caught and answered.

    The API's catch-all handler turns every unexpected fault into a JSON 500
    with a short reference. Reporting it here, tagged with that reference, is
    what lets a support message quoting "Reference 3f9c1a2b" be found.
    """
    if not _enabled:
        return
    import sentry_sdk

    with sentry_sdk.new_scope() as scope:
        if reference:
            scope.set_tag("reference", reference)
        sentry_sdk.capture_exception(exc)
