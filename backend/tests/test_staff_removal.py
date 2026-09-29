"""A restaurant can never be left without an admin by its own staff page.

Removing a team member had no guard: an admin could remove themselves while
the only admin, and two admins could remove each other, leaving a restaurant
nobody could administer and only platform support could recover.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor

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


@pytest.fixture
def team(admin_user, cleanup):
    from app.db.base import utcnow
    from app.db.session import system_session, tenant_session
    from app.models import RestaurantUser

    restaurant = _create(admin_user, cleanup)
    owner_email = _email()
    owner, _ = _owner(admin_user, restaurant.id, owner_email)
    _set_own_password(owner_email, "owner password 123")

    class Team:
        id = restaurant.id

        @staticmethod
        def client(user_id):
            c = _staff_client(restaurant.slug)
            c.cookies.set(staff_auth.SESSION_COOKIE, staff_auth.issue_session(user_id))
            return c

        @staticmethod
        def member(role, status="ACTIVE"):
            """A new person on the team; returns (user_id, membership_id)."""
            with system_session() as session:
                user_id = session.execute(
                    text(
                        "INSERT INTO users (id, kind, email, password_hash, is_platform_admin, "
                        "is_active, must_change_password, created_at, updated_at) "
                        "VALUES (gen_random_uuid(), 'STAFF', :e, :h, false, true, false, "
                        "now(), now()) RETURNING id"
                    ),
                    {"e": _email(), "h": staff_auth.hash_password("staff password 12")},
                ).scalar_one()
            with tenant_session(restaurant.id) as session:
                row = RestaurantUser(
                    restaurant_id=restaurant.id, user_id=user_id, role_code=role, status=status,
                    invited_at=utcnow(), accepted_at=utcnow() if status == "ACTIVE" else None,
                )
                session.add(row)
                session.flush()
                return user_id, row.id

        @staticmethod
        def status(membership_id):
            with tenant_session(restaurant.id) as session:
                return session.execute(
                    text("SELECT status FROM restaurant_users WHERE id = :i"), {"i": membership_id}
                ).scalar_one()

        @staticmethod
        def active_admins():
            with tenant_session(restaurant.id) as session:
                return session.execute(
                    text(
                        "SELECT count(*) FROM restaurant_users "
                        "WHERE role_code = 'ADMIN' AND status = 'ACTIVE'"
                    )
                ).scalar_one()

    Team.owner_id = owner.user_id
    Team.owner = Team.client(owner.user_id)
    with tenant_session(restaurant.id) as session:
        Team.owner_membership = session.execute(
            text("SELECT id FROM restaurant_users WHERE user_id = :u"), {"u": owner.user_id}
        ).scalar_one()
    return Team


def _remove(client, membership_id):
    return client.delete(f"/api/v1/restaurant/staff/{membership_id}")


def test_an_admin_cannot_remove_themselves(team):
    res = _remove(team.owner, team.owner_membership)
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "CANNOT_REMOVE_SELF"
    assert team.status(team.owner_membership) == "ACTIVE"

    # Not even with a second admin to take over: another admin does it.
    _, second = team.member("ADMIN")
    assert _remove(team.owner, team.owner_membership).status_code == 409
    assert team.status(second) == "ACTIVE"


def test_one_admin_can_remove_another_and_access_ends_at_once(team):
    colleague_id, colleague = team.member("ADMIN")
    colleague_client = team.client(colleague_id)
    assert colleague_client.get("/api/v1/restaurant/staff").status_code == 200

    res = _remove(team.owner, colleague)
    assert res.status_code == 200, res.text
    assert team.status(colleague) == "REVOKED"
    assert colleague_client.get("/api/v1/restaurant/staff").status_code == 403


def test_the_last_admin_cannot_be_removed(team):
    """Unreachable through the API while self-removal is refused, except by a
    race -- so the guard is called directly, as a manager's membership would
    reach it if the role check ever widened."""
    from fastapi import BackgroundTasks

    from app.api.v1.restaurant import revoke_staff
    from app.core import errors
    from app.db.session import tenant_session
    from app.models import Restaurant, RestaurantUser

    manager_user, manager = team.member("MANAGER")
    with pytest.raises(errors.ApiError) as caught:
        with tenant_session(team.id) as session:
            revoke_staff(
                team.owner_membership,
                BackgroundTasks(),
                restaurant=session.get(Restaurant, team.id),
                db=session,
                membership=session.get(RestaurantUser, manager),
            )
    assert caught.value.code == "LAST_ADMIN"
    assert team.active_admins() == 1


def test_two_admins_removing_each_other_at_once_leave_one(team):
    """Without the row lock each request counts two admins, and both go
    through. Raced a few times, since a single race can miss."""
    for _ in range(5):
        a_user, a = team.member("ADMIN")
        b_user, b = team.member("ADMIN")
        # Only these two may count, so the last-admin rule is what is tested.
        from app.db.session import tenant_session

        with tenant_session(team.id) as session:
            session.execute(
                text("UPDATE restaurant_users SET status = 'REVOKED', revoked_at = now() "
                     "WHERE role_code = 'ADMIN' AND id NOT IN (:a, :b)"),
                {"a": a, "b": b},
            )

        a_client, b_client = team.client(a_user), team.client(b_user)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(_remove, a_client, b)
            second = pool.submit(_remove, b_client, a)
            codes = sorted([first.result().status_code, second.result().status_code])

        assert codes.count(200) == 1, codes
        assert team.active_admins() == 1


def test_an_invitation_can_be_withdrawn_and_the_list_marks_you(team):
    _, invited = team.member("KITCHEN", status="INVITED")

    rows = team.owner.get("/api/v1/restaurant/staff").json()
    assert [r["is_you"] for r in rows if r["id"] == str(team.owner_membership)] == [True]
    assert all(not r["is_you"] for r in rows if r["id"] != str(team.owner_membership))

    assert _remove(team.owner, invited).status_code == 200
    assert all(r["id"] != str(invited) for r in team.owner.get("/api/v1/restaurant/staff").json())

    # Already gone, or never existed.
    assert _remove(team.owner, invited).status_code == 422
    assert _remove(team.owner, uuid.uuid4()).status_code == 422
