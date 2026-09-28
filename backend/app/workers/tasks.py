"""Background processing.

The pattern that matters here is Appendix E.8: system-role discovery, then
tenant-scoped execution.

  1. Load the event under zenoeats_system (narrow declared read surface).
  2. Resolve order_id -> restaurant_id and the configured connected account.
  3. Prove the event's stripe_account_id matches that restaurant.
  4. Open a SEPARATE zenoeats_app transaction with SET LOCAL app.current_tenant
     and do the business mutation under FORCE RLS.

Step 3 is what stops a compromised or misrouted Connect event from mutating
another restaurant's orders.
"""

import logging
from uuid import UUID

from sqlalchemy import select, text

from app.db.base import utcnow
from app.db.session import system_session, tenant_session
from app.models import (
    ClerkEvent, Order, OrderStatus, Payment, PaymentStatus,
    RestaurantPaymentAccount, StripeEvent, StripeEventStatus, User, UserKind,
)
from app.services import ops_health, stripe_service, stripe_tax
from app.services.orders import transition
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=5, default_retry_delay=10, name="app.workers.tasks.process_stripe_event")
def process_stripe_event(self, event_row_id: str):
    with system_session() as session:
        event = session.get(StripeEvent, UUID(event_row_id))
        if event is None:
            log.error("stripe event row %s not found", event_row_id)
            return
        if event.status == StripeEventStatus.PROCESSED.value:
            return  # already done, redelivery is safe
        payload = event.payload
        event_type = event.type
        account_id = event.stripe_account_id
        event.attempts += 1

    try:
        handler = {
            "payment_intent.succeeded": _handle_intent_succeeded,
            "payment_intent.payment_failed": _handle_intent_failed,
            "payment_intent.canceled": _handle_intent_canceled,
            "charge.refunded": _handle_charge_refunded,
            "account.updated": _handle_account_updated,
        }.get(event_type)

        if handler is None:
            _mark(event_row_id, StripeEventStatus.IGNORED.value)
            return

        handler(payload, account_id)
        _mark(event_row_id, StripeEventStatus.PROCESSED.value)

    except Exception as exc:
        log.exception("stripe event %s (%s) failed", event_row_id, event_type)
        _mark(event_row_id, StripeEventStatus.FAILED.value, error=str(exc)[:2000])
        raise self.retry(exc=exc)


def _mark(event_row_id: str, status: str, error: str | None = None):
    with system_session() as session:
        event = session.get(StripeEvent, UUID(event_row_id))
        if event is None:
            return
        event.status = status
        event.error = error
        if status == StripeEventStatus.PROCESSED.value:
            event.processed_at = utcnow()


def _resolve_tenant_for_intent(intent_id: str, event_account_id: str | None):
    """Narrow cross-tenant discovery, then the account guard.

    Returns (restaurant_id, payment_id) or None if this event is not ours.
    Raises PermissionError when the event's account does not match the
    restaurant's configured connected account, which is a real security
    signal and should page someone.
    """
    with system_session() as session:
        row = session.execute(
            text(
                """
                SELECT p.id AS payment_id,
                       p.restaurant_id,
                       rpa.stripe_account_id AS configured_account
                FROM payments p
                JOIN restaurant_payment_accounts rpa
                  ON rpa.restaurant_id = p.restaurant_id
                WHERE p.stripe_payment_intent_id = :intent_id
                """
            ),
            {"intent_id": intent_id},
        ).one_or_none()

    if row is None:
        log.warning("no payment for intent %s", intent_id)
        return None

    if event_account_id and row.configured_account != event_account_id:
        raise PermissionError(
            f"Connect event account {event_account_id} does not match the account "
            f"configured for restaurant {row.restaurant_id}"
        )

    return row.restaurant_id, row.payment_id


