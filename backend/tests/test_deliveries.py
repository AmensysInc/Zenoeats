"""A restaurant's own drivers: assignment, the two steps, and what a driver
cannot reach.

Customers still cannot order a delivery. This is the phone order a restaurant
agrees to run out itself: a manager hands a paid order to one of its drivers
with the address, the driver picks it up and delivers it, and the order's
history says who did each part.

The other half of the feature is everything a driver may not do. A driver is
not "floor staff with an extra screen": the board, stock, the menu, reports
and the team are all refused.
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

PIN = "771144"


@pytest.fixture
def shop(admin_user, cleanup):
    from app.db.base import utcnow
    from app.db.session import system_session, tenant_session
    from app.models import RestaurantUser

    restaurant = _create(admin_user, cleanup)
    owner_email = _email()
    owner, _ = _owner(admin_user, restaurant.id, owner_email)
    _set_own_password(owner_email, "owner password 123")

    def client(user_id):
        c = _staff_client(restaurant.slug)
        c.cookies.set(staff_auth.SESSION_COOKIE, staff_auth.issue_session(user_id))
        return c

    people, memberships = {"MANAGER": owner.user_id}, {}
    for key, role, name in (
        ("KITCHEN", "KITCHEN", "Kit Cook"),
        ("DRIVER", "DRIVER", "Dana Driver"),
        ("OTHER_DRIVER", "DRIVER", "Sam Second"),
    ):
        with system_session() as session:
            user_id = session.execute(
                text(
                    "INSERT INTO users (id, kind, email, full_name, password_hash, "
                    "is_platform_admin, is_active, must_change_password, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), 'STAFF', :e, :n, :h, false, true, false, "
                    "now(), now()) RETURNING id"
                ),
                {"e": _email(), "n": name, "h": staff_auth.hash_password("staff password 12")},
            ).scalar_one()
        with tenant_session(restaurant.id) as session:
            row = RestaurantUser(
                restaurant_id=restaurant.id, user_id=user_id, role_code=role, status="ACTIVE",
                invited_at=utcnow(), accepted_at=utcnow(),
            )
            session.add(row)
            session.flush()
            memberships[key] = str(row.id)
        people[key] = user_id

    numbers = iter(range(6001, 7000))
    with system_session() as session:
        customer = session.execute(
            text(
                "INSERT INTO users (id, kind, email, is_platform_admin, is_active, "
                "must_change_password, created_at, updated_at) VALUES (gen_random_uuid(), "
                "'CUSTOMER', :e, false, true, false, now(), now()) RETURNING id"
            ),
            {"e": f"cust-{uuid.uuid4().hex[:8]}@zenoeats.invalid"},
        ).scalar_one()

    class Shop:
        id = restaurant.id
        slug = restaurant.slug
        manager = client(owner.user_id)
        kitchen = client(people["KITCHEN"])
        driver = client(people["DRIVER"])
        other_driver = client(people["OTHER_DRIVER"])
        driver_membership = memberships["DRIVER"]
        other_membership = memberships["OTHER_DRIVER"]
        kitchen_membership = memberships["KITCHEN"]
        driver_id = people["DRIVER"]

        @staticmethod
        def order(status="PREPARING", paid=True):
            from app.core.crypto import encrypt_field
            from app.models import Order, OrderItem, Payment

            now = utcnow()
            with tenant_session(restaurant.id) as session:
                order = Order(
                    restaurant_id=restaurant.id, order_number=next(numbers),
                    customer_user_id=customer, status=status, payment_method="STRIPE",
                    currency="USD", subtotal_minor=1400, discount_minor=0, tax_minor=100,
                    total_minor=1500, pickup_pin_encrypted=encrypt_field(PIN),
                    paid_at=now if paid else None,
                )
                session.add(order)
                session.flush()
                session.add(OrderItem(
                    restaurant_id=restaurant.id, order_id=order.id, name_snapshot="Smash Burger",
                    unit_price_minor=1400, quantity=1, line_total_minor=1400,
                ))
                session.add(Payment(
                    restaurant_id=restaurant.id, order_id=order.id, method="STRIPE",
                    status="PAID" if paid else "PENDING", amount_minor=1500, currency="USD",
                    succeeded_at=now if paid else None,
                ))
                return str(order.id)

        @staticmethod
        def row(order_id):
            with tenant_session(restaurant.id) as session:
                return session.execute(
                    text("SELECT status, fulfillment_type, driver_user_id, delivery_address, "
                         "completed_at FROM orders WHERE id = :i"),
                    {"i": order_id},
                ).one()

        @staticmethod
        def events(order_id):
            with tenant_session(restaurant.id) as session:
                return [
                    tuple(r) for r in session.execute(
                        text("SELECT action, actor_user_id, reason FROM order_events "
                             "WHERE order_id = :i ORDER BY created_at"),
                        {"i": order_id},
                    ).all()
                ]

    return Shop


def _assign(client, order_id, membership_id, address="12 Oak Street, flat 3"):
    return client.post(
        f"/api/v1/restaurant/orders/{order_id}/assign-driver",
        json={"membership_id": membership_id, "delivery_address": address},
    )


# ---------------------------------------------------------- the journey ---

def test_a_manager_sends_an_order_out_and_the_driver_runs_it(shop):
    order_id = shop.order()

    assigned = _assign(shop.manager, order_id, shop.driver_membership)
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["driver"] == "Dana Driver"
    row = shop.row(order_id)
    assert (row.fulfillment_type, row.delivery_address) == ("DELIVERY", "12 Oak Street, flat 3")
    assert str(row.driver_user_id) == str(shop.driver_id)

    # The kitchen's one button now means "ready for the driver".
    ready = shop.kitchen.post(f"/api/v1/restaurant/orders/{order_id}/ready")
    assert ready.status_code == 200 and ready.json()["status"] == "READY_FOR_DELIVERY"

    # There is no counter, so the PIN route is closed.
    handover = shop.kitchen.post(
        f"/api/v1/restaurant/orders/{order_id}/complete", json={"pin": PIN}
    )
    assert handover.status_code == 409

    mine = shop.driver.get("/api/v1/restaurant/deliveries").json()
    assert [(o["order_id"], o["mine"], o["delivery_address"]) for o in mine] == [
        (order_id, True, "12 Oak Street, flat 3")
    ]

    assert shop.driver.post(f"/api/v1/restaurant/orders/{order_id}/picked-up").status_code == 200
    assert shop.row(order_id).status == "OUT_FOR_DELIVERY"
    done = shop.driver.post(f"/api/v1/restaurant/orders/{order_id}/delivered")
    assert done.status_code == 200 and done.json()["status"] == "COMPLETED"
    assert shop.row(order_id).completed_at is not None

    assert shop.events(order_id) == [
        ("ASSIGNED_DRIVER", shop.manager.cookies and _actor(shop, "MANAGER"), "to Dana Driver"),
        ("MARKED_READY", _actor(shop, "KITCHEN"), None),
        ("PICKED_UP", shop.driver_id, None),
        ("DELIVERED", shop.driver_id, None),
    ]

    # And it reads back in today's history, as a delivery.
    history = shop.manager.get("/api/v1/restaurant/orders/history").json()
    finished = next(o for o in history["orders"] if o["order_id"] == order_id)
    assert finished["fulfillment_type"] == "DELIVERY"
    assert finished["last_action"]["action"] == "DELIVERED"
    assert finished["last_action"]["by"] == "Dana Driver"


def _actor(shop, key):
    """The user id behind one of the fixture's clients, for the event trail."""
    from app.db.session import system_session

    token = [c for c in getattr(shop, key.lower()).cookies.jar if c.name == staff_auth.SESSION_COOKIE]
    principal = staff_auth.verify_session(token[0].value)
    with system_session() as session:
        return session.execute(
            text("SELECT id FROM users WHERE id = :i"), {"i": str(principal.user_id)}
        ).scalar_one()


