"""Operator sessions end on the server, not just in the browser.

Admin and staff sessions are signed cookies. Signing out used to only delete
the cookie, so a copy taken before sign-out kept working until it expired.
users.sessions_valid_after now refuses every token issued before it.

Tokens are minted directly rather than through the login endpoints, which
are rate limited per address and would trip after a few runs.
"""

import uuid

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from app.core import platform_auth, staff_auth

from tests.test_admin_restaurants import (  # noqa: F401  (fixtures)
    _create,
    _email,
    _owner,
    admin_user,
    cleanup,
)

pytestmark = pytest.mark.integration


def _app():
    from app.main import app

    return app


# ----------------------------------------------------------------- admin ---

@pytest.fixture
def registered_admin(monkeypatch):
    """A platform admin in ADMIN_USERS, with an account row of its own so
    one test's sign-out never ends another's session."""
    email = f"revoke-{uuid.uuid4().hex[:8]}@zenoeats.invalid"
    monkeypatch.setattr(
        platform_auth.settings, "ADMIN_USERS", f"{email}:{platform_auth.hash_password('x' * 16)}"
    )
    return platform_auth.PlatformAdmin(email=email)


def _admin_client(token):
    client = TestClient(_app(), base_url="http://admin.zenoeats.local")
    client.cookies.set(platform_auth.SESSION_COOKIE, token)
    return client


def test_a_token_copied_before_admin_sign_out_stops_working(registered_admin):
    token = platform_auth.issue_session(registered_admin)
    assert _admin_client(token).get("/api/v1/admin/me").status_code == 200

    assert _admin_client(token).post("/api/v1/admin/logout").status_code == 204

    replay = _admin_client(token).get("/api/v1/admin/me")
    assert replay.status_code == 401
    assert replay.json()["detail"]["code"] == "UNAUTHENTICATED"


def test_admin_sign_out_ends_sessions_on_every_device(registered_admin):
    laptop = platform_auth.issue_session(registered_admin)
    phone = platform_auth.issue_session(registered_admin)

    assert _admin_client(laptop).post("/api/v1/admin/logout").status_code == 204
    assert _admin_client(phone).get("/api/v1/admin/me").status_code == 401


def test_signing_back_in_straight_after_sign_out_works(registered_admin):
    old = platform_auth.issue_session(registered_admin)
    assert _admin_client(old).post("/api/v1/admin/logout").status_code == 204

    fresh = platform_auth.issue_session(registered_admin)
    assert _admin_client(fresh).get("/api/v1/admin/me").status_code == 200


def test_signing_out_without_a_valid_session_is_not_an_error():
    client = TestClient(_app(), base_url="http://admin.zenoeats.local")
    assert client.post("/api/v1/admin/logout").status_code == 204
    client.cookies.set(platform_auth.SESSION_COOKIE, "not-a-token")
    assert client.post("/api/v1/admin/logout").status_code == 204


# ----------------------------------------------------------------- staff ---

def _staff(slug, token):
    client = TestClient(_app(), base_url=f"http://{slug}.zenoeats.local")
    client.cookies.set(staff_auth.SESSION_COOKIE, token)
    return client


def test_changing_a_password_ends_sessions_issued_under_the_old_one(admin_user, cleanup):
    restaurant = _create(admin_user, cleanup)
    email = _email()
    out, _ = _owner(admin_user, restaurant.id, email)

    tablet = staff_auth.issue_session(out.user_id)
    phone = staff_auth.issue_session(out.user_id)
    assert _staff(restaurant.slug, tablet).get("/api/v1/restaurant/me").status_code == 200

    changed = _staff(restaurant.slug, phone).post(
        "/api/v1/restaurant/change-password",
        json={"current_password": out.temporary_password, "new_password": "a brand new password 1"},
    )
    assert changed.status_code == 204

    assert _staff(restaurant.slug, tablet).get("/api/v1/restaurant/me").status_code == 401
    assert _staff(restaurant.slug, phone).get("/api/v1/restaurant/me").status_code == 401

    fresh = staff_auth.issue_session(out.user_id)
    assert _staff(restaurant.slug, fresh).get("/api/v1/restaurant/me").status_code == 200


def test_a_super_admin_reset_ends_the_owners_sessions(admin_user, cleanup):
    from app.api.v1.admin import reset_owner_password

    restaurant = _create(admin_user, cleanup)
    email = _email()
    out, _ = _owner(admin_user, restaurant.id, email)
    before = staff_auth.issue_session(out.user_id)
    assert _staff(restaurant.slug, before).get("/api/v1/restaurant/me").status_code == 200

    from app.schemas.api import CreateOwnerIn

    reset_owner_password(
        restaurant.id, CreateOwnerIn(email=email), BackgroundTasks(), admin=admin_user
    )

    assert _staff(restaurant.slug, before).get("/api/v1/restaurant/me").status_code == 401


def test_staff_sign_out_stays_on_this_device(admin_user, cleanup):
    """Restaurants share one login across kitchen tablets: one person signing
    out must not sign the tablet on the pass out too."""
    restaurant = _create(admin_user, cleanup)
    out, _ = _owner(admin_user, restaurant.id, _email())
    tablet = staff_auth.issue_session(out.user_id)
    phone = staff_auth.issue_session(out.user_id)

    assert _staff(restaurant.slug, phone).post("/api/v1/restaurant/logout").status_code == 204
    assert _staff(restaurant.slug, tablet).get("/api/v1/restaurant/me").status_code == 200


def test_signing_out_everywhere_ends_every_device(admin_user, cleanup):
    """For a lost phone, or a session left open on a borrowed device, without
    having to change the password."""
    restaurant = _create(admin_user, cleanup)
    out, _ = _owner(admin_user, restaurant.id, _email())
    tablet = staff_auth.issue_session(out.user_id)
    lost_phone = staff_auth.issue_session(out.user_id)

    res = _staff(restaurant.slug, tablet).post("/api/v1/restaurant/logout-everywhere")
    assert res.status_code == 204
    assert staff_auth.SESSION_COOKIE in res.headers.get("set-cookie", "")

    assert _staff(restaurant.slug, lost_phone).get("/api/v1/restaurant/me").status_code == 401
    assert _staff(restaurant.slug, tablet).get("/api/v1/restaurant/me").status_code == 401

    # Signing straight back in works; the password never changed.
    fresh = staff_auth.issue_session(out.user_id)
    assert _staff(restaurant.slug, fresh).get("/api/v1/restaurant/me").status_code == 200


def test_signing_out_everywhere_needs_a_session():
    client = TestClient(_app(), base_url="http://spicehouse.zenoeats.local")
    assert client.post("/api/v1/restaurant/logout-everywhere").status_code == 401
