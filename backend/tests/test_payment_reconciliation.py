"""Settling a payment from Stripe's own record when its webhook never comes.

The webhook is the normal way an order becomes PAID, and on its own it made a
missed delivery permanent: Stripe had the customer's money, the tracking page
said "Confirming your payment" indefinitely, and the TTL sweep then expired an
order that had been paid for. A stopped `stripe listen` or a starved worker
was enough.

Stripe is replaced at the one call that reads an intent. Everything else is
real: the handlers, the account guard, the row lock, the tracking endpoint and
its background task, and the sweep.
"""

import uuid
from types import SimpleNamespace

import pytest
import stripe
from sqlalchemy import text

from app.core import errors
from app.db.base import utcnow

integration = pytest.mark.integration


@pytest.fixture
def pending(monkeypatch):
    """An order a customer has just tried to pay for: PENDING_PAYMENT, with a
    PROCESSING payment on a real-looking intent, and no webhook yet."""
    from app.api.v1.admin import _PURGE_ORDER
    from app.db.session import system_session, tenant_session
    from app.models import (
        Order, OrderStatus, Payment, PaymentStatus, Restaurant, RestaurantPaymentAccount,
        RestaurantStatus, User, UserKind,
    )

    suffix = uuid.uuid4().hex[:8]
    slug = f"recon-{suffix}"
    with system_session() as session:
        restaurant = Restaurant(slug=slug, name="Reconcile Test",
                                status=RestaurantStatus.ACTIVE.value,
                                timezone="UTC", currency="USD")
        customer = User(kind=UserKind.CUSTOMER.value, clerk_user_id=f"user_recon_{suffix}",
                        email=f"recon-{suffix}@zenoeats.invalid")
        session.add_all([restaurant, customer])
        session.flush()
        rid, uid = restaurant.id, customer.id

    account_id = f"acct_recon_{suffix}"
    intent_id = f"pi_recon_{suffix}"
    with tenant_session(rid) as session:
        session.add(RestaurantPaymentAccount(restaurant_id=rid, stripe_account_id=account_id,
                                             charges_enabled=True))
        order = Order(
            restaurant_id=rid, order_number=9201, customer_user_id=uid,
            fulfillment_type="PICKUP", status=OrderStatus.PENDING_PAYMENT.value,
            payment_method="STRIPE", currency="USD", subtotal_minor=1000, discount_minor=0,
            tax_minor=80, total_minor=1080, expires_at=utcnow(),
        )
        session.add(order)
        session.flush()
        payment = Payment(restaurant_id=rid, order_id=order.id,
                          status=PaymentStatus.PROCESSING.value, amount_minor=1080,
                          currency="USD", stripe_account_id=account_id,
                          stripe_payment_intent_id=intent_id)
        session.add(payment)
        session.flush()
        oid, pid = order.id, payment.id

    # What Stripe will say about the intent, set per test, and every read,
    # cancellation and refund asked of it.
    stripe_side = SimpleNamespace(intent={"id": intent_id, "status": "succeeded"},
                                  error=None, reads=[], cancels=[], refunds=[],
                                  pay_before_cancel=False)

    def retrieve(id, stripe_account=None, **kwargs):
        stripe_side.reads.append((id, stripe_account))
        if stripe_side.error is not None:
            raise stripe_side.error
        return stripe.PaymentIntent.construct_from(dict(stripe_side.intent), "sk_test")

    def cancel(id, cancellation_reason=None, stripe_account=None, **kwargs):
        # Stripe's own rule: an intent that is paid, cancelled or settling
        # cannot be cancelled, and says so with this code.
        stripe_side.cancels.append((id, cancellation_reason, stripe_account))
        if stripe_side.pay_before_cancel:
            # The customer's confirm reached Stripe a moment before ours.
            stripe_side.intent["status"] = "succeeded"
        if stripe_side.intent["status"] in ("succeeded", "canceled", "processing"):
            raise stripe.InvalidRequestError(
                "You cannot cancel this PaymentIntent", "intent",
                code="payment_intent_unexpected_state",
            )
        stripe_side.intent.update(status="canceled", cancellation_reason=cancellation_reason)
        return stripe.PaymentIntent.construct_from(dict(stripe_side.intent), "sk_test")

    def refund(payment_intent=None, stripe_account=None, idempotency_key=None, **kwargs):
        stripe_side.refunds.append((payment_intent, stripe_account, idempotency_key))
        return {"id": "re_test", "status": "succeeded", "amount": 1080}

    monkeypatch.setattr(stripe.PaymentIntent, "retrieve", retrieve)
    monkeypatch.setattr(stripe.PaymentIntent, "cancel", cancel)
    monkeypatch.setattr(stripe.Refund, "create", refund)

    yield SimpleNamespace(restaurant_id=rid, order_id=oid, payment_id=pid, slug=slug,
                          account_id=account_id, intent_id=intent_id, stripe=stripe_side)

    with tenant_session(rid) as session:
        for table in _PURGE_ORDER:
            session.execute(text(f"DELETE FROM {table} WHERE restaurant_id = :r"), {"r": rid})
        session.execute(text("DELETE FROM restaurants WHERE id = :r"), {"r": rid})