def test_a_delivery_shows_on_the_board_with_its_driver_and_address(shop):
    order_id = shop.order()
    _assign(shop.manager, order_id, shop.driver_membership)

    ticket = next(
        o for o in shop.kitchen.get("/api/v1/restaurant/orders").json()
        if o["order_id"] == order_id
    )
    assert ticket["fulfillment_type"] == "DELIVERY"
    assert ticket["driver"] == "Dana Driver"
    assert ticket["delivery_address"] == "12 Oak Street, flat 3"


def test_a_manager_can_hand_the_order_to_a_different_driver(shop):
    order_id = shop.order()
    _assign(shop.manager, order_id, shop.driver_membership)
    again = _assign(shop.manager, order_id, shop.other_membership, address="9 Elm Road")
    assert again.status_code == 200 and again.json()["driver"] == "Sam Second"

    assert shop.driver.get("/api/v1/restaurant/deliveries").json() == []
    assert [o["order_id"] for o in shop.other_driver.get("/api/v1/restaurant/deliveries").json()] == [
        order_id
    ]
    assert [e[0] for e in shop.events(order_id)] == ["ASSIGNED_DRIVER", "ASSIGNED_DRIVER"]


def test_a_manager_can_act_for_a_driver_on_the_road(shop):
    order_id = shop.order()
    _assign(shop.manager, order_id, shop.driver_membership)
    shop.kitchen.post(f"/api/v1/restaurant/orders/{order_id}/ready")

    assert shop.manager.post(f"/api/v1/restaurant/orders/{order_id}/picked-up").status_code == 200
    assert shop.manager.post(f"/api/v1/restaurant/orders/{order_id}/delivered").status_code == 200
    assert shop.row(order_id).status == "COMPLETED"


