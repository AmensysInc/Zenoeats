"""The customer's own page: details, order history, favourites.

What matters most here is whose data comes back. Every list is the caller's
own and this restaurant's own, and a guest -- whose session ends with the
browser -- is refused the one thing that would outlive it.
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
from tests.test_order_delivery_fee import _an_item, _customer
from tests.test_staff_removal import team  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration

CONTACT = {"full_name": "Sam Regular", "phone": "+1 312 555 0199", "address": "20 Oak Ave, Chicago"}


@pytest.fixture(autouse=True)
def no_rate_limits(monkeypatch):
    from app.core import ratelimit

    monkeypatch.setattr(ratelimit, "_consume", lambda *a, **k: None)


@pytest.fixture
def shop(team):
    from app.api import deps
    from app.db.session import system_session, tenant_session

    with system_session() as session:
        team.slug = session.execute(
            text("UPDATE restaurants SET status = 'ACTIVE' WHERE id = :r RETURNING slug"),
            {"r": team.id},
        ).scalar_one()
    with tenant_session(team.id) as session:
        team.item_id = _an_item(session, team.id)
    deps._tenant_cache.clear()
    return team


@pytest.fixture
def signed_in():
    """Requests made as a real customer row, without a Clerk token to mint."""
    from app.api.deps import get_current_user
    from app.db.session import system_session
    from app.main import app
    from app.models import User

    state = {}

    def as_customer(user_id):
        with system_session() as session:
            user = session.get(User, user_id)
            session.expunge(user)
        state["user"] = user

    app.dependency_overrides[get_current_user] = lambda: state["user"]
    yield as_customer
    app.dependency_overrides.pop(get_current_user, None)


def _order(shop, customer_id, status="COMPLETED"):
    from app.db.session import tenant_session
    from app.models import Restaurant
    from app.services.orders import create_pending_order
    from app.services.pricing import price_cart

    with tenant_session(shop.id) as session:
        restaurant = session.get(Restaurant, shop.id)
        cart = price_cart(
            session, restaurant,
            [{"menu_item_id": shop.item_id, "quantity": 2, "note": None, "modifiers": []}],
        )
        order = create_pending_order(
            session, restaurant=restaurant, customer_user_id=customer_id,
            cart=cart, customer_note=None,
        )
        order.status = status
        session.flush()
        return str(order.id)


# ------------------------------------------------------------- details ---

def test_details_are_saved_and_come_back(shop):
    client = _staff_client(shop.slug)
    client.post("/api/v1/orders/guest-session", json={"email": "sam@example.com"})

    res = client.put("/api/v1/customer/profile", json=CONTACT)
    assert res.status_code == 200, res.text
    assert res.json()["phone"] == "+1 312 555 0199"

    session = client.get("/api/v1/orders/session").json()
    assert session["full_name"] == "Sam Regular"
    assert session["address"] == "20 Oak Ave, Chicago"


def test_details_follow_checkouts_rules(shop):
    client = _staff_client(shop.slug)
    client.post("/api/v1/orders/guest-session", json={"email": "sam@example.com"})
    assert client.put("/api/v1/customer/profile", json={**CONTACT, "phone": "no"}).status_code == 422
    assert client.put("/api/v1/customer/profile", json={**CONTACT, "full_name": ""}).status_code == 422
    assert client.put("/api/v1/customer/profile", json={**CONTACT, "address": ""}).status_code == 422


def test_the_profile_needs_someone_to_belong_to(shop):
    assert _staff_client(shop.slug).put("/api/v1/customer/profile", json=CONTACT).status_code == 401


def test_a_page_on_another_site_cannot_write_to_it(shop):
    client = _staff_client(shop.slug)
    client.post("/api/v1/orders/guest-session", json={"email": "sam@example.com"})
    res = client.put(
        "/api/v1/customer/profile", json=CONTACT, headers={"Origin": "https://evil.example"}
    )
    assert res.status_code == 403


# -------------------------------------------------------------- orders ---

def test_history_is_mine_paid_and_newest_first(shop, signed_in):
    me, someone_else = _customer(), _customer()
    first = _order(shop, me)
    second = _order(shop, me, status="PREPARING")
    _order(shop, me, status="PENDING_PAYMENT")
    _order(shop, me, status="EXPIRED")
    _order(shop, someone_else)

    signed_in(me)
    body = _staff_client(shop.slug).get("/api/v1/customer/orders").json()

    assert [o["order_id"] for o in body["orders"]] == [second, first]
    assert body["orders"][0]["lines"] == ["2× Smash Burger"]
    assert body["next_before"] is None


def test_history_pages_without_repeating_a_row(shop, signed_in):
    me = _customer()
    ids = [_order(shop, me) for _ in range(3)]
    signed_in(me)
    client = _staff_client(shop.slug)

    page = client.get("/api/v1/customer/orders", params={"limit": 2}).json()
    assert len(page["orders"]) == 2 and page["next_before"]
    rest = client.get(
        "/api/v1/customer/orders", params={"limit": 2, "before": page["next_before"]}
    ).json()

    seen = [o["order_id"] for o in page["orders"] + rest["orders"]]
    assert sorted(seen) == sorted(ids)
    assert rest["next_before"] is None


# ---------------------------------------------------------- favourites ---

def test_a_guest_cannot_save_favourites(shop):
    client = _staff_client(shop.slug)
    client.post("/api/v1/orders/guest-session", json={"email": "sam@example.com"})
    res = client.put(f"/api/v1/customer/favourites/{shop.item_id}")
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "ACCOUNT_REQUIRED"


def test_saving_twice_is_one_favourite_and_removing_takes_it_away(shop, signed_in):
    signed_in(_customer())
    client = _staff_client(shop.slug)
    url = f"/api/v1/customer/favourites/{shop.item_id}"

    assert client.put(url).status_code == 204
    assert client.put(url).status_code == 204
    saved = client.get("/api/v1/customer/favourites").json()
    assert [f["item_id"] for f in saved] == [str(shop.item_id)]
    assert saved[0]["name"] == "Smash Burger"

    assert client.delete(url).status_code == 204
    assert client.delete(url).status_code == 204
    assert client.get("/api/v1/customer/favourites").json() == []


def test_favourites_are_each_customers_own(shop, signed_in):
    client = _staff_client(shop.slug)
    signed_in(_customer())
    client.put(f"/api/v1/customer/favourites/{shop.item_id}")

    signed_in(_customer())
    assert client.get("/api/v1/customer/favourites").json() == []


def test_an_item_not_on_this_menu_cannot_be_saved(shop, signed_in):
    signed_in(_customer())
    res = _staff_client(shop.slug).put(f"/api/v1/customer/favourites/{uuid.uuid4()}")
    assert res.status_code == 404


def test_an_item_taken_off_the_menu_drops_out_of_the_list(shop, signed_in):
    from app.db.session import tenant_session

    signed_in(_customer())
    client = _staff_client(shop.slug)
    client.put(f"/api/v1/customer/favourites/{shop.item_id}")

    with tenant_session(shop.id) as session:
        session.execute(
            text("UPDATE menu_items SET deleted_at = now() WHERE id = :i"), {"i": shop.item_id}
        )
    assert client.get("/api/v1/customer/favourites").json() == []


# ------------------------------------------------------- verified email ---
def test_email_sync_refuses_guests(shop):
    client = _staff_client(shop.slug)
    client.post("/api/v1/orders/guest-session", json={"email": "guest@example.com"})
    assert client.post("/api/v1/customer/profile/email-sync").status_code == 403


@pytest.mark.parametrize("case, status", [("unverified", 409), ("foreign", 409), ("unavailable", 503), ("verified", 200)])
def test_email_sync_requires_own_verified_clerk_primary(shop, signed_in, monkeypatch, case, status):
    from app.db.session import system_session
    from app.models import User
    from app.services import clerk_customers

    me = _customer()
    with system_session() as db:
        user = db.get(User, me)
        original_email, clerk_id = user.email, user.clerk_user_id
        user.full_name, user.phone, user.address = CONTACT.values()
    # Unique per run: one address per customer is a database constraint, so a
    # literal here would collide with the row the previous run left behind.
    next_email = f"verified-next-{clerk_id}@example.com"
    signed_in(me)
    profile = None if case == "unavailable" else clerk_customers.ClerkProfile(
        clerk_user_id="someone_else" if case == "foreign" else clerk_id,
        email=next_email, email_verified=case != "unverified",
        full_name="Provider name must not overwrite saved contact",
    )
    monkeypatch.setattr(clerk_customers, "fetch_profile", lambda asked: profile if asked == clerk_id else None)
    response = _staff_client(shop.slug).post("/api/v1/customer/profile/email-sync")
    assert response.status_code == status, response.text
    with system_session() as db:
        user = db.get(User, me)
        assert user.email == (next_email if case == "verified" else original_email)
        assert (user.full_name, user.phone, user.address) == tuple(CONTACT.values())
