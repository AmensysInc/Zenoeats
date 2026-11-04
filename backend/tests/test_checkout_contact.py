"""Checkout asks who is ordering, and whether it is coming to them.

Name and phone are required on every order; delivery also requires an address.
A registered email is the account's own; a guest may correct the receipt
destination for this order without changing identity or previous snapshots.

The details land twice: on the order as a snapshot, which is what the
restaurant calls and what the driver reads, and on the customer as what to
offer next time. Only the first is ever read to decide what an order says.

Delivery is priced from the address on the quote and again on the order,
never from a fee the browser names.
"""

import uuid

import pytest
from sqlalchemy import text

from tests.test_admin_restaurants import (  # noqa: F401  (fixtures)
    _create,
    _email,
    _owner,
    _set_own_password,
    _staff_client,
    admin_user,
    cleanup,
)
from tests.test_delivery_pricing import ADDRESS, DELIVERY, LOCATE, ORIGIN, PROFILE, ZONES, _place
from tests.test_order_delivery_fee import _an_item
from tests.test_staff_removal import team  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration

CONTACT = {"full_name": "Pat  Customer", "phone": "(312) 555-0142", "address": "9 Elm St, Chicago IL"}


@pytest.fixture(autouse=True)
def no_rate_limits(monkeypatch):
    from app.core import ratelimit

    monkeypatch.setattr(ratelimit, "_consume", lambda *a, **k: None)


@pytest.fixture
def shop(team, monkeypatch):
    """An open restaurant with one item, placed at ORIGIN and delivering in
    two rings."""
    from app.db.session import system_session, tenant_session

    _place(monkeypatch, *ORIGIN)
    team.owner.patch(PROFILE, json=ADDRESS)
    team.owner.post(LOCATE)
    team.owner.put(ZONES, json={"zones": [
        {"max_miles": 1, "fee_minor": 200},
        {"max_miles": 3, "fee_minor": 400},
    ]})
    team.owner.patch(DELIVERY, json={"delivery_enabled": True})

    with system_session() as session:
        team.slug = session.execute(
            text("UPDATE restaurants SET status = 'ACTIVE' WHERE id = :r RETURNING slug"),
            {"r": team.id},
        ).scalar_one()
    with tenant_session(team.id) as session:
        team.item_id = _an_item(session, team.id)
    # The staff calls above resolved it while still a draft, and host
    # resolution remembers that for a few seconds.
    from app.api import deps

    deps._tenant_cache.clear()
    return team


def _guest(shop):
    client = _staff_client(shop.slug)
    res = client.post("/api/v1/orders/guest-session", json={"email": "pat@example.com"})
    assert res.status_code == 201
    return client


def _cart(shop, **extra):
    return {
        "items": [{"menu_item_id": str(shop.item_id), "quantity": 1, "modifiers": []}],
        **extra,
    }


def _order(client, shop, **extra):
    return client.post(
        "/api/v1/orders",
        json=_cart(shop, **extra),
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )


def _row(shop, order_id, columns):
    from app.db.session import tenant_session

    with tenant_session(shop.id) as session:
        return session.execute(
            text(f"SELECT {columns} FROM orders WHERE id = :o"), {"o": order_id}
        ).mappings().one()


# ------------------------------------------------------------ required ---

@pytest.mark.parametrize("missing", ["full_name", "phone", "address"])
def test_each_detail_is_required(shop, missing):
    contact = {k: v for k, v in CONTACT.items() if k != missing}
    res = _order(_guest(shop), shop, contact=contact)
    assert res.status_code == 422


def test_an_order_with_no_contact_at_all_is_refused(shop):
    assert _order(_guest(shop), shop).status_code == 422


@pytest.mark.parametrize(
    "field, value",
    [("full_name", "   "), ("phone", "call me"), ("phone", "12345")],
)
def test_details_nobody_could_use_are_refused(shop, field, value):
    res = _order(_guest(shop), shop, contact={**CONTACT, field: value})
    assert res.status_code == 422


def test_pickup_does_not_require_an_address(shop):
    res = _order(_guest(shop), shop, contact={**CONTACT, "address": ""})
    assert res.status_code == 201, res.text
    assert res.json()["delivery_address"] is None


@pytest.mark.parametrize("address", ["", "x"])
def test_delivery_requires_a_usable_address(shop, address):
    res = _order(
        _guest(shop), shop,
        contact={**CONTACT, "address": address}, fulfillment_type="DELIVERY",
    )
    assert res.status_code == 422


def test_an_old_account_with_a_placeholder_address_cannot_order(shop):
    """The email is mandatory too. A placeholder is not one."""
    from app.api.deps import get_current_user
    from app.main import app
    from app.models import User, UserKind

    placeholder = User(
        id=uuid.uuid4(), kind=UserKind.CUSTOMER.value,
        email="user_pending@pending.local", is_active=True,
    )
    app.dependency_overrides[get_current_user] = lambda: placeholder
    try:
        res = _order(_staff_client(shop.slug), shop, contact=CONTACT)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "EMAIL_PENDING"


# ------------------------------------------------------------- snapshot ---

def test_a_collection_keeps_the_name_and_phone_but_no_address(shop):
    res = _order(_guest(shop), shop, contact=CONTACT)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["fulfillment_type"] == "PICKUP"
    assert body["delivery_address"] is None

    row = _row(shop, body["order_id"], "contact_name, contact_phone, delivery_address")
    assert row["contact_name"] == "Pat Customer"
    assert row["contact_phone"] == "(312) 555-0142"
    assert row["delivery_address"] is None


