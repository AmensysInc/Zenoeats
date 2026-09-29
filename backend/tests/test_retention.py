"""The retention sweep removes exactly what has stopped mattering.

Against the real database, because the point is which rows survive: an
expired idempotency key goes, a live one stays; a year-old processed webhook
goes, a year-old failed one stays for someone to look at.
"""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.config import settings
from app.db.base import utcnow
from app.services import retention

pytestmark = pytest.mark.integration


def _exists(table: str, row_id) -> bool:
    with retention._platform_transaction() as session:
        return session.execute(
            text(f"SELECT 1 FROM {table} WHERE id = :id"), {"id": row_id}
        ).first() is not None


def _idempotency_key(expires_at) -> uuid.UUID:
    row_id = uuid.uuid4()
    with retention._platform_transaction() as session:
        session.execute(
            text(
                "INSERT INTO idempotency_keys (id, key, actor_id, endpoint, request_hash, "
                "created_at, expires_at) VALUES (:id, :key, :actor, 'test', 'h', now(), :exp)"
            ),
            {"id": row_id, "key": uuid.uuid4().hex, "actor": uuid.uuid4(), "exp": expires_at},
        )
    return row_id


def _stripe_event(status: str, received_at) -> uuid.UUID:
    row_id = uuid.uuid4()
    with retention._platform_transaction() as session:
        session.execute(
            text(
                "INSERT INTO stripe_events (id, stripe_event_id, type, payload, status, "
                "received_at, attempts) VALUES (:id, :eid, 'test', '{}'::jsonb, :status, :at, 0)"
            ),
            {"id": row_id, "eid": f"evt_test_{uuid.uuid4().hex}", "status": status, "at": received_at},
        )
    return row_id


def test_expired_idempotency_keys_go_and_live_ones_stay():
    expired = _idempotency_key(utcnow() - timedelta(minutes=1))
    live = _idempotency_key(utcnow() + timedelta(hours=1))

    retention.sweep()

    assert not _exists("idempotency_keys", expired)
    assert _exists("idempotency_keys", live)
    retention.sweep()  # idempotent