def _state(path):
    from app.db.session import tenant_session
    from app.models import Order, Payment

    with tenant_session(path.restaurant_id) as session:
        order = session.get(Order, path.order_id)
        payment = session.get(Payment, path.payment_id)
        return SimpleNamespace(order=order.status, paid_at=order.paid_at,
                               payment=payment.status, failure=payment.failure_message)


def _age_payment(path, seconds=60):
    """Make the payment look like it has waited this long for its webhook."""
    from app.db.session import tenant_session

    with tenant_session(path.restaurant_id) as session:
        session.execute(
            text("UPDATE payments SET updated_at = now() - make_interval(secs => :s) "
                 "WHERE id = :p"),
            {"s": seconds, "p": path.payment_id},
        )


# ------------------------------------------------------------ reconcile ---

@integration
def test_a_payment_stripe_charged_is_settled_without_its_webhook(pending, queued_emails):
    from app.workers.tasks import reconcile_payment_intent

    assert reconcile_payment_intent(pending.intent_id, pending.account_id) == "succeeded"

    state = _state(pending)
    assert state.order == "PREPARING"
    assert state.payment == "PAID"
    # Read on the account the intent lives on, not the platform.
    assert pending.stripe.reads == [(pending.intent_id, pending.account_id)]
    assert [name for name, _ in queued_emails] == ["send_order_confirmation"]


@integration
def test_the_webhook_arriving_afterwards_changes_nothing(pending):
    """Both paths will often run for the same payment. Whichever is second
    must find the work done, in either order."""
    from app.workers.tasks import _handle_intent_succeeded, reconcile_payment_intent

    reconcile_payment_intent(pending.intent_id, pending.account_id)
    first = _state(pending)
    _handle_intent_succeeded({"data": {"object": {"id": pending.intent_id}}}, pending.account_id)
    reconcile_payment_intent(pending.intent_id, pending.account_id)

    again = _state(pending)
    assert again.order == "PREPARING"
    assert again.paid_at == first.paid_at


@integration
def test_an_intent_not_yet_paid_is_left_alone(pending):
    from app.workers.tasks import reconcile_payment_intent

    pending.stripe.intent["status"] = "requires_payment_method"
    reconcile_payment_intent(pending.intent_id, pending.account_id)

    state = _state(pending)
    assert (state.order, state.payment) == ("PENDING_PAYMENT", "PROCESSING")


@integration
def test_a_declined_card_fails_the_payment_but_keeps_the_order_open(pending):
    from app.workers.tasks import reconcile_payment_intent

    pending.stripe.intent.update(status="requires_payment_method",
                                 last_payment_error={"message": "Your card was declined."})
    reconcile_payment_intent(pending.intent_id, pending.account_id)

    state = _state(pending)
    assert state.order == "PENDING_PAYMENT"
    assert state.payment == "FAILED"
    assert state.failure == "Your card was declined."


@integration
def test_a_canceled_intent_cancels_the_order(pending):
    from app.workers.tasks import reconcile_payment_intent

    pending.stripe.intent["status"] = "canceled"
    reconcile_payment_intent(pending.intent_id, pending.account_id)

    assert _state(pending).order == "CANCELLED"


