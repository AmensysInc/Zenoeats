"""Webhook inboxes. Verify, persist, enqueue, return 200 fast.

Section 8.2 and rule 17: every Stripe webhook is persisted with a unique
event id before processing, processed asynchronously, and is safe to receive
more than once. Nothing here mutates order or payment state; that happens in
the Celery worker after the account-to-tenant guard passes.
"""

import logging

import stripe
from fastapi import APIRouter, Header, Request, Response
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db.base import utcnow
from app.db.session import system_session
from app.models import ClerkEvent, StripeEvent, StripeEventStatus
from app.services import clerk_emails, stripe_service
from app.workers.tasks import process_clerk_event, process_stripe_event, send_clerk_email

log = logging.getLogger(__name__)
router = APIRouter(tags=["webhooks"])

# Only these are acted on. Everything else is stored and marked IGNORED so we
# have the audit trail without pretending to handle it.
SUPPORTED_STRIPE_EVENTS = {
    "payment_intent.succeeded",
    "payment_intent.payment_failed",
    "payment_intent.canceled",
    "charge.refunded",
    "account.updated",
}


@router.post("/webhooks/stripe/connect")
async def stripe_connect_webhook(
    request: Request,
    stripe_signature: str = Header(alias="Stripe-Signature"),
):
    payload = await request.body()

    try:
        event = stripe_service.construct_connect_event(payload, stripe_signature)
    except (ValueError, stripe.SignatureVerificationError) as exc:
        log.warning("rejected stripe webhook: %s", type(exc).__name__)
        return Response(status_code=400, content='{"code":"INVALID_SIGNATURE"}',
                        media_type="application/json")

    # Everything below reads the plain dict, never the Event object.
    # stripe-python's Event supports __getitem__ but raises on .get(), and
    # mixing the two is what silently broke this endpoint: .get("account")
    # threw AttributeError, the handler 500'd, and because the webhook is the
    # only thing that can mark an order PAID, every payment stayed pending
    # while Stripe had already taken the money. One shape, one access.
    event_dict = event.to_dict() if hasattr(event, "to_dict") else dict(event)
    event_type = event_dict.get("type", "")
    row_id = None

    with system_session() as session:
        row = StripeEvent(
            stripe_event_id=event_dict["id"],
            type=event_type,
            payload=event_dict,
            status=(
                StripeEventStatus.RECEIVED.value
                if event_type in SUPPORTED_STRIPE_EVENTS
                else StripeEventStatus.IGNORED.value
            ),
            received_at=utcnow(),
            # Connect events carry the connected account. Rule 25: this is
            # the value the worker must match against the restaurant's
            # configured account before touching tenant state.
            stripe_account_id=event_dict.get("account"),
        )
        session.add(row)
        try:
            session.flush()
            row_id = str(row.id)
            should_process = row.status == StripeEventStatus.RECEIVED.value
        except IntegrityError:
            # Duplicate delivery. Stripe retries aggressively and this is the
            # normal case, not an error. 200 and stop.
            session.rollback()
            log.info("duplicate stripe event %s", event_dict["id"])
            return {"received": True, "duplicate": True}

    if row_id and should_process:
        process_stripe_event.delay(row_id)

    return {"received": True}


@router.post("/webhooks/clerk")
async def clerk_webhook(request: Request):
    """Mirror Clerk identity changes into PostgreSQL, and send the customer
    emails Clerk has handed to us (email.created; services/clerk_emails.py).

    Same durability contract as the Stripe inbox: persist first, process
    asynchronously, tolerate redelivery.
    """
    from svix.webhooks import Webhook, WebhookVerificationError

    payload = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}

    # Building the verifier and using it fail for different reasons and
    # deserve different answers. svix base64-decodes the secret in its
    # constructor and raises a plain binascii error there, not a
    # WebhookVerificationError, so an unset or malformed secret used to
    # escape the handler as an opaque 500.
    try:
        verifier = Webhook(settings.CLERK_WEBHOOK_SECRET)
    except Exception:
        # Our misconfiguration, not a bad request. 503 so Svix keeps the
        # delivery and retries once the secret is actually set.
        log.error("CLERK_WEBHOOK_SECRET is missing or malformed; cannot verify webhooks")
        return Response(status_code=503, content='{"code":"WEBHOOK_NOT_CONFIGURED"}',
                        media_type="application/json")

    try:
        verified = verifier.verify(payload, headers)
    except WebhookVerificationError:
        log.warning("rejected clerk webhook: bad signature")
        return Response(status_code=400, content='{"code":"INVALID_SIGNATURE"}',
                        media_type="application/json")

    event_id = headers.get("svix-id")
    row_id = None
    event_type = verified.get("type", "unknown")

    if event_type == "email.created":
        return _clerk_email(event_id, verified)

    with system_session() as session:
        row = ClerkEvent(
            clerk_event_id=event_id,
            type=event_type,
            payload=verified,
            received_at=utcnow(),
        )
        session.add(row)
        try:
            session.flush()
            row_id = str(row.id)
        except IntegrityError:
            session.rollback()
            return {"received": True, "duplicate": True}

    if row_id:
        process_clerk_event.delay(row_id)
    return {"received": True}


def _clerk_email(event_id: str | None, event: dict):
    """An email Clerk would have sent, now ours to send.

    Stored like every other event, so a redelivery is recognised, but
    redacted: a sign-in code has no business in a table kept for a year. The
    contents go to the worker sealed instead. Stored as processed, because
    there is nothing for process_clerk_event to do with it.

    A queue that cannot take the task answers 503 so Clerk delivers the
    event again; a 200 would have lost a code the customer is waiting for.
    The row stays, marked FAILED, under a changed id, so the redelivery is
    not mistaken for a duplicate. (This role may not delete from the inbox.)
    """
    data = event.get("data") or {}
    wanted = clerk_emails.wanted(data)

    with system_session() as session:
        row = ClerkEvent(
            clerk_event_id=event_id,
            type="email.created",
            payload=clerk_emails.redacted(event),
            status="PROCESSED" if wanted else "IGNORED",
            received_at=utcnow(),
            processed_at=utcnow(),
        )
        session.add(row)
        try:
            session.flush()
            row_id = row.id
        except IntegrityError:
            session.rollback()
            return {"received": True, "duplicate": True}

    if not wanted:
        # Clerk sent this one itself.
        return {"received": True}

    try:
        send_clerk_email.delay(clerk_emails.seal(data), event_id)
    except Exception:
        log.exception("could not queue a clerk email; asking Clerk to deliver it again")
        with system_session() as session:
            failed = session.get(ClerkEvent, row_id)
            if failed is not None:
                failed.clerk_event_id = f"{event_id}:unqueued:{row_id}"
                failed.status = "FAILED"
                failed.error = "could not be queued; left for Clerk to deliver again"
        return Response(status_code=503, content='{"code":"QUEUE_UNAVAILABLE"}',
                        media_type="application/json")
    return {"received": True}
