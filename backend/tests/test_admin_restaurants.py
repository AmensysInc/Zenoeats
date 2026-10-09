"""Super admin restaurant administration, against the real database.

Restaurant creation had no test, and broke without anyone noticing: the
starter item types were written through the system role, which may not touch
menu tables, and every "Create as draft" answered 500.
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import text

from app.core import errors

pytestmark = pytest.mark.integration


@pytest.fixture
def admin_user():
    from app.db.session import system_session
    from app.models import User, UserKind

    email = "admin-restaurants-tests@zenoeats.invalid"
    with system_session() as session:
        found = session.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email}).first()
        if found:
            return SimpleNamespace(id=found.id, email=email)
        user = User(kind=UserKind.PLATFORM_ADMIN.value, email=email, full_name="Admin Tests",
                    is_platform_admin=True)
        session.add(user)
        session.flush()
        return SimpleNamespace(id=user.id, email=email)


@pytest.fixture
def cleanup():
    """Purge every restaurant a test registers, whatever happened."""
    from app.api.v1.admin import _PURGE_ORDER
    from app.db.session import system_session, tenant_session

    created = []
    yield created
    for rid in created:
        # Activation lists a restaurant on the platform root, and the location
        # row holds a RESTRICT key on it. System role: the app role only reads.
        with system_session() as session:
            session.execute(text("DELETE FROM locations WHERE restaurant_id = :r"), {"r": rid})
        with tenant_session(rid) as session:
            for table in _PURGE_ORDER:
                session.execute(text(f"DELETE FROM {table} WHERE restaurant_id = :r"), {"r": rid})
            session.execute(text("DELETE FROM restaurants WHERE id = :r"), {"r": rid})


def _create(admin_user, cleanup, slug=None, **overrides):
    from app.api.v1.admin import create_restaurant
    from app.schemas.api import CreateRestaurantIn

    body = CreateRestaurantIn(
        slug=slug or f"adm-{uuid.uuid4().hex[:8]}", name="Admin Test Kitchen",
        tax_rate_bps=825, **overrides,
    )
    out = create_restaurant(body, admin=admin_user)
    cleanup.append(out.id)
    return out


def test_creating_a_restaurant_writes_it_whole(admin_user, cleanup):
    from app.db.session import tenant_session
    from app.models import ItemType, Restaurant, RestaurantOrderCounter, STARTER_ITEM_TYPES
    from sqlalchemy import select

    out = _create(admin_user, cleanup)
    assert out.status == "DRAFT"

    with tenant_session(out.id) as session:
        restaurant = session.get(Restaurant, out.id)
        assert restaurant is not None and restaurant.slug == out.slug
        counter = session.get(RestaurantOrderCounter, out.id)
        assert counter is not None and counter.next_order_number == 1001
        types = session.execute(
            select(ItemType.name).where(ItemType.restaurant_id == out.id).order_by(ItemType.sort_order)
        ).scalars().all()
        assert list(types) == list(STARTER_ITEM_TYPES)


def test_the_creation_is_audited(admin_user, cleanup):
    from app.db.session import system_session

    out = _create(admin_user, cleanup)
    with system_session() as session:
        found = session.execute(
            text(
                "SELECT 1 FROM platform_audit_logs WHERE action = 'SUPER_ADMIN_CREATE_RESTAURANT' "
                "AND scope->>'restaurant_id' = :rid"
            ),
            {"rid": str(out.id)},
        ).first()
    assert found is not None


def test_a_taken_slug_is_refused_and_nothing_half_created(admin_user, cleanup):
    from app.api.v1.admin import create_restaurant
    from app.db.session import system_session
    from app.schemas.api import CreateRestaurantIn

    first = _create(admin_user, cleanup)
    with pytest.raises(errors.ApiError) as caught:
        create_restaurant(
            CreateRestaurantIn(slug=first.slug, name="Second", tax_rate_bps=0), admin=admin_user
        )
    assert caught.value.status_code == 409
    assert caught.value.code == "SLUG_TAKEN"

    with system_session() as session:
        count = session.execute(
            text("SELECT count(*) FROM restaurants WHERE slug = :s"), {"s": first.slug}
        ).scalar_one()
    assert count == 1


def test_a_reserved_slug_is_refused(admin_user):
    from app.api.v1.admin import create_restaurant
    from app.schemas.api import CreateRestaurantIn

    with pytest.raises(errors.ApiError) as caught:
        create_restaurant(CreateRestaurantIn(slug="clerk", name="X", tax_rate_bps=0), admin=admin_user)
    assert caught.value.status_code == 422


# ------------------------------------------------------------- owners ---

def _owner(admin_user, restaurant_id, email, full_name=None):
    from fastapi import BackgroundTasks

    from app.api.v1.admin import create_restaurant_owner
    from app.schemas.api import CreateOwnerIn

    background = BackgroundTasks()
    out = create_restaurant_owner(
        restaurant_id, CreateOwnerIn(email=email, full_name=full_name), background, admin=admin_user
    )
    return out, background


def _set_own_password(email, password):
    """What changing the temporary password at first sign-in leaves behind."""
    from app.core import staff_auth
    from app.db.session import system_session

    with system_session() as session:
        session.execute(
            text("UPDATE users SET password_hash = :h, must_change_password = false "
                 "WHERE email = :e AND kind = 'STAFF'"),
            {"h": staff_auth.hash_password(password), "e": email},
        )


def _staff_client(slug):
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app, base_url=f"http://{slug}.zenoeats.local")


def _email():
    return f"owner-{uuid.uuid4().hex[:8]}@zenoeats.invalid"


def test_a_new_owner_gets_a_temporary_password_and_is_active(admin_user, cleanup, monkeypatch):
    from app.core import ratelimit

    monkeypatch.setattr(ratelimit, "_consume", lambda *a, **k: None)
    restaurant = _create(admin_user, cleanup)
    email = _email()
    out, background = _owner(admin_user, restaurant.id, email, "New Owner")

    assert out.status == "ACTIVE"
    assert out.temporary_password
    assert background.tasks == []  # credentials are passed on by the admin

    login = _staff_client(restaurant.slug).post(
        "/api/v1/restaurant/login", json={"email": email, "password": out.temporary_password}
    )
    assert login.status_code == 200
    assert login.json()["membership_status"] == "ACTIVE"


def test_someone_who_owns_one_restaurant_can_be_owner_of_another(admin_user, cleanup, monkeypatch):
    """The dead end this fixes: creating refused the address, and a reset
    refused anyone not already staff at the second restaurant."""
    from app.core import ratelimit

    monkeypatch.setattr(ratelimit, "_consume", lambda *a, **k: None)
    first = _create(admin_user, cleanup)
    second = _create(admin_user, cleanup)
    email = _email()

    _owner(admin_user, first.id, email)
    _set_own_password(email, "my own password 123")

    out, background = _owner(admin_user, second.id, email)
    assert out.status == "INVITED"
    assert out.temporary_password is None  # their own password is left alone
    assert len(background.tasks) == 1      # and they are emailed the invitation

    # Still signs in to the first restaurant exactly as before.
    assert _staff_client(first.slug).post(
        "/api/v1/restaurant/login", json={"email": email, "password": "my own password 123"}
    ).status_code == 200

    # Signs in to the second with the same password, sees the invitation,
    # accepts, and is owner there.
    client = _staff_client(second.slug)
    login = client.post("/api/v1/restaurant/login", json={"email": email, "password": "my own password 123"})
    assert login.status_code == 200
    assert login.json()["membership_status"] == "INVITED"
    assert client.get("/api/v1/restaurant/staff").status_code == 403  # nothing before accepting
    assert client.post("/api/v1/restaurant/staff/accept").status_code == 200
    me = client.get("/api/v1/restaurant/me").json()
    assert me["membership_status"] == "ACTIVE" and me["role_code"] == "ADMIN"


def test_a_login_nobody_ever_used_gets_a_fresh_temporary_password(admin_user, cleanup):
    """The owner of a restaurant purged before they signed in: their address
    was stranded, since create refused it and reset needed a membership."""
    first = _create(admin_user, cleanup)
    second = _create(admin_user, cleanup)
    email = _email()

    original, _ = _owner(admin_user, first.id, email)
    out, _ = _owner(admin_user, second.id, email)
    assert out.status == "ACTIVE"
    assert out.temporary_password and out.temporary_password != original.temporary_password


def test_an_existing_team_member_is_refused_and_nothing_changes(admin_user, cleanup, monkeypatch):
    from app.core import ratelimit

    monkeypatch.setattr(ratelimit, "_consume", lambda *a, **k: None)
    restaurant = _create(admin_user, cleanup)
    email = _email()
    _owner(admin_user, restaurant.id, email)
    _set_own_password(email, "my own password 123")

    with pytest.raises(errors.ApiError) as caught:
        _owner(admin_user, restaurant.id, email)
    assert caught.value.status_code == 409
    assert caught.value.code == "ALREADY_STAFF"

    assert _staff_client(restaurant.slug).post(
        "/api/v1/restaurant/login", json={"email": email, "password": "my own password 123"}
    ).status_code == 200


def test_an_invited_owner_can_have_their_password_reset(admin_user, cleanup):
    from app.api.v1.admin import reset_owner_password
    from app.schemas.api import CreateOwnerIn

    first = _create(admin_user, cleanup)
    second = _create(admin_user, cleanup)
    email = _email()
    _owner(admin_user, first.id, email)
    _set_own_password(email, "my own password 123")
    _owner(admin_user, second.id, email)

    out = reset_owner_password(
        second.id, CreateOwnerIn(email=email), BackgroundTasks(), admin=admin_user
    )
    assert out.temporary_password
    assert out.status == "INVITED"


@pytest.mark.parametrize(
    "overrides, field",
    [
        ({"currency": "USDOLLARS"}, "currency"),
        ({"currency": "us"}, "currency"),
        ({"tagline": "x" * 201}, "tagline"),
        ({"admin_email": "a" * 321}, "admin email"),
    ],
)
def test_a_value_the_database_cannot_hold_is_a_422_not_a_500(
    admin_user, cleanup, overrides, field, monkeypatch
):
    """Found by the security pass: an overlong currency reached the INSERT and
    came back as a 500 carrying the database's own error."""
    from fastapi.testclient import TestClient

    from app.core import platform_auth
    from app.main import app

    monkeypatch.setattr(
        platform_auth.settings, "ADMIN_USERS",
        f"{admin_user.email}:{platform_auth.hash_password('x' * 16)}",
    )
    client = TestClient(app, base_url="http://admin.zenoeats.local")
    client.cookies.set(
        platform_auth.SESSION_COOKIE,
        platform_auth.issue_session(platform_auth.PlatformAdmin(email=admin_user.email)),
    )
    res = client.post(
        "/api/v1/admin/restaurants",
        json={"slug": f"bad-{uuid.uuid4().hex[:8]}", "name": "Probe", **overrides},
    )
    if res.status_code == 201:
        # Only against code without the bounds -- but then it must not be left
        # behind in whatever database the suite ran against.
        cleanup.append(res.json()["id"])
    assert res.status_code == 422, res.text
    assert field in res.json()["message"]