def _handle_intent_succeeded(payload: dict, event_account_id: str | None):
    intent = payload["data"]["object"]
    resolved = _resolve_tenant_for_intent(intent["id"], event_account_id)
    if resolved is None:
        return
    restaurant_id, payment_id = resolved

    with tenant_session(restaurant_id) as session:
        # Locked, because a webhook and a reconciliation can arrive for the
        # same payment at the same moment. The second waits here and then
        # finds succeeded_at already set, rather than both reading it empty
        # and both trying to move the order.
        payment = session.get(Payment, payment_id, with_for_update=True)
        if payment is None:
            return
        order_id = payment.order_id
        if payment.succeeded_at is None:
            payment.status = PaymentStatus.PAID.value
            payment.succeeded_at = utcnow()
            payment.failure_message = None

            order = session.get(Order, order_id)
            if order and order.status == OrderStatus.PENDING_PAYMENT.value:
                # Only Stripe's word can do this -- a verified webhook, or the
                # intent read back from Stripe. The client never can.
                transition(order, OrderStatus.AUTO_ACCEPTED.value)
                transition(order, OrderStatus.PREPARING.value)
                log.info("order %s paid and now PREPARING", order.order_number)

    # After the paid state has committed, and outside any transaction (rule
    # 6). Reached on a redelivery too, not only the first delivery: if Stripe
    # Tax failed last time, the order is already paid and this retry is what
    # records its tax. A no-op for flat-rate restaurants and already-recorded
    # orders.
    stripe_tax.record_transaction(restaurant_id, order_id)

    # The customer's confirmation, in its own task so a slow or failing email
    # provider never holds up or fails the payment webhook. Safe to enqueue on
    # every delivery: the task sends once per order.
    send_order_confirmation.delay(str(restaurant_id), str(order_id))


def _handle_intent_failed(payload: dict, event_account_id: str | None):
    intent = payload["data"]["object"]
    resolved = _resolve_tenant_for_intent(intent["id"], event_account_id)
    if resolved is None:
        return
    restaurant_id, payment_id = resolved

    with tenant_session(restaurant_id) as session:
        payment = session.get(Payment, payment_id, with_for_update=True)
        if payment is None or payment.succeeded_at is not None:
            return
        payment.status = PaymentStatus.FAILED.value
        error =(intent.get("last_payment_error") or {}).get("message")
        payment.failure_message = error
        # The order stays PENDING_PAYMENT so the customer can retry until the
        # TTL sweep expires it.


def _handle_intent_canceled(payload: dict, event_account_id: str | None):
    intent = payload["data"]["object"]
    resolved = _resolve_tenant_for_intent(intent["id"], event_account_id)
    if resolved is None:
        return
    restaurant_id, payment_id = resolved

    with tenant_session(restaurant_id) as session:
        payment = session.get(Payment, payment_id, with_for_update=True)
        if payment is None or payment.succeeded_at is not None:
            return
        payment.status = PaymentStatus.FAILED.value
        order = session.get(Order, payment.order_id)
        if order and order.status == OrderStatus.PENDING_PAYMENT.value:
            transition(order, OrderStatus.CANCELLED.value, reason="PAYMENT_CANCELLED")


def reconcile_payment_intent(intent_id: str, stripe_account_id: str) -> str | None:
    """Ask Stripe what became of an intent, and apply it as the webhook would.

    The webhook is the normal path and stays so. This is for when it does not
    arrive -- a listener that is not running, a delivery Stripe gave up on, a
    worker that is down or starved -- so that a customer Stripe has already
    charged is not left watching "Confirming your payment" until the order
    expires under them.

    Nothing here trusts the client. The intent is read from Stripe with the
    platform key, on the account recorded when it was created, and handed to
    the same handlers a verified event reaches, so the account guard, the row
    lock and every exactly-once check apply unchanged. Running it any number
    of times, before or after the webhook, is safe.

    Returns the intent's status as Stripe reports it, or None when Stripe has
    no such intent. Raises when Stripe cannot be reached, which callers must
    not mistake for "not paid". Calls Stripe, so never call it inside a
    transaction (rule 6).
    """
    intent = stripe_service.retrieve_payment_intent(intent_id, stripe_account_id)
    if intent is None:
        return None

    status = intent.get("status")
    payload = {"data": {"object": intent}}
    if status == "succeeded":
        _handle_intent_succeeded(payload, stripe_account_id)
    elif status == "canceled":
        _handle_intent_canceled(payload, stripe_account_id)
    elif status == "requires_payment_method" and intent.get("last_payment_error"):
        # A declined card puts the intent back to awaiting a payment method,
        # with the decline attached. Without the error it is simply a
        # customer who has not paid yet, which changes nothing.
        _handle_intent_failed(payload, stripe_account_id)
    return status