def test_only_old_settled_webhook_deliveries_are_removed(monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_EVENT_RETENTION_DAYS", 30)
    old = utcnow() - timedelta(days=31)
    recent = utcnow() - timedelta(days=1)

    old_processed = _stripe_event("PROCESSED", old)
    old_ignored = _stripe_event("IGNORED", old)
    old_failed = _stripe_event("FAILED", old)
    old_received = _stripe_event("RECEIVED", old)
    recent_processed = _stripe_event("PROCESSED", recent)

    try:
        retention.sweep()

        assert not _exists("stripe_events", old_processed)
        assert not _exists("stripe_events", old_ignored)
        # Work not done, and evidence of a problem, are never swept away.
        assert _exists("stripe_events", old_failed)
        assert _exists("stripe_events", old_received)
        assert _exists("stripe_events", recent_processed)
    finally:
        with retention._platform_transaction() as session:
            session.execute(
                text("DELETE FROM stripe_events WHERE id IN (:a, :b, :c)"),
                {"a": old_failed, "b": old_received, "c": recent_processed},
            )


def test_zero_retention_keeps_every_webhook_delivery(monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_EVENT_RETENTION_DAYS", 0)
    ancient = _stripe_event("PROCESSED", utcnow() - timedelta(days=4000))
    try:
        retention.sweep()
        assert _exists("stripe_events", ancient)
    finally:
        with retention._platform_transaction() as session:
            session.execute(text("DELETE FROM stripe_events WHERE id = :id"), {"id": ancient})


# ------------------------------------------------------- abandoned guests ---

def _guest(created_at) -> uuid.UUID:
    """A guest row, as "continue as guest" creates one."""
    from app.db.session import system_session
    from app.models import User, UserKind

    with system_session() as session:
        user = User(
            kind=UserKind.GUEST.value,
            email=f"guest-{uuid.uuid4().hex[:8]}@zenoeats.invalid",
        )
        session.add(user)
        session.flush()
        uid = user.id
        session.execute(
            text("UPDATE users SET created_at = :t WHERE id = :id"),
            {"t": created_at, "id": uid},
        )
    return uid


def test_a_guest_who_never_ordered_is_swept():
    old = _guest(utcnow() - timedelta(days=settings.GUEST_RETENTION_DAYS + 1))
    assert _exists("users", old)
    retention._sweep_abandoned_guests(settings.GUEST_RETENTION_DAYS)
    assert not _exists("users", old)


def test_a_recent_guest_is_left_alone():
    """Their cookie is still live and their cart may still be open."""
    fresh = _guest(utcnow())
    retention._sweep_abandoned_guests(settings.GUEST_RETENTION_DAYS)
    assert _exists("users", fresh)


def test_a_guest_who_ordered_is_never_swept(active_restaurant_with_order):
    """The property that matters.

    orders is under RLS and the sweep holds no tenant, so asking "did this
    guest order?" as the app role answers no for everyone -- which would have
    proposed deleting the customer of every paid guest sale. The system role
    is what can see across tenants; this test fails if that ever regresses,
    rather than waiting for a foreign key to refuse in production.
    """
    guest_id, order_id, rid = active_restaurant_with_order

    # Swept with the most aggressive cutoff there is: age is not what
    # protects this row, owning an order is.
    retention._sweep_abandoned_guests(0)

    assert _exists("users", guest_id), "a guest who paid for an order was deleted"
    from app.db.session import tenant_session

    with tenant_session(rid) as session:
        assert session.execute(
            text("SELECT 1 FROM orders WHERE id = :id"), {"id": order_id}
        ).first() is not None, "their order went with them"


@pytest.fixture
def active_restaurant_with_order():
    """A guest who placed a real order, and the restaurant it belongs to."""
    from app.db.session import system_session, tenant_session
    from app.models import (
        Order, OrderStatus, PaymentStatus, Restaurant, RestaurantStatus, User, UserKind,
    )

    slug = f"retention-{uuid.uuid4().hex[:8]}"
    with system_session() as session:
        restaurant = Restaurant(slug=slug, name="Retention Test", timezone="UTC",
                                currency="USD", status=RestaurantStatus.ACTIVE.value)
        guest = User(kind=UserKind.GUEST.value,
                     email=f"payer-{uuid.uuid4().hex[:8]}@zenoeats.invalid")
        session.add_all([restaurant, guest])
        session.flush()
        rid, guest_id = restaurant.id, guest.id
        session.execute(
            text("UPDATE users SET created_at = :t WHERE id = :id"),
            {"t": utcnow() - timedelta(days=3650), "id": guest_id},
        )

    with tenant_session(rid) as session:
        order = Order(
            restaurant_id=rid, order_number=1, customer_user_id=guest_id,
            status=OrderStatus.COMPLETED.value, payment_method="CARD", currency="USD",
            subtotal_minor=500, discount_minor=0, tax_minor=0, total_minor=500,
        )
        session.add(order)
        session.flush()
        order_id = order.id

    yield guest_id, order_id, rid

    with tenant_session(rid) as session:
        session.execute(text("DELETE FROM orders WHERE restaurant_id = :r"), {"r": rid})
        session.execute(text("DELETE FROM restaurants WHERE id = :r"), {"r": rid})
    # The app role, not the system one: only zenoeats_app may delete a user,
    # which is why the sweep itself splits the work across both.
    with retention._platform_transaction() as session:
        session.execute(text("DELETE FROM users WHERE id = :id"), {"id": guest_id})


# --------------------------------------------------- personal details ---
#
# A settled delivery's payload keeps what an audit needs -- ids, amounts,
# statuses -- and loses the person: a closed account must not live on in a
# year-old copy of Clerk's user object or Stripe's billing details.

def _event_with(table: str, status: str, payload: dict) -> uuid.UUID:
    import json

    row_id = uuid.uuid4()
    id_column = "stripe_event_id" if table == "stripe_events" else "clerk_event_id"
    with retention._platform_transaction() as session:
        session.execute(
            text(
                f"INSERT INTO {table} (id, {id_column}, type, payload, status, received_at, "
                "attempts) VALUES (:id, :eid, :type, CAST(:p AS jsonb), :status, now(), 1)"
            ),
            {"id": row_id, "eid": f"evt_{uuid.uuid4().hex}", "type": payload["type"],
             "p": json.dumps(payload), "status": status},
        )
    return row_id


def _payload(table: str, row_id):
    with retention._platform_transaction() as session:
        return session.execute(
            text(f"SELECT payload FROM {table} WHERE id = :id"), {"id": row_id}
        ).scalar_one()


STRIPE_PAYMENT = {
    "type": "charge.refunded",
    "data": {"object": {
        "id": "ch_1", "amount": 2192, "amount_refunded": 2192, "payment_intent": "pi_1",
        "receipt_email": "sam@example.com",
        "billing_details": {"email": "sam@example.com", "name": "Sam Customer",
                            "phone": "+12145550100", "address": {"postal_code": "75201"}},
        "metadata": {"order_id": "o-1"},
    }},
}

CLERK_USER = {
    "type": "user.updated",
    "data": {"id": "user_abc", "object": "user", "first_name": "Sam",
             "email_addresses": [{"email_address": "sam@example.com"}],
             "phone_numbers": [{"phone_number": "+12145550100"}]},
}


def test_a_settled_payment_event_keeps_its_ids_and_amounts_and_loses_the_person():
    settled = _event_with("stripe_events", "PROCESSED", STRIPE_PAYMENT)
    retention.sweep()

    charge = _payload("stripe_events", settled)["data"]["object"]
    assert "sam@example.com" not in str(charge) and "Sam Customer" not in str(charge)
    assert "billing_details" not in charge and "receipt_email" not in charge
    assert (charge["id"], charge["amount"], charge["payment_intent"]) == ("ch_1", 2192, "pi_1")
    assert charge["metadata"] == {"order_id": "o-1"}


def test_a_settled_clerk_event_keeps_only_whose_it_was():
    settled = _event_with("clerk_events", "PROCESSED", CLERK_USER)
    retention.sweep()
    assert _payload("clerk_events", settled) == {
        "type": "user.updated", "data": {"id": "user_abc", "object": "user"}, "redacted": True,
    }


@pytest.mark.parametrize("status", ["RECEIVED", "FAILED"])
def test_an_event_still_to_be_processed_keeps_everything(status):
    """Its handler still has to read it."""
    stripe_row = _event_with("stripe_events", status, STRIPE_PAYMENT)
    clerk_row = _event_with("clerk_events", status, CLERK_USER)
    retention.sweep()
    assert _payload("stripe_events", stripe_row) == STRIPE_PAYMENT
    assert _payload("clerk_events", clerk_row) == CLERK_USER
