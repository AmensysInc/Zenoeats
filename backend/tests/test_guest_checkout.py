"""Ordering without an account.

A guest is the one caller who proves nothing, so what is tested here is
mostly what a guest must *not* reach: another guest's order, a staff row, an
order the emailed link was not issued for.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core import errors, guest_auth, staff_auth
from app.services import guest_customers


@pytest.fixture(autouse=True)
def sweep_test_guests():
    """Remove the guest rows a test minted.

    Guests are created per checkout by design and are swept in production by
    the retention job, but a test suite should not be leaning on a scheduled
    job to tidy up after itself.
    """
    from app.db.base import utcnow

    started = utcnow()
    yield
    from app.services import retention

    with retention._platform_transaction() as session:
        session.execute(
            text("DELETE FROM users WHERE kind = 'GUEST' AND created_at >= :t"),
            {"t": started},
        )

integration = pytest.mark.integration


@pytest.fixture(autouse=True)
def no_rate_limits(monkeypatch):
    from app.core import ratelimit

    monkeypatch.setattr(ratelimit, "_consume", lambda *a, **k: None)


def _client(host: str = "spicehouse.zenoeats.local"):
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app, base_url=f"http://{host}")


@pytest.fixture
def active_restaurant():
    """A restaurant a guest is allowed to order from.

    Torn down afterwards, as the other suites do: without it every run left
    another restaurant behind in the development database, and a menu full of
    "Guest Test" is a nuisance the next person has to clean up by hand.
    """
    from app.db.session import system_session, tenant_session
    from app.models import Restaurant, RestaurantStatus

    slug = f"guest-{uuid.uuid4().hex[:8]}"
    with system_session() as session:
        restaurant = Restaurant(
            slug=slug, name="Guest Test", status=RestaurantStatus.ACTIVE.value,
            timezone="UTC", currency="USD",
        )
        session.add(restaurant)
        session.flush()
        rid = restaurant.id

    yield slug

    with tenant_session(rid) as session:
        session.execute(text("DELETE FROM restaurants WHERE id = :r"), {"r": rid})


# ------------------------------------------------------------ unit ---

def test_a_guest_token_round_trips():
    user_id = uuid.uuid4()
    principal = guest_auth.verify_session(guest_auth.issue_session(user_id))
    assert principal is not None and principal.user_id == user_id


def test_a_staff_session_is_not_a_guest_session():
    """Both are HS256 on SESSION_SECRET. Only the typ claim keeps them apart,
    and this is the token anyone can mint for themselves by asking."""
    assert guest_auth.verify_session(staff_auth.issue_session(uuid.uuid4())) is None


def test_a_guest_session_is_not_a_staff_session():
    assert staff_auth.verify_session(guest_auth.issue_session(uuid.uuid4())) is None


def test_a_guest_session_is_not_an_order_view_token():
    order_id = uuid.uuid4()
    assert guest_auth.order_token_grants(guest_auth.issue_session(order_id), order_id) is False


def test_an_order_token_opens_only_its_own_order():
    mine, yours = uuid.uuid4(), uuid.uuid4()
    token = guest_auth.issue_order_token(mine)
    assert guest_auth.order_token_grants(token, mine) is True
    assert guest_auth.order_token_grants(token, yours) is False


def test_a_forged_order_token_is_refused():
    order_id = uuid.uuid4()
    assert guest_auth.order_token_grants("not-a-token", order_id) is False
    assert guest_auth.order_token_grants("", order_id) is False


@pytest.mark.parametrize(
    "address",
    ["sam@example.com", "SAM@Example.COM", "a.b+tag@sub.example.co.uk"],
)
def test_a_plausible_address_is_accepted(address):
    from app.schemas.api import GuestSessionIn

    assert GuestSessionIn(email=address).email == address.strip().lower()


@pytest.mark.parametrize("address", ["sam", "sam@localhost", "@example.com", "sam@.com"])
def test_an_address_no_receipt_could_reach_is_refused(address):
    import pydantic
    from app.schemas.api import GuestSessionIn

    with pytest.raises(pydantic.ValidationError):
        GuestSessionIn(email=address)


# ------------------------------------------------------- integration ---

@integration
def test_a_guest_row_is_reachable_by_its_own_token():
    guest = guest_customers.create_guest("Guest@Example.com", "  Pat Guest  ")
    assert guest.kind == "GUEST"
    assert guest.email == "guest@example.com"
    assert guest.full_name == "Pat Guest"
    assert guest.password_hash is None

    again = guest_customers.guest_for_session(guest.id)
    assert again.id == guest.id


@integration
def test_two_guests_who_type_the_same_address_are_different_people():
    """The address is where a receipt goes, not a claim about who this is.
    Matching on it would hand whoever typed it everyone else's pickup PINs."""
    first = guest_customers.create_guest("same@example.com", None)
    second = guest_customers.create_guest("same@example.com", None)
    assert first.id != second.id