@integration
def test_stripe_being_unreachable_is_not_taken_as_unpaid(pending):
    from app.workers.tasks import reconcile_payment_intent

    pending.stripe.error = stripe.APIConnectionError("connection reset")
    with pytest.raises(errors.ApiError):
        reconcile_payment_intent(pending.intent_id, pending.account_id)

    state = _state(pending)
    assert (state.order, state.payment) == ("PENDING_PAYMENT", "PROCESSING")


@integration
def test_an_intent_stripe_has_no_record_of_is_reported_as_none(pending):
    from app.workers.tasks import reconcile_payment_intent

    pending.stripe.error = stripe.InvalidRequestError("No such payment_intent", "intent",
                                                      code="resource_missing")
    assert reconcile_payment_intent(pending.intent_id, pending.account_id) is None
    assert _state(pending).order == "PENDING_PAYMENT"


@integration
def test_the_account_guard_still_applies(pending):
    """Reconciliation goes through the same guard a Connect event does: an
    intent read from an account that is not the restaurant's changes
    nothing."""
    from app.workers.tasks import reconcile_payment_intent

    with pytest.raises(PermissionError):
        reconcile_payment_intent(pending.intent_id, "acct_someone_else")
    assert _state(pending).order == "PENDING_PAYMENT"


# ------------------------------------------------------- tracking page ---

@pytest.fixture
def track(pending, monkeypatch):
    """Poll the tracking endpoint as the guest email link does. Redis is a
    dict, so the throttle is exercised without a server."""
    from fastapi.testclient import TestClient

    from app.api.v1 import orders
    from app.core import guest_auth
    from app.main import app

    keys = {}

    class FakeRedis:
        def set(self, key, value, nx=False, ex=None):
            if nx and key in keys:
                return None
            keys[key] = value
            return True

    monkeypatch.setattr(orders, "runtime_redis", lambda: FakeRedis())

    client = TestClient(app, base_url=f"http://{pending.slug}.zenoeats.local")
    token = guest_auth.issue_order_token(pending.order_id)

    def poll():
        res = client.get(f"/api/v1/orders/{pending.order_id}", headers={"X-Order-Token": token})
        assert res.status_code == 200, res.text
        return res.json()

    return poll


@integration
def test_an_order_token_works_from_a_header_and_opens_only_its_own_order(pending):
    """The page sends the emailed token in X-Order-Token, not ?t=, so it stays
    out of access logs. The header must grant exactly what the query did."""
    from fastapi.testclient import TestClient

    from app.core import guest_auth
    from app.main import app

    client = TestClient(app, base_url=f"http://{pending.slug}.zenoeats.local")
    url = f"/api/v1/orders/{pending.order_id}"

    own = client.get(url, headers={"X-Order-Token": guest_auth.issue_order_token(pending.order_id)})
    assert own.status_code == 200, own.text

    other = client.get(url, headers={"X-Order-Token": guest_auth.issue_order_token(uuid.uuid4())})
    assert other.status_code == 401

    forged = client.get(url, headers={"X-Order-Token": "not-a-token"})
    assert forged.status_code == 401

    # Only the header. A token in the query string is ignored, so a client
    # that puts one back in a URL -- and so in the access logs -- gets nothing.
    in_query = client.get(url, params={"t": guest_auth.issue_order_token(pending.order_id)})
    assert in_query.status_code == 401


@integration
def test_the_tracking_page_asks_stripe_once_the_webhook_is_overdue(pending, track):
    _age_payment(pending)

    # The Stripe check runs after the response, so the next poll sees it.
    assert track()["status"] == "PENDING_PAYMENT"
    assert track()["status"] == "PREPARING"
    assert len(pending.stripe.reads) == 1


@integration
def test_a_payment_just_made_is_left_to_the_webhook(pending, track):
    track()
    track()
    assert pending.stripe.reads == []


