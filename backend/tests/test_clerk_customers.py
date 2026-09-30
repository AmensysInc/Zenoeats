"""Customers through Clerk: token checks, the mirrored row, and the invite flow.

Tokens here are real RS256 JWTs signed with a key generated per run, with
Clerk's key lookup pointed at it. Everything after the signature -- claims,
authorized party, the users row, the order endpoints -- runs for real.
"""

import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import text

from app.core import auth, staff_auth
from app.db.base import utcnow
from app.services import clerk_customers

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _Key:
    key = _KEY.public_key()


class _FakeJWKS:
    def get_signing_key_from_jwt(self, _token):
        return _Key()


@pytest.fixture(autouse=True)
def clerk_keys(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(auth, "_jwks", lambda: _FakeJWKS())
    monkeypatch.setattr(settings, "CLERK_ISSUER", "https://test.clerk.accounts.dev")
    monkeypatch.setattr(settings, "AUTH_DEV_BYPASS", False)


@pytest.fixture(autouse=True)
def no_rate_limits(monkeypatch):
    from app.core import ratelimit

    monkeypatch.setattr(ratelimit, "_consume", lambda *a, **k: None)


def _token(sub: str, azp: str | None = "https://spicehouse.zenoeats.local:8443", **extra) -> str:
    now = int(time.time())
    claims = {"sub": sub, "iat": now, "exp": now + 60, "iss": "https://test.clerk.accounts.dev"}
    if azp is not None:
        claims["azp"] = azp
    claims.update(extra)
    return jwt.encode(claims, _KEY, algorithm="RS256")


def _clerk_id() -> str:
    return f"user_test_{uuid.uuid4().hex[:12]}"


# ------------------------------------------------------------ unit ---

@pytest.mark.parametrize(
    "azp, ours",
    [
        ("https://spicehouse.zenoeats.local:8443", True),
        ("http://spicehouse.zenoeats.local:3000", True),
        ("https://zenoeats.local", True),
        ("https://zenoeats.com", True),
        ("https://spicehouse.zenoeats.com", True),
        ("https://admin.zenoeats.com", True),
        ("https://stg9.zenoeats.com", True),
        ("https://spicehouse.stg9.zenoeats.com", True),
        ("https://admin.stg9.zenoeats.com", True),
        ("https://evil.example", False),
        ("https://zenoeats.local.evil.example", False),
        ("https://zenoeats.com.evil.example", False),
        ("https://notzenoeats.com", False),
        ("http://evilzenoeats.local", False),
        ("zenoeats.local", False),
    ],
)
def test_only_tokens_minted_for_our_own_pages_are_accepted(azp, ours):
    assert auth._authorized_party_is_ours(azp) is ours


def test_a_valid_token_names_its_clerk_user():
    principal = auth.verify_clerk_token(_token("user_abc"))
    assert principal.clerk_user_id == "user_abc"


def test_a_token_for_a_foreign_site_is_refused():
    with pytest.raises(auth.AuthError):
        auth.verify_clerk_token(_token("user_abc", azp="https://evil.example"))


def test_an_expired_or_foreign_issuer_token_is_refused():
    now = int(time.time())
    expired = jwt.encode(
        {"sub": "u", "iat": now - 120, "exp": now - 60, "iss": "https://test.clerk.accounts.dev"},
        _KEY, algorithm="RS256",
    )
    with pytest.raises(auth.AuthError):
        auth.verify_clerk_token(expired)
    with pytest.raises(auth.AuthError):
        auth.verify_clerk_token(_token("u", iss="https://someone-else.clerk.accounts.dev"))


def test_a_staff_session_cookie_value_is_not_a_clerk_token():
    """Staff tokens are HS256 with our own secret. Presented as a bearer token
    they must fail Clerk's RS256 check, not slip through it."""
    with pytest.raises(auth.AuthError):
        auth.verify_clerk_token(staff_auth.issue_session(uuid.uuid4()))


def test_the_primary_email_and_name_are_read_from_a_clerk_user():
    profile = clerk_customers.profile_from_payload({
        "id": "user_1",
        "primary_email_address_id": "idn_2",
        "email_addresses": [
            {"id": "idn_1", "email_address": "old@example.com",
             "verification": {"status": "verified"}},
            {"id": "idn_2", "email_address": "Sam@Example.com",
             "verification": {"status": "verified"}},
        ],
        "first_name": "Sam",
        "last_name": "Customer",
    })
    assert profile.email == "sam@example.com"
    assert profile.email_verified is True
    assert profile.full_name == "Sam Customer"


# ------------------------------------------------------- integration ---

integration = pytest.mark.integration


@integration
def test_a_new_customer_gets_a_row_with_their_clerk_email(monkeypatch):
    clerk_id = _clerk_id()
    calls = []

    def fetch(cid):
        calls.append(cid)
        return clerk_customers.ClerkProfile(cid, "new@zenoeats.invalid", True, "New Person")

    monkeypatch.setattr(clerk_customers, "fetch_profile", fetch)

    user = clerk_customers.customer_for_clerk_user(clerk_id)
    assert user.email == "new@zenoeats.invalid"
    assert user.full_name == "New Person"
    assert user.kind == "CUSTOMER"

    # Known and complete: no second trip to Clerk.
    again = clerk_customers.customer_for_clerk_user(clerk_id)
    assert again.id == user.id
    assert calls == [clerk_id]


@integration
def test_when_clerk_is_unreachable_the_address_is_filled_in_later(monkeypatch):
    clerk_id = _clerk_id()
    monkeypatch.setattr(clerk_customers, "fetch_profile", lambda cid: None)
    user = clerk_customers.customer_for_clerk_user(clerk_id)
    assert user.email == f"{clerk_id}@pending.local"

    monkeypatch.setattr(
        clerk_customers, "fetch_profile",
        lambda cid: clerk_customers.ClerkProfile(cid, "later@zenoeats.invalid", True, None),
    )
    assert clerk_customers.customer_for_clerk_user(clerk_id).email == "later@zenoeats.invalid"


@integration
def test_a_clerk_id_on_a_staff_row_opens_nothing(monkeypatch):
    from app.db.session import system_session
    from app.models import User, UserKind

    clerk_id = _clerk_id()
    with system_session() as session:
        session.add(User(kind=UserKind.STAFF.value, clerk_user_id=clerk_id,
                         email=f"{clerk_id}@zenoeats.invalid"))

    monkeypatch.setattr(clerk_customers, "fetch_profile", lambda cid: None)
    from app.core import errors

    with pytest.raises(errors.ApiError) as caught:
        clerk_customers.customer_for_clerk_user(clerk_id)
    assert caught.value.status_code == 401


@integration
def test_a_customer_deleted_in_clerk_is_refused(monkeypatch):
    from app.core import errors
    from app.db.session import system_session

    clerk_id = _clerk_id()
    monkeypatch.setattr(
        clerk_customers, "fetch_profile",
        lambda cid: clerk_customers.ClerkProfile(cid, "gone@zenoeats.invalid", True, None),
    )
    clerk_customers.customer_for_clerk_user(clerk_id)

    with system_session() as session:
        clerk_customers.deactivate(session, clerk_id)

    with pytest.raises(errors.ApiError) as caught:
        clerk_customers.customer_for_clerk_user(clerk_id)
    assert caught.value.status_code == 403


def _client(host: str):
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app, base_url=f"http://{host}")