# ------------------------------------------------------------ refusals ---

def test_a_driver_only_ever_sees_their_own_orders(shop):
    theirs = shop.order()
    someone_elses = shop.order()
    _assign(shop.manager, theirs, shop.driver_membership)
    _assign(shop.manager, someone_elses, shop.other_membership)
    shop.kitchen.post(f"/api/v1/restaurant/orders/{someone_elses}/ready")

    assert [o["order_id"] for o in shop.driver.get("/api/v1/restaurant/deliveries").json()] == [theirs]

    # Not even by id: a driver cannot learn which other orders exist.
    assert shop.driver.post(
        f"/api/v1/restaurant/orders/{someone_elses}/picked-up"
    ).status_code == 404
    assert shop.driver.post(
        f"/api/v1/restaurant/orders/{someone_elses}/delivered"
    ).status_code == 404
    assert shop.row(someone_elses).status == "READY_FOR_DELIVERY"


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/api/v1/restaurant/orders"),
        ("get", "/api/v1/restaurant/orders/history"),
        ("get", "/api/v1/restaurant/stock"),
        ("get", "/api/v1/restaurant/items"),
        ("get", "/api/v1/restaurant/menu"),
        ("get", "/api/v1/restaurant/reports"),
        ("get", "/api/v1/restaurant/staff"),
        ("get", "/api/v1/restaurant/drivers"),
    ],
)
def test_a_driver_reaches_nothing_else_in_the_portal(shop, method, path):
    assert getattr(shop.driver, method)(path).status_code == 403


def test_a_driver_cannot_assign_or_cancel(shop):
    order_id = shop.order()
    assert _assign(shop.driver, order_id, shop.driver_membership).status_code == 403
    assert shop.driver.post(
        f"/api/v1/restaurant/orders/{order_id}/cancel", json={"reason": "no"}
    ).status_code == 403
    assert shop.row(order_id).fulfillment_type == "PICKUP"


def test_the_kitchen_and_the_counter_have_no_delivery_screen(shop):
    assert shop.kitchen.get("/api/v1/restaurant/deliveries").status_code == 403