@integration
def test_many_polls_share_one_stripe_call(pending, track):
    """A page polls every two seconds, and a customer may have it open in
    several tabs. Stripe is asked once per throttle window, not per poll."""
    _age_payment(pending)
    pending.stripe.intent["status"] = "requires_payment_method"

    for _ in range(4):
        assert track()["status"] == "PENDING_PAYMENT"
    assert len(pending.stripe.reads) == 1


@integration
def test_stripe_failing_never_fails_the_poll(pending, track):
    _age_payment(pending)
    pending.stripe.error = stripe.APIConnectionError("connection reset")

    assert track()["status"] == "PENDING_PAYMENT"


# ---------------------------------------------------------------- sweep ---

def _expire(path):
    from app.workers.tasks import _expire_order

    return _expire_order(path.restaurant_id, path.order_id, path.intent_id,
                         path.account_id, None)


@integration
def test_the_sweep_never_expires_an_order_stripe_charged(pending):
    assert _expire(pending) is False
    assert _state(pending).order == "PREPARING"


@integration
def test_the_sweep_leaves_an_order_it_cannot_check_for_next_time(pending):
    pending.stripe.error = stripe.APIConnectionError("connection reset")

    assert _expire(pending) is False
    assert _state(pending).order == "PENDING_PAYMENT"


@integration
def test_the_sweep_expires_an_order_that_was_never_paid(pending):
    pending.stripe.intent["status"] = "requires_payment_method"

    assert _expire(pending) is True
    assert _state(pending).order == "EXPIRED"


# ----------------------------------------------- expiry against payment ---
#
# Expiring an order used to leave its PaymentIntent open. A customer whose
# payment page was still up could pay after the order had expired, and was
# charged for an order the kitchen never saw.


@integration
def test_the_sweep_closes_the_payment_before_expiring_the_order(pending):
    pending.stripe.intent["status"] = "requires_payment_method"

    assert _expire(pending) is True
    assert pending.stripe.cancels == [(pending.intent_id, "abandoned", pending.account_id)]
    # The page left open can no longer charge: Stripe has the intent cancelled.
    assert pending.stripe.intent["status"] == "canceled"
    state = _state(pending)
    assert state.order == "EXPIRED"
    assert state.payment == "FAILED"  # closed, not "processing"


@integration
def test_a_customer_who_pays_just_before_the_cancel_gets_their_order(pending, queued_emails):
    """Stripe settles the confirm and the cancel one at a time. The customer's
    confirm won, so the cancel is refused and the order goes to the kitchen."""
    pending.stripe.intent["status"] = "requires_payment_method"
    pending.stripe.pay_before_cancel = True

    assert _expire(pending) is False
    state = _state(pending)
    assert (state.order, state.payment) == ("PREPARING", "PAID")
    assert ("send_order_confirmation", (str(pending.restaurant_id), str(pending.order_id))) in queued_emails


@integration
def test_an_order_whose_payment_is_still_settling_is_not_expired(pending):
    pending.stripe.intent["status"] = "processing"

    assert _expire(pending) is False
    assert _state(pending).order == "PENDING_PAYMENT"


@integration
def test_an_order_is_not_expired_when_its_payment_cannot_be_closed(pending, monkeypatch):
    pending.stripe.intent["status"] = "requires_payment_method"

    def unreachable(*args, **kwargs):
        raise stripe.APIConnectionError("connection reset")

    monkeypatch.setattr(stripe.PaymentIntent, "cancel", unreachable)
    assert _expire(pending) is False
    assert _state(pending).order == "PENDING_PAYMENT"


@integration
def test_the_sweeps_own_cancellation_reported_back_reads_as_expired(pending):
    """If Stripe's canceled event is handled before the sweep writes the
    expiry, the order still ends EXPIRED, not CANCELLED."""
    from app.workers.tasks import _handle_intent_canceled

    _handle_intent_canceled(
        {"data": {"object": {"id": pending.intent_id, "status": "canceled",
                             "cancellation_reason": "abandoned"}}},
        pending.account_id,
    )
    assert _state(pending).order == "EXPIRED"