@pytest.fixture
def staff_restaurant():
    """An active restaurant with an owner who can sign in."""
    from app.db.session import system_session, tenant_session
    from app.models import (
        Restaurant, RestaurantStatus, RestaurantUser, StaffRole, StaffStatus, User, UserKind,
    )

    slug = f"auth-{uuid.uuid4().hex[:8]}"
    owner_email = f"owner-{uuid.uuid4().hex[:8]}@zenoeats.invalid"
    with system_session() as session:
        restaurant = Restaurant(slug=slug, name="Auth Test", status=RestaurantStatus.ACTIVE.value,
                                timezone="UTC", currency="USD")
        owner = User(kind=UserKind.STAFF.value, email=owner_email,
                     password_hash=staff_auth.hash_password("owner password 123"))
        session.add_all([restaurant, owner])
        session.flush()
        rid, owner_id = restaurant.id, owner.id

    with tenant_session(rid) as session:
        session.add(RestaurantUser(restaurant_id=rid, user_id=owner_id,
                                   role_code=StaffRole.ADMIN.value,
                                   status=StaffStatus.ACTIVE.value,
                                   invited_at=utcnow(), accepted_at=utcnow()))

    yield slug, owner_email

    with tenant_session(rid) as session:
        session.execute(text("DELETE FROM restaurant_users WHERE restaurant_id = :r"), {"r": rid})
        session.execute(text("DELETE FROM restaurants WHERE id = :r"), {"r": rid})