def _handle_charge_refunded(payload: dict, event_account_id: str | None):
    """Reconcile refunds, including ones issued from the restaurant's own
    Stripe Dashboard. Rule 26: a refund changes payment state, never
    OrderStatus."""
    charge = payload["data"]["object"]
    intent_id = charge.get("payment_intent")
    if not intent_id:
        return
    resolved = _resolve_tenant_for_intent(intent_id, event_account_id)
    if resolved is None:
        return
    restaurant_id, payment_id = resolved

    refunded = charge.get("amount_refunded", 0)
    total = charge.get("amount", 0)
    with tenant_session(restaurant_id) as session:
        payment = session.get(Payment, payment_id)
        if payment is None:
            return
        order_id = payment.order_id
        # What the customer had been told was coming back before this event:
        # the amount recorded, or the whole charge while a refund from the
        # board is still pending. Only more than that is news to them.
        told = (
            payment.amount_minor if payment.status == PaymentStatus.REFUND_PENDING.value
            else payment.refunded_minor
        )
        payment.status = (
            PaymentStatus.REFUNDED.value if refunded >= total
            else PaymentStatus.PARTIALLY_REFUNDED.value
        )
        # Stripe's figure is cumulative, so a redelivered or out-of-order
        # event writes the same total rather than adding to it.
        payment.refunded_minor = min(max(refunded, 0), payment.amount_minor)

    # The restaurant's tax reports must show the refund too. Cumulative and
    # idempotent, so redelivery is safe; a no-op for flat-rate restaurants.
    stripe_tax.record_refund(restaurant_id, order_id, refunded)

    # A refund the customer has not heard about -- one issued from Stripe's
    # dashboard, typically. The board's own refunds were told at the time,
    # and their keys stop a second email even if this arrives first.
    if refunded > told:
        from app.services import order_emails

        order_emails.queue(
            "refund_issued", restaurant_id, order_id,
            refunded_before=told, refunded_after=min(refunded, total) if total else refunded,
        )


def _handle_account_updated(payload: dict, event_account_id: str | None):
    """Pause ordering when a restaurant loses charges_enabled rather than
    letting PaymentIntents fail one customer at a time."""
    account = payload["data"]["object"]
    account_id = account.get("id") or event_account_id
    if not account_id:
        return

    with system_session() as session:
        row = session.execute(
            text(
                "SELECT restaurant_id FROM restaurant_payment_accounts "
                "WHERE stripe_account_id = :acct"
            ),
            {"acct": account_id},
        ).one_or_none()
    if row is None:
        return

    with tenant_session(row.restaurant_id) as session:
        mapping = session.execute(select(RestaurantPaymentAccount)).scalar_one_or_none()
        if mapping is None:
            return
        mapping.charges_enabled = bool(account.get("charges_enabled"))
        mapping.payouts_enabled = bool(account.get("payouts_enabled"))
        mapping.details_submitted = bool(account.get("details_submitted"))
        mapping.onboarding_status = "COMPLETE" if account.get("details_submitted") else "PENDING"
        if not mapping.charges_enabled:
            log.error("restaurant %s lost charges_enabled", row.restaurant_id)


@celery_app.task(bind=True, max_retries=3, name="app.workers.tasks.process_clerk_event")
def process_clerk_event(self, event_row_id: str):
    """Mirror Clerk users into the local users table.

    The request path creates a customer's row on first sight; this keeps it
    current afterwards -- a changed email or name, a deleted account. Both go
    through clerk_customers, so they agree on what a customer row holds.
    """
    from app.services import clerk_customers

    with system_session() as session:
        event = session.get(ClerkEvent, UUID(event_row_id))
        if event is None or event.status == "PROCESSED":
            return
        event.attempts += 1
        etype = event.type
        data = (event.payload or {}).get("data", {})

    try:
        with system_session() as session:
            clerk_id = str(data.get("id") or "")
            if etype in ("user.created", "user.updated") and clerk_id:
                existing = session.execute(
                    select(User).where(User.clerk_user_id == clerk_id)
                ).scalar_one_or_none()
                # Never touch a row that is not a customer's, whatever the
                # payload says.
                if existing is None or existing.kind == UserKind.CUSTOMER.value:
                    clerk_customers.upsert_customer(
                        session, clerk_id, clerk_customers.profile_from_payload(data)
                    )
            elif etype == "user.deleted" and clerk_id:
                clerk_customers.deactivate(session, clerk_id)

        with system_session() as session:
            row = session.get(ClerkEvent, UUID(event_row_id))
            if row:
                row.status = "PROCESSED"
                row.processed_at = utcnow()

    except Exception as exc:
        log.exception("clerk event %s failed", event_row_id)
        with system_session() as session:
            row = session.get(ClerkEvent, UUID(event_row_id))
            if row:
                row.status = "FAILED"
                row.error = str(exc)[:2000]
        raise self.retry(exc=exc)