# --- Activation, and what the super admin can see of a restaurant's logins ---


def _location(restaurant_id):
    from app.db.session import system_session

    with system_session() as session:
        return session.execute(
            text("SELECT slug, name, status FROM locations WHERE restaurant_id = :r"),
            {"r": restaurant_id},
        ).mappings().all()


def test_a_restaurant_goes_live_without_a_phone_or_stripe(admin_user, cleanup):
    """The menu is built and the storefront tested before Stripe is connected,
    so activation must not wait for either."""
    from app.api.v1.admin import activate_restaurant

    restaurant = _create(admin_user, cleanup)
    out = activate_restaurant(restaurant.id, admin=admin_user)

    assert out["status"] == "ACTIVE"
    assert out["wallet_domain"] is None  # nothing to register without an account


def test_activation_lists_the_restaurant_on_the_platform_root(admin_user, cleanup):
    from app.api.v1.admin import activate_restaurant, suspend_restaurant

    restaurant = _create(admin_user, cleanup)
    assert _location(restaurant.id) == []

    assert activate_restaurant(restaurant.id, admin=admin_user)["location"] == "created"
    [listed] = _location(restaurant.id)
    assert listed["slug"] == restaurant.slug and listed["status"] == "OPEN"
    assert listed["name"] == "Admin Test Kitchen"

    # Suspending and reactivating reuses the one row rather than adding more.
    suspend_restaurant(restaurant.id, admin=admin_user)
    assert activate_restaurant(restaurant.id, admin=admin_user)["location"] == "unchanged"
    assert len(_location(restaurant.id)) == 1


