"""Stuck orders can leave the board, and who moved them is kept.

There was no way to cancel an order and no override for the PIN, so a
customer who lost their PIN, never came, or was refunded from the Stripe
Dashboard left a ticket on the board for good -- and an order locked by five
wrong PINs could never be completed by anyone. Both are now open to the whole
floor, kitchen and cashier included, always with a reason.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core import staff_auth

from tests.test_admin_restaurants import (  # noqa: F401  (fixtures)
    _create,
    _email,
    _owner,
    _set_own_password,
    _staff_client,
    admin_user,
    cleanup,
)

pytestmark = pytest.mark.integration

PIN = "305518"


@pytest.fixture
def shop(admin_user, cleanup):
    from app.db.base import utcnow
    from app.db.session import system_session, tenant_session
    from app.models import RestaurantUser

    restaurant = _create(admin_user, cleanup)
    owner_email = _email()
    owner, _ = _owner(admin_user, restaurant.id, owner_email)
    _set_own_password(owner_email, "owner password 123")

    with system_session() as session:
        customer_id = session.execute(
            text(
                "INSERT INTO users (id, kind, email, clerk_user_id, is_platform_admin, "
                "is_active, must_change_password, created_at, updated_at) "
                "VALUES (gen_random_uuid(), 'CUSTOMER', :e, :c, false, true, false, now(), now()) "
                "RETURNING id"
            ),
            {"e": f"board-{uuid.uuid4().hex[:8]}@zenoeats.invalid", "c": f"user_b_{uuid.uuid4().hex}"},
        ).scalar_one()
        cook_id = session.execute(
            text(
                "INSERT INTO users (id, kind, email, password_hash, is_platform_admin, "
                "is_active, must_change_password, created_at, updated_at) "
                "VALUES (gen_random_uuid(), 'STAFF', :e, :h, false, true, false, now(), now()) "
                "RETURNING id"
            ),
            {"e": _email(), "h": staff_auth.hash_password("kitchen password 1")},
        ).scalar_one()
    with tenant_session(restaurant.id) as session:
        session.add(RestaurantUser(
            restaurant_id=restaurant.id, user_id=cook_id, role_code="KITCHEN",
            status="ACTIVE", invited_at=utcnow(), accepted_at=utcnow(),
        ))

    numbers = iter(range(4001, 5000))

    class Shop:
        id = restaurant.id
        owner_id = owner.user_id

        @staticmethod
        def client(user_id):
            c = _staff_client(restaurant.slug)
            c.cookies.set(staff_auth.SESSION_COOKIE, staff_auth.issue_session(user_id))
            return c

        manager = None
        cook = None

        @staticmethod
        def order(status="READY_FOR_PICKUP", payment="PAID", paid=True, attempts=0,
                  paid_at=None):
            from app.core.crypto import encrypt_field
            from app.models import Order, Payment

            now = paid_at or utcnow()
            with tenant_session(restaurant.id) as session:
                order = Order(
                    restaurant_id=restaurant.id, order_number=next(numbers),
                    customer_user_id=customer_id, status=status,
                    payment_method="STRIPE", currency="USD", subtotal_minor=1500,
                    discount_minor=0, tax_minor=0, total_minor=1500,
                    pickup_pin_encrypted=encrypt_field(PIN), pickup_pin_failed_attempts=attempts,
                    paid_at=now if paid else None,
                )
                session.add(order)
                session.flush()
                session.add(Payment(
                    restaurant_id=restaurant.id, order_id=order.id, method="STRIPE",
                    status=payment, amount_minor=1500, currency="USD",
                    succeeded_at=now if paid else None,
                ))
                return str(order.id)

        @staticmethod
        def row(order_id):
            with tenant_session(restaurant.id) as session:
                return session.execute(
                    text("SELECT status, cancelled_reason, completed_at FROM orders WHERE id = :i"),
                    {"i": order_id},
                ).one()

        @staticmethod
        def events(order_id):
            with tenant_session(restaurant.id) as session:
                return [
                    tuple(r) for r in session.execute(
                        text(
                            "SELECT action, actor_user_id, reason FROM order_events "
                            "WHERE order_id = :i ORDER BY created_at"
                        ),
                        {"i": order_id},
                    ).all()
                ]

    Shop.manager = Shop.client(owner.user_id)
    Shop.cook = Shop.client(cook_id)
    Shop.cook_id = cook_id
    return Shop


def _post(client, order_id, action, **body):
    return client.post(f"/api/v1/restaurant/orders/{order_id}/{action}", json=body)


# ------------------------------------------------------------ override ---

def test_a_manager_hands_over_a_locked_order_and_it_is_recorded(shop):
    order_id = shop.order(attempts=5)
    assert _post(shop.cook, order_id, "complete", pin=PIN).status_code == 423

    board = shop.manager.get("/api/v1/restaurant/orders").json()
    assert next(o for o in board if o["order_id"] == order_id)["pin_locked"] is True

    res = _post(shop.manager, order_id, "override-complete", reason="  Phone died, checked name  ")
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "COMPLETED"
    assert shop.row(order_id).completed_at is not None
    assert shop.events(order_id) == [
        ("COMPLETED_BY_OVERRIDE", shop.owner_id, "Phone died, checked name")
    ]
    assert all(o["order_id"] != order_id for o in shop.manager.get("/api/v1/restaurant/orders").json())


def test_an_override_needs_a_reason(shop):
    order_id = shop.order()

    assert _post(shop.manager, order_id, "override-complete", reason="  ").status_code == 422
    assert _post(shop.manager, order_id, "override-complete").status_code == 422
    assert _post(shop.cook, order_id, "override-complete", reason="  ").status_code == 422
    assert shop.row(order_id).status == "READY_FOR_PICKUP"
    assert shop.events(order_id) == []


def test_kitchen_staff_hand_over_a_locked_order_and_it_is_recorded_against_them(shop):
    order_id = shop.order(attempts=5)

    res = _post(shop.cook, order_id, "override-complete", reason="Phone died, checked name")
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "COMPLETED"
    assert shop.events(order_id) == [
        ("COMPLETED_BY_OVERRIDE", shop.cook_id, "Phone died, checked name")
    ]


def test_an_override_skips_the_pin_and_nothing_else(shop):
    cooking = shop.order(status="PREPARING")
    res = _post(shop.manager, cooking, "override-complete", reason="checked name")
    assert res.status_code == 409  # not ready yet

    unpaid = shop.order(payment="PENDING", paid=False)
    res = _post(shop.manager, unpaid, "override-complete", reason="checked name")
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "PAYMENT_NOT_CONFIRMED"


# -------------------------------------------------------------- cancel ---

def test_a_manager_cancels_a_paid_order_and_is_told_to_refund(shop):
    order_id = shop.order(status="PREPARING")

    # Without the refund: the money is a separate decision, and this test is
    # about the order leaving the board. test_order_refund.py has the money.
    res = _post(shop.manager, order_id, "cancel", reason="Customer called to cancel", refund=False)
    assert res.status_code == 200, res.text
    assert res.json() == {
        "order_id": order_id, "status": "CANCELLED", "payment_status": "PAID",
        "refund_needed": True, "refund_problem": None,
    }
    row = shop.row(order_id)
    assert row.status == "CANCELLED" and row.cancelled_reason == "CANCELLED_BY_RESTAURANT"
    assert shop.events(order_id) == [("CANCELLED", shop.owner_id, "Customer called to cancel")]
    assert all(o["order_id"] != order_id for o in shop.manager.get("/api/v1/restaurant/orders").json())

    # Cancelled is final.
    assert _post(shop.manager, order_id, "cancel", reason="again").status_code == 409


def test_a_refunded_order_is_flagged_on_the_board_and_needs_no_refund(shop):
    order_id = shop.order(status="PREPARING", payment="REFUNDED")

    board = shop.cook.get("/api/v1/restaurant/orders").json()
    ticket = next(o for o in board if o["order_id"] == order_id)
    assert ticket["payment_status"] == "REFUNDED"
    assert ticket["pin_locked"] is False

    res = _post(shop.manager, order_id, "cancel", reason="Refunded in Stripe")
    assert res.status_code == 200
    assert res.json()["refund_needed"] is False
    # Nothing was asked of Stripe: there is nothing left to give back.
    assert res.json()["refund_problem"] is None


def test_kitchen_staff_cancel_a_paid_order_and_it_is_recorded_against_them(shop):
    order_id = shop.order()

    assert _post(shop.cook, order_id, "cancel", reason="  ", refund=False).status_code == 422
    res = _post(shop.cook, order_id, "cancel", reason="Never came", refund=False)
    assert res.status_code == 200, res.text
    assert res.json()["refund_needed"] is True
    assert shop.row(order_id).status == "CANCELLED"
    assert shop.events(order_id) == [("CANCELLED", shop.cook_id, "Never came")]

    # Refunding it afterwards is still a manager's.
    assert _post(shop.cook, order_id, "refund", reason="agreed").status_code == 403


def test_cancel_is_refused_for_unpaid_orders(shop):
    unpaid = shop.order(status="PENDING_PAYMENT", payment="PENDING", paid=False)
    for who in (shop.manager, shop.cook):
        res = _post(who, unpaid, "cancel", reason="never came", refund=False)
        assert res.status_code == 409
        assert "has not been paid" in res.json()["detail"]["message"]

    assert shop.row(unpaid).status == "PENDING_PAYMENT"


def test_another_restaurants_order_cannot_be_cancelled(shop, admin_user, cleanup):
    other = _create(admin_user, cleanup)
    other_owner = _email()
    out, _ = _owner(admin_user, other.id, other_owner)
    _set_own_password(other_owner, "other owner pass 1")
    order_id = shop.order()

    intruder = _staff_client(other.slug)
    intruder.cookies.set(staff_auth.SESSION_COOKIE, staff_auth.issue_session(out.user_id))
    assert _post(intruder, order_id, "cancel", reason="not mine").status_code == 404
    assert shop.row(order_id).status == "READY_FOR_PICKUP"


# ------------------------------------------------------------- history ---

def test_the_ordinary_path_is_recorded_too(shop):
    order_id = shop.order(status="PREPARING")

    assert _post(shop.cook, order_id, "ready").status_code == 200
    assert _post(shop.cook, order_id, "complete", pin=PIN).status_code == 200
    assert shop.events(order_id) == [
        ("MARKED_READY", shop.cook_id, None),
        ("COMPLETED_WITH_PIN", shop.cook_id, None),
    ]


def test_the_application_cannot_rewrite_an_event(shop):
    from sqlalchemy.exc import ProgrammingError

    from app.db.session import tenant_session

    order_id = shop.order()
    _post(shop.manager, order_id, "override-complete", reason="checked name")

    with pytest.raises(ProgrammingError, match="permission denied"):
        with tenant_session(shop.id) as session:
            session.execute(
                text("UPDATE order_events SET reason = 'nothing to see' WHERE order_id = :i"),
                {"i": order_id},
            )


# ------------------------------------------------------------- the day ---
#
# The board timed tickets from checkout start, and an order handed over or
# cancelled vanished from every screen the counter has.


def test_a_ticket_says_when_it_was_paid(shop):
    order_id = shop.order(status="PREPARING")
    ticket = next(o for o in shop.cook.get("/api/v1/restaurant/orders").json()
                  if o["order_id"] == order_id)
    assert ticket["paid_at"] is not None


def test_todays_finished_orders_say_what_happened_and_who_did_it(shop):
    from datetime import timedelta

    from app.db.base import utcnow

    with_pin = shop.order()
    assert _post(shop.cook, with_pin, "complete", pin=PIN).status_code == 200
    overridden = shop.order()
    assert _post(shop.manager, overridden, "override-complete", reason="Phone died").status_code == 200
    cancelled = shop.order(status="PREPARING", payment="REFUNDED")
    assert _post(shop.manager, cancelled, "cancel", reason="Never came").status_code == 200
    still_cooking = shop.order(status="PREPARING")
    yesterdays = shop.order(status="COMPLETED", paid_at=utcnow() - timedelta(hours=30))

    res = shop.cook.get("/api/v1/restaurant/orders/history")  # the floor can read it
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["timezone"] == "America/Chicago"
    by_id = {o["order_id"]: o for o in body["orders"]}

    assert set(by_id) == {with_pin, overridden, cancelled}
    assert still_cooking not in by_id and yesterdays not in by_id

    assert by_id[with_pin]["last_action"]["action"] == "COMPLETED_WITH_PIN"
    assert by_id[with_pin]["last_action"]["by"]  # the cook, by name or email
    assert by_id[overridden]["last_action"] | {"by": None} == {
        "action": "COMPLETED_BY_OVERRIDE", "by": None, "reason": "Phone died",
    }
    assert by_id[cancelled]["status"] == "CANCELLED"
    assert by_id[cancelled]["last_action"]["reason"] == "Never came"
    assert by_id[cancelled]["payment_status"] == "REFUNDED"
    assert by_id[with_pin]["items"] == []  # the fixture's orders carry no lines

    # Newest first.
    assert body["orders"][0]["order_id"] == cancelled