@celery_app.task(
    bind=True, max_retries=6, default_retry_delay=60,
    name="app.workers.tasks.send_order_confirmation",
)
def send_order_confirmation(self, restaurant_id: str, order_id: str):
    """Email the customer that their paid order is confirmed. Retries while
    the email provider is rate limiting or down; sends at most once."""
    from app.services import email, notifications

    try:
        return notifications.send_order_confirmation(UUID(restaurant_id), UUID(order_id))
    except email.RetryableEmailError as exc:
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(
    bind=True, max_retries=6, default_retry_delay=60,
    name="app.workers.tasks.send_order_email",
)
def send_order_email(self, kind: str, restaurant_id: str, order_id: str, extra: dict | None = None):
    """Email the customer that their order moved on: ready, on its way,
    delivered, cancelled or refunded (services/order_emails.py). Retries
    while the email provider is rate limiting or down; sends at most once."""
    from app.services import email, order_emails

    try:
        return order_emails.send(kind, UUID(restaurant_id), UUID(order_id), extra)
    except email.RetryableEmailError as exc:
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(
    bind=True, max_retries=6, default_retry_delay=60,
    name="app.workers.tasks.send_staff_invitation",
)
def send_staff_invitation(
    self, restaurant_id: str, membership_id: str, sealed_password: str | None = None
):
    """Email someone that a restaurant has invited them to its team.

    `sealed_password` is the temporary password the invitation issued,
    encrypted with the field key for its time in the queue. Absent for an
    existing login, for an owner invitation, and for any task queued before
    invitations carried one -- which is why it defaults.
    """
    from app.core import crypto
    from app.services import email, notifications

    temporary_password = crypto.decrypt_field(sealed_password) if sealed_password else None
    try:
        return notifications.send_staff_invitation(
            UUID(restaurant_id), UUID(membership_id), temporary_password
        )
    except email.RetryableEmailError as exc:
        if self.request.retries >= self.max_retries:
            # Out of retries: say so on the team list rather than leaving the
            # invitation looking as if it were still on its way.
            notifications.record_invitation_outcome(
                UUID(restaurant_id), UUID(membership_id),
                email.Outcome("FAILED", "Not delivered: the email provider could not be reached."),
            )
            return False
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(name="app.workers.tasks.heartbeat", ignore_result=True)
def heartbeat():
    """Scheduled every minute by beat; see services/ops_health."""
    ops_health.beat()


@celery_app.task(name="app.workers.tasks.sweep_retention")
def sweep_retention():
    """Remove expired idempotency keys and old settled webhook deliveries.
    See services/retention for exactly what is and is not removed."""
    from app.services import retention

    return retention.sweep()


@celery_app.task(name="app.workers.tasks.expire_pending_orders")
def expire_pending_orders():
    """TTL sweep for abandoned checkouts.

    Discovery under the system role, mutation under the tenant role. An order
    that has a successful payment is never expired, no matter what the clock
    says.
    """
    with system_session() as session:
        rows = session.execute(
            text(
                """
                SELECT o.id, o.restaurant_id,
                       p.stripe_payment_intent_id, p.stripe_account_id, p.succeeded_at
                FROM orders o
                LEFT JOIN payments p ON p.order_id = o.id
                WHERE o.status = 'PENDING_PAYMENT'
                  AND o.expires_at IS NOT NULL
                  AND o.expires_at <= now()
                LIMIT 500
                """
            )
        ).all()

    expired = sum(
        _expire_order(row.restaurant_id, row.id, row.stripe_payment_intent_id,
                      row.stripe_account_id, row.succeeded_at)
        for row in rows
    )
    if expired:
        log.info("expired %d stale pending orders", expired)
    return expired


def _expire_order(restaurant_id, order_id, intent_id, account_id, succeeded_at) -> bool:
    """Expire one abandoned order, unless Stripe says it was paid.

    Our own succeeded_at is not enough to go on: it is written by the webhook,
    and the webhook is exactly what may have gone missing. Expiring on it
    alone once swept up orders Stripe had charged. So an order with an intent
    is checked with Stripe first, outside any transaction (rule 6), and one
    Stripe cannot answer for is left for the next sweep rather than guessed
    at.
    """
    if intent_id and account_id and succeeded_at is None:
        try:
            reconcile_payment_intent(intent_id, account_id)
        except Exception:
            log.warning("not expiring order %s: could not check its payment with Stripe",
                        order_id, exc_info=True)
            return False

    with tenant_session(restaurant_id) as session:
        order = session.get(Order, order_id)
        if order is None or order.status != OrderStatus.PENDING_PAYMENT.value:
            return False
        payment = session.execute(
            select(Payment).where(Payment.order_id == order.id)
        ).scalar_one_or_none()
        if payment and payment.succeeded_at is not None:
            return False  # paid; the handler that recorded it owns the order
        transition(order, OrderStatus.EXPIRED.value)
        return True