@integration
def test_the_order_endpoints_accept_a_clerk_bearer_token(monkeypatch, staff_restaurant):
    slug, _ = staff_restaurant
    monkeypatch.setattr(
        clerk_customers, "fetch_profile",
        lambda cid: clerk_customers.ClerkProfile(cid, "buyer@zenoeats.invalid", True, None),
    )
    client = _client(f"{slug}.zenoeats.local")

    assert client.get("/api/v1/orders").status_code == 401
    res = client.get("/api/v1/orders", headers={"Authorization": f"Bearer {_token(_clerk_id())}"})
    assert res.status_code == 200, res.text
    assert res.json() == []

    # A customer token opens nothing in either portal.
    bearer = {"Authorization": f"Bearer {_token(_clerk_id())}"}
    assert client.get("/api/v1/restaurant/me", headers=bearer).status_code == 401
    # The platform API is only reachable on admin.<root domain> at all.
    assert client.get("/api/v1/admin/me", headers=bearer).status_code == 404
    admin = _client("admin.zenoeats.local")
    assert admin.get("/api/v1/admin/me", headers=bearer).status_code == 401


@integration
def test_an_invited_person_signs_in_changes_password_then_accepts(staff_restaurant, queued_emails):
    slug, owner_email = staff_restaurant
    host = f"{slug}.zenoeats.local"

    owner = _client(host)
    assert owner.post("/api/v1/restaurant/login",
                      json={"email": owner_email, "password": "owner password 123"}).status_code == 200

    invitee_email = f"cook-{uuid.uuid4().hex[:8]}@zenoeats.invalid"
    invite = owner.post("/api/v1/restaurant/staff",
                        json={"email": invitee_email, "role_code": "KITCHEN"})
    assert invite.status_code == 201, invite.text
    temp = invite.json()["temporary_password"]
    # The invitation email is handed to the worker, for this membership, with
    # the temporary password sealed -- never in plain text, because the
    # broker writes what it holds to disk.
    assert temp
    from app.core import crypto

    handed = [
        args for name, args in queued_emails
        if name == "send_staff_invitation"
        and args[:2] == (str(invite_restaurant_id(host)), invite.json()["id"])
    ]
    assert len(handed) == 1, queued_emails
    sealed = handed[0][2]
    assert temp not in sealed
    assert crypto.decrypt_field(sealed) == temp

    cook = _client(host)
    login = cook.post("/api/v1/restaurant/login", json={"email": invitee_email, "password": temp})
    assert login.status_code == 200, login.text
    assert login.json()["membership_status"] == "INVITED"
    assert login.json()["must_change_password"] is True

    # Nothing but the password change until it is done.
    assert cook.post("/api/v1/restaurant/staff/accept").status_code == 403

    changed = cook.post("/api/v1/restaurant/change-password",
                        json={"current_password": temp, "new_password": "my own password 1"})
    assert changed.status_code == 204
    assert cook.post("/api/v1/restaurant/login",
                     json={"email": invitee_email, "password": "my own password 1"}).status_code == 200

    # Signed in and invited, but not yet a member: the board is refused.
    assert cook.get("/api/v1/restaurant/me").json()["membership_status"] == "INVITED"
    assert cook.get("/api/v1/restaurant/orders").status_code == 403

    accepted = cook.post("/api/v1/restaurant/staff/accept")
    assert accepted.status_code == 200, accepted.text
    assert cook.get("/api/v1/restaurant/me").json()["membership_status"] == "ACTIVE"
    assert cook.get("/api/v1/restaurant/orders").status_code == 200

    team = owner.get("/api/v1/restaurant/staff").json()
    assert any(m["email"] == invitee_email and m["status"] == "ACTIVE" for m in team)


def test_a_placeholder_address_never_becomes_a_receipt_address():
    """Stripe would email a receipt to user_...@pending.local on every order
    placed while Clerk's profile was unavailable."""
    from app.models import User, UserKind

    pending = User(kind=UserKind.CUSTOMER.value, email="user_abc@pending.local")
    real = User(kind=UserKind.CUSTOMER.value, email="sam@example.com")
    assert clerk_customers.receipt_address(pending) is None
    assert clerk_customers.receipt_address(real) == "sam@example.com"


def invite_restaurant_id(host: str) -> str:
    from app.db.session import system_session

    slug = host.split(".")[0]
    with system_session() as session:
        return str(session.execute(text("SELECT id FROM restaurants WHERE slug = :s"), {"s": slug}).scalar_one())