def test_the_details_are_offered_back_next_time(shop):
    client = _guest(shop)
    assert _order(client, shop, contact=CONTACT).status_code == 201

    session = client.get("/api/v1/orders/session").json()
    assert session["full_name"] == "Pat Customer"
    assert session["phone"] == "(312) 555-0142"
    assert session["address"] == "9 Elm St, Chicago IL"
    assert session["email"] == "pat@example.com"


def test_the_board_shows_who_to_call(shop):
    res = _order(_guest(shop), shop, contact=CONTACT)
    order_id = res.json()["order_id"]
    from app.db.session import tenant_session

    # The board leaves out orders nobody has paid for yet.
    with tenant_session(shop.id) as session:
        session.execute(
            text("UPDATE orders SET status = 'PREPARING', paid_at = now() WHERE id = :o"),
            {"o": order_id},
        )

    board = shop.owner.get("/api/v1/restaurant/orders").json()
    ticket = next(o for o in board if o["order_id"] == order_id)
    assert ticket["contact_name"] == "Pat Customer"
    assert ticket["contact_phone"] == "(312) 555-0142"


# ------------------------------------------------------------- delivery ---

def test_the_portal_says_whether_delivery_is_offered(shop):
    res = _staff_client(shop.slug).get("/api/v1/portal")
    assert res.status_code == 200, res.text
    assert res.json()["delivery_offered"] is True


def test_the_quote_adds_the_fee_for_the_address(shop, monkeypatch):
    # About 1.4 miles north: the second ring.
    _place(monkeypatch, ORIGIN[0] + 0.02, ORIGIN[1])
    client = _staff_client(shop.slug)

    collect = client.post("/api/v1/orders/quote", json=_cart(shop)).json()
    deliver = client.post(
        "/api/v1/orders/quote",
        json=_cart(shop, fulfillment_type="DELIVERY", delivery_address=CONTACT["address"]),
    ).json()

    assert collect["amounts"]["delivery_fee_minor"] == 0
    assert collect["delivery_miles"] is None
    assert deliver["amounts"]["delivery_fee_minor"] == 400
    assert deliver["amounts"]["total_minor"] == collect["amounts"]["total_minor"] + 400
    assert 1.2 < deliver["delivery_miles"] < 1.6


def test_a_delivery_order_goes_to_the_contact_address_and_charges_the_fee(shop, monkeypatch):
    _place(monkeypatch, ORIGIN[0] + 0.01, ORIGIN[1])
    res = _order(_guest(shop), shop, contact=CONTACT, fulfillment_type="DELIVERY")
    assert res.status_code == 201, res.text
    body = res.json()

    assert body["fulfillment_type"] == "DELIVERY"
    assert body["delivery_address"] == "9 Elm St, Chicago IL"
    assert body["amounts"]["delivery_fee_minor"] == 200

    row = _row(shop, body["order_id"], "delivery_fee_minor, delivery_miles")
    assert row["delivery_fee_minor"] == 200
    assert row["delivery_miles"] is not None


def test_an_address_out_of_range_cannot_be_ordered_for_delivery(shop, monkeypatch):
    # About 7 miles: past the last ring.
    _place(monkeypatch, ORIGIN[0] + 0.1, ORIGIN[1])
    res = _order(_guest(shop), shop, contact=CONTACT, fulfillment_type="DELIVERY")
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "OUT_OF_DELIVERY_RANGE"


def test_a_delivery_the_customer_paid_for_cannot_become_a_collection(shop, monkeypatch):
    _place(monkeypatch, ORIGIN[0] + 0.01, ORIGIN[1])
    order_id = _order(_guest(shop), shop, contact=CONTACT, fulfillment_type="DELIVERY").json()[
        "order_id"
    ]
    res = shop.owner.post(f"/api/v1/restaurant/orders/{order_id}/unassign-driver")
    assert res.status_code == 409
    assert _row(shop, order_id, "fulfillment_type")["fulfillment_type"] == "DELIVERY"


def test_guest_receipt_email_is_an_order_snapshot_not_an_identity(shop):
    client = _guest(shop)
    created = _order(client, shop, contact=CONTACT, guest_email="corrected@example.com")
    assert created.status_code == 201, created.text
    original = created.json()["order_id"]
    row = _row(shop, original, "contact_email, contact_address")
    assert row["contact_email"] == "corrected@example.com"
    assert row["contact_address"] == CONTACT["address"]
    assert client.get("/api/v1/orders/session").json()["email"] == "pat@example.com"
    assert _order(client, shop, contact={**CONTACT, "address": "10 New Street"}, guest_email="later@example.com").status_code == 201
    assert _row(shop, original, "contact_email, contact_address") == row


@pytest.mark.parametrize("email", ["", "not-email", "two@@example.com", "space here@example.com"])
def test_invalid_guest_receipt_email_is_rejected(shop, email):
    assert _order(_guest(shop), shop, contact=CONTACT, guest_email=email).status_code == 422


def test_registered_checkout_cannot_replace_profile_or_verified_email(shop):
    from app.api.deps import get_current_user
    from app.db.session import system_session
    from app.main import app
    from app.models import User
    from tests.test_order_delivery_fee import _customer

    me = _customer()
    with system_session() as db:
        user = db.get(User, me)
        user.full_name, user.phone, user.address = "Saved Name", "3125550123", "Old saved address"
        original_email = user.email
        db.flush()
        db.expunge(user)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        response = _order(_staff_client(shop.slug), shop, contact=CONTACT, guest_email="unverified@example.com")
        assert response.status_code == 201, response.text
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert _row(shop, response.json()["order_id"], "contact_email")["contact_email"] == original_email
    with system_session() as db:
        saved = db.get(User, me)
        assert (saved.full_name, saved.phone, saved.address) == ("Saved Name", "3125550123", "Old saved address")