def test_activation_links_a_location_announced_before_the_restaurant(admin_user, cleanup):
    from app.api.v1.admin import activate_restaurant
    from app.db.session import system_session

    slug = f"adm-{uuid.uuid4().hex[:8]}"
    with system_session() as session:
        session.execute(
            text("INSERT INTO locations (slug, name, city, status) "
                 "VALUES (:s, 'Announced', 'Ardmore', 'COMING_SOON')"),
            {"s": slug},
        )
    restaurant = _create(admin_user, cleanup, slug=slug)

    assert activate_restaurant(restaurant.id, admin=admin_user)["location"] == "linked"
    [listed] = _location(restaurant.id)
    assert listed["name"] == "Announced" and listed["status"] == "OPEN"


def test_a_hidden_location_stays_hidden_on_activation(admin_user, cleanup):
    from app.api.v1.admin import activate_restaurant
    from app.db.session import system_session

    restaurant = _create(admin_user, cleanup)
    with system_session() as session:
        session.execute(
            text("INSERT INTO locations (slug, name, city, status, restaurant_id) "
                 "VALUES (:s, 'Pulled', 'Ardmore', 'HIDDEN', :r)"),
            {"s": restaurant.slug, "r": restaurant.id},
        )

    assert activate_restaurant(restaurant.id, admin=admin_user)["location"] == "unchanged"
    assert _location(restaurant.id)[0]["status"] == "HIDDEN"


def test_the_admin_sees_every_login_and_whether_it_was_used(admin_user, cleanup):
    from app.api.v1.admin import list_restaurant_logins

    restaurant = _create(admin_user, cleanup)
    assert list_restaurant_logins(restaurant.id, admin=admin_user) == []

    email = _email()
    out, _ = _owner(admin_user, restaurant.id, email, "Jr Owner")
    [login] = list_restaurant_logins(restaurant.id, admin=admin_user)
    assert (login.email, login.full_name, login.role, login.status) == (
        email, "Jr Owner", "ADMIN", "ACTIVE"
    )
    assert login.signed_in is False  # still on the temporary password
    assert "password" not in login.model_dump_json()

    _set_own_password(email, "a-password-they-chose")
    [login] = list_restaurant_logins(restaurant.id, admin=admin_user)
    assert login.signed_in is True


def test_logins_for_a_restaurant_that_does_not_exist_is_a_404(admin_user):
    from app.api.v1.admin import list_restaurant_logins

    with pytest.raises(errors.ApiError) as missing:
        list_restaurant_logins(uuid.uuid4(), admin=admin_user)
    assert missing.value.status_code == 404