@integration
def test_a_guest_token_naming_a_staff_row_opens_nothing():
    from app.db.session import system_session
    from app.models import User, UserKind

    with system_session() as session:
        staff = User(
            kind=UserKind.STAFF.value,
            email=f"staff-{uuid.uuid4().hex[:8]}@zenoeats.invalid",
        )
        session.add(staff)
        session.flush()
        staff_id = staff.id

    with pytest.raises(errors.ApiError) as caught:
        guest_customers.guest_for_session(staff_id)
    assert caught.value.status_code == 401


@integration
def test_a_token_for_a_row_that_no_longer_exists_is_refused():
    with pytest.raises(errors.ApiError) as caught:
        guest_customers.guest_for_session(uuid.uuid4())
    assert caught.value.status_code == 401


@integration
def test_starting_a_guest_session_sets_an_httponly_cookie(active_restaurant):
    # On the restaurant's own subdomain, as in production: the tenant comes
    # from the Host header and nothing in the body decides it.
    client = _client(f"{active_restaurant}.zenoeats.local")
    res = client.post(
        "/api/v1/orders/guest-session",
        json={"email": "pat@example.com", "full_name": "Pat Guest"},
    )
    assert res.status_code == 201
    assert res.json() == {"email": "pat@example.com", "full_name": "Pat Guest"}

    cookie = res.headers.get("set-cookie", "")
    assert guest_auth.SESSION_COOKIE in cookie
    assert "httponly" in cookie.lower()

    # And the cookie it set is the identity the ordering endpoints will see.
    token = client.cookies.get(guest_auth.SESSION_COOKIE)
    principal = guest_auth.verify_session(token)
    assert principal is not None
    assert guest_customers.guest_for_session(principal.user_id).email == "pat@example.com"


@integration
def test_a_guest_cannot_be_minted_against_an_unknown_restaurant():
    res = _client(f"no-such-{uuid.uuid4().hex[:8]}.zenoeats.local").post(
        "/api/v1/orders/guest-session",
        json={"email": "pat@example.com"},
    )
    assert res.status_code == 404


@integration
def test_ordering_without_a_session_or_a_cookie_is_refused(active_restaurant):
    res = _client(f"{active_restaurant}.zenoeats.local").get(f"/api/v1/orders/{uuid.uuid4()}")
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "UNAUTHENTICATED"


@integration
def test_the_session_endpoint_names_a_guest_as_one(active_restaurant):
    client = _client(f"{active_restaurant}.zenoeats.local")
    client.post(
        "/api/v1/orders/guest-session",
        json={"email": "pat@example.com", "full_name": "Pat Guest"},
    )
    res = client.get("/api/v1/orders/session")
    assert res.status_code == 200
    assert res.json() == {
        "email": "pat@example.com", "full_name": "Pat Guest", "is_guest": True,
        # Nothing saved yet: those come from the first order.
        "phone": None, "address": None, "email_pending": False,
        # Nor has anything been agreed to. A guest is not asked to before
        # checkout, where the line above the button says what continuing
        # means and the order records it.
        "terms_accepted": False,
    }


@integration
def test_the_session_endpoint_refuses_a_browser_holding_nothing(active_restaurant):
    res = _client(f"{active_restaurant}.zenoeats.local").get("/api/v1/orders/session")
    assert res.status_code == 401


@integration
def test_a_staff_cookie_is_not_a_guest_session(active_restaurant):
    """Both cookies ride on the same origin. The typ claim is what stops a
    signed-in cashier's session from also being a customer identity."""
    client = _client(f"{active_restaurant}.zenoeats.local")
    client.cookies.set(guest_auth.SESSION_COOKIE, staff_auth.issue_session(uuid.uuid4()))
    assert client.get("/api/v1/orders/session").status_code == 401