def test_only_a_driver_can_be_assigned_a_delivery(shop):
    order_id = shop.order()
    not_a_driver = _assign(shop.manager, order_id, shop.kitchen_membership)
    assert not_a_driver.status_code == 422
    assert "driver" in not_a_driver.json()["detail"]["message"]

    unknown = _assign(shop.manager, order_id, str(uuid.uuid4()))
    assert unknown.status_code == 422
    assert shop.row(order_id).fulfillment_type == "PICKUP"


def test_an_unpaid_or_finished_order_cannot_be_sent_out(shop):
    unpaid = shop.order(status="PENDING_PAYMENT", paid=False)
    res = _assign(shop.manager, unpaid, shop.driver_membership)
    assert res.status_code == 409 and res.json()["detail"]["code"] == "PAYMENT_NOT_CONFIRMED"

    done = shop.order(status="COMPLETED")
    assert _assign(shop.manager, done, shop.driver_membership).status_code == 409


def test_an_address_is_required(shop):
    order_id = shop.order()
    assert _assign(shop.manager, order_id, shop.driver_membership, address="").status_code == 422


def test_a_collection_is_not_a_delivery(shop):
    """The two steps belong to deliveries; a pickup order still uses the PIN."""
    order_id = shop.order()
    shop.kitchen.post(f"/api/v1/restaurant/orders/{order_id}/ready")

    res = shop.manager.post(f"/api/v1/restaurant/orders/{order_id}/picked-up")
    assert res.status_code == 409
    assert "collection" in res.json()["detail"]["message"]


# ------------------------------------------------ taking it back off ---

def _unassign(client, order_id):
    return client.post(f"/api/v1/restaurant/orders/{order_id}/unassign-driver")


def test_a_delivery_can_become_a_collection_again(shop):
    """The customer rings back to say they will come in after all."""
    order_id = shop.order()
    _assign(shop.manager, order_id, shop.driver_membership)
    shop.kitchen.post(f"/api/v1/restaurant/orders/{order_id}/ready")

    res = _unassign(shop.manager, order_id)
    assert res.status_code == 200, res.text
    row = shop.row(order_id)
    assert (row.status, row.fulfillment_type) == ("READY_FOR_PICKUP", "PICKUP")
    assert (row.driver_user_id, row.delivery_address) == (None, None)
    assert shop.driver.get("/api/v1/restaurant/deliveries").json() == []
    assert [e[0] for e in shop.events(order_id)][-1] == "UNASSIGNED_DRIVER"

    # And the counter can hand it over with the PIN, as any collection.
    done = shop.kitchen.post(
        f"/api/v1/restaurant/orders/{order_id}/complete", json={"pin": PIN}
    )
    assert done.status_code == 200 and done.json()["status"] == "COMPLETED"


def test_an_order_still_being_made_can_be_taken_back_too(shop):
    order_id = shop.order()
    _assign(shop.manager, order_id, shop.driver_membership)

    assert _unassign(shop.manager, order_id).status_code == 200
    row = shop.row(order_id)
    assert (row.status, row.fulfillment_type) == ("PREPARING", "PICKUP")


def test_an_order_the_driver_already_has_cannot_be_taken_back(shop):
    order_id = shop.order()
    _assign(shop.manager, order_id, shop.driver_membership)
    shop.kitchen.post(f"/api/v1/restaurant/orders/{order_id}/ready")
    shop.driver.post(f"/api/v1/restaurant/orders/{order_id}/picked-up")

    res = _unassign(shop.manager, order_id)
    assert res.status_code == 409
    assert "already has this order" in res.json()["detail"]["message"]
    assert shop.row(order_id).status == "OUT_FOR_DELIVERY"


def test_a_collection_cannot_be_unassigned_and_a_driver_cannot_do_it(shop):
    order_id = shop.order()
    plain = _unassign(shop.manager, order_id)
    assert plain.status_code == 409 and "already a collection" in plain.json()["detail"]["message"]

    _assign(shop.manager, order_id, shop.driver_membership)
    assert _unassign(shop.driver, order_id).status_code == 403
    assert shop.row(order_id).fulfillment_type == "DELIVERY"
