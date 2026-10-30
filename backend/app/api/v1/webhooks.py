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

from app.db.base import utcnow
from app.db.session import system_session
from app.models import StripeEvent, StripeEventStatus
from app.services import stripe_service
from app.workers.tasks import process_stripe_event

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