@integration
def test_the_sweep_waits_for_a_payment_being_recorded_and_then_leaves_it(pending):
    """The race: the payment is being recorded in one transaction while the
    sweep runs in another. The sweep read the order unlocked, saw it unpaid,
    and its EXPIRED overwrote PREPARING once the payment committed. Now it
    waits on the payment's lock and reads the outcome."""
    import threading

    from app.db.session import tenant_session
    from app.models import Order, Payment
    from app.services.orders import transition
    from app.workers.tasks import _expire_order

    result = {}

    def sweep():
        # No intent: this is the locked half of the sweep, on its own.
        result["expired"] = _expire_order(pending.restaurant_id, pending.order_id, None, None, None)

    with tenant_session(pending.restaurant_id) as session:
        payment = session.get(Payment, pending.payment_id, with_for_update=True)
        order = session.get(Order, pending.order_id, with_for_update=True)
        payment.status, payment.succeeded_at = "PAID", utcnow()
        transition(order, "AUTO_ACCEPTED")
        transition(order, "PREPARING")
        session.flush()

        racer = threading.Thread(target=sweep)
        racer.start()
        racer.join(timeout=2)
        assert racer.is_alive(), "the sweep did not wait for the payment being recorded"

    racer.join(timeout=15)
    assert result == {"expired": False}
    assert _state(pending).order == "PREPARING"


def _expire_directly(path):
    """Expire the order the way the sweep did before this fix: the intent
    left open behind it."""
    from app.db.session import tenant_session
    from app.models import Order
    from app.services.orders import transition

    with tenant_session(path.restaurant_id) as session:
        transition(session.get(Order, path.order_id), "EXPIRED")


@integration
def test_a_payment_that_lands_on_an_expired_order_is_refunded(pending, queued_emails):
    from app.workers.tasks import _handle_intent_succeeded

    _expire_directly(pending)
    _handle_intent_succeeded({"data": {"object": {"id": pending.intent_id}}}, pending.account_id)

    state = _state(pending)
    assert state.order == "EXPIRED"  # final: the kitchen never gets it 30 minutes late
    assert state.payment == "REFUNDED"
    assert [r[0] for r in pending.stripe.refunds] == [pending.intent_id]
    names = [name for name, _ in queued_emails]
    assert "send_order_confirmation" not in names
    assert names == ["send_order_email"]
    kind = queued_emails[0][1][0]
    assert kind == "refund_issued"

    # The event redelivered: nothing more is refunded or sent.
    _handle_intent_succeeded({"data": {"object": {"id": pending.intent_id}}}, pending.account_id)
    assert len(pending.stripe.refunds) == 1
    assert len(queued_emails) == 1


@integration
def test_a_refund_stripe_refuses_is_tried_again_on_the_retried_event(pending, monkeypatch, queued_emails):
    from app.workers.tasks import _handle_intent_succeeded

    _expire_directly(pending)

    def refused(**kwargs):
        raise stripe.InvalidRequestError("balance too low", "amount", code="balance_insufficient")

    monkeypatch.setattr(stripe.Refund, "create", refused)
    with pytest.raises(errors.ApiError):
        _handle_intent_succeeded({"data": {"object": {"id": pending.intent_id}}}, pending.account_id)
    assert _state(pending).payment == "PAID"  # the money is still owed back

    monkeypatch.setattr(stripe.Refund, "create",
                        lambda **kw: {"id": "re_2", "status": "succeeded", "amount": 1080})
    _handle_intent_succeeded({"data": {"object": {"id": pending.intent_id}}}, pending.account_id)
    assert _state(pending).payment == "REFUNDED"


@integration
def test_the_customer_is_told_about_the_refund(pending, monkeypatch):
    """The refund email goes even though the order was never marked paid."""
    from app.services import email, order_emails
    from app.workers.tasks import _handle_intent_succeeded

    _expire_directly(pending)
    _handle_intent_succeeded({"data": {"object": {"id": pending.intent_id}}}, pending.account_id)

    sent = []
    monkeypatch.setattr(email, "deliver",
                        lambda message: sent.append(message) or email.Outcome("SENT", None))
    assert order_emails.send("refund_issued", pending.restaurant_id, pending.order_id,
                             {"refunded_before": 0, "refunded_after": 1080})
    assert "$10.80" in sent[0].text
