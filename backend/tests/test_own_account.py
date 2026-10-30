"""Staff can fix their own name and change the address they sign in with.

Neither was possible: a name typed wrong at invitation stayed wrong on every
ticket, and an address could only be changed by the platform.

The email half is the dangerous half. Sign-in finds a staff account by address
alone and expects exactly one row, so two accounts sharing an address make
both of them unreachable -- typing a colleague's address into your own
settings would have locked them out, and locked you out with them. That is
what the refusal here is for, and the test that matters most in this file.
"""

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
from tests.test_staff_removal import team  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration

ME = "/api/v1/restaurant/me"
CHANGE_EMAIL = "/api/v1/restaurant/change-email"
# What tests/test_staff_removal.py gives every member it makes.
MEMBER_PASSWORD = "staff password 12"


@pytest.fixture(autouse=True)
def no_rate_limits(monkeypatch):
    from app.core import ratelimit

    monkeypatch.setattr(ratelimit, "_consume", lambda *a, **k: None)


def _user(user_id, column):
    from app.db.session import system_session

    with system_session() as session:
        return session.execute(
            text(f"SELECT {column} FROM users WHERE id = :i"), {"i": user_id}
        ).scalar_one()


# ------------------------------------------------------------ your name ---

@pytest.mark.parametrize("role", ["ADMIN", "MANAGER", "KITCHEN", "CASHIER", "DRIVER"])
def test_anyone_with_a_login_can_fix_their_own_name(team, role):
    """Including a driver. A name is not a privilege."""
    user_id, _ = team.member(role)
    res = team.client(user_id).patch(ME, json={"full_name": "Dana Okonkwo"})

    assert res.status_code == 200, res.text
    assert res.json()["full_name"] == "Dana Okonkwo"
    assert _user(user_id, "full_name") == "Dana Okonkwo"


def test_a_name_can_be_removed_again(team):
    user_id, _ = team.member("KITCHEN")
    client = team.client(user_id)

    client.patch(ME, json={"full_name": "Temporary"})
    assert client.patch(ME, json={"full_name": "   "}).json()["full_name"] is None
    assert _user(user_id, "full_name") is None


def test_the_answer_carries_the_rest_of_the_session(team):
    """The portal paints its header from this, so a rename must not blank it."""
    user_id, _ = team.member("MANAGER")
    body = team.client(user_id).patch(ME, json={"full_name": "Sam"}).json()

    assert body["role_code"] == "MANAGER"
    assert body["restaurant_name"]
    assert body["membership_status"] == "ACTIVE"


def test_an_empty_patch_is_refused(team):
    user_id, _ = team.member("KITCHEN")
    assert team.client(user_id).patch(ME, json={}).status_code == 422


def test_a_name_is_not_a_way_to_reach_other_fields(team):
    user_id, _ = team.member("KITCHEN")
    client = team.client(user_id)

    for forbidden in ({"email": "new@example.com"}, {"role_code": "ADMIN"},
                      {"must_change_password": False}):
        assert client.patch(ME, json=forbidden).status_code == 422


def test_a_stranger_cannot_change_anyones_name(team):
    from tests.test_admin_restaurants import _staff_client

    assert _staff_client("nobody").patch(ME, json={"full_name": "x"}).status_code in (401, 404)


# ----------------------------------------------------- your sign-in address ---

def test_changing_the_address_is_what_you_then_sign_in_with(team):
    user_id, _ = team.member("CASHIER")
    client = team.client(user_id)
    fresh = _email()

    res = client.post(CHANGE_EMAIL, json={"email": fresh, "current_password": MEMBER_PASSWORD})
    assert res.status_code == 204, res.text
    assert _user(user_id, "email") == fresh


def test_the_address_is_tidied_before_it_is_stored(team):
    user_id, _ = team.member("CASHIER")
    fresh = _email()

    team.client(user_id).post(
        CHANGE_EMAIL,
        json={"email": f"  {fresh.upper()} ", "current_password": MEMBER_PASSWORD},
    )
    assert _user(user_id, "email") == fresh


def test_it_takes_the_current_password_not_just_a_session(team):
    """A tablet left signed in behind a counter should not be a way to move
    somebody's login to an address the next person controls."""
    user_id, _ = team.member("CASHIER")
    before = _user(user_id, "email")

    res = team.client(user_id).post(
        CHANGE_EMAIL, json={"email": _email(), "current_password": "not the password"}
    )
    assert res.status_code == 401
    assert _user(user_id, "email") == before


def test_an_address_another_staff_login_holds_is_refused(team):
    """The one that matters: sign-in expects exactly one account per address,
    so allowing this would take both accounts off the air."""
    mine, _ = team.member("CASHIER")
    colleague, _ = team.member("KITCHEN")
    theirs = _user(colleague, "email")
    before = _user(mine, "email")

    res = team.client(mine).post(
        CHANGE_EMAIL, json={"email": theirs, "current_password": MEMBER_PASSWORD}
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "EMAIL_IN_USE"
    assert _user(mine, "email") == before

    # Both logins still resolve to exactly one row, which is what sign-in needs.
    assert _staff_rows(theirs) == 1
    assert _staff_rows(before) == 1


def test_a_login_at_a_restaurant_you_cannot_see_is_refused_the_same_way(team, admin_user, cleanup):
    """And says no more than that. Which addresses have staff accounts
    elsewhere is not this caller's to discover."""
    other = _create(admin_user, cleanup)
    other_email = _email()
    _owner(admin_user, other.id, other_email)

    mine, _ = team.member("CASHIER")
    res = team.client(mine).post(
        CHANGE_EMAIL, json={"email": other_email, "current_password": MEMBER_PASSWORD}
    )
    assert res.status_code == 409
    assert res.json()["detail"]["message"] == "That address already has a staff login."


def test_your_own_address_again_is_not_an_error(team):
    """Saving a form without touching the field should not read as a failure."""
    user_id, _ = team.member("CASHIER")
    same = _user(user_id, "email")

    res = team.client(user_id).post(
        CHANGE_EMAIL, json={"email": same.upper(), "current_password": MEMBER_PASSWORD}
    )
    assert res.status_code == 204
    assert _user(user_id, "email") == same


def test_something_that_is_not_an_address_is_refused(team):
    user_id, _ = team.member("CASHIER")
    res = team.client(user_id).post(
        CHANGE_EMAIL, json={"email": "not an address", "current_password": MEMBER_PASSWORD}
    )
    assert res.status_code == 422


def test_a_customer_with_the_same_address_is_not_in_the_way(team):
    """Customers are a separate population. Someone who orders lunch here can
    also work here -- which is already true at invitation time."""
    from app.db.session import system_session

    user_id, _ = team.member("CASHIER")
    shared = _email()
    with system_session() as session:
        session.execute(
            text(
                "INSERT INTO users (id, kind, email, is_platform_admin, "
                "is_active, must_change_password, created_at, updated_at) "
                "VALUES (gen_random_uuid(), 'CUSTOMER', :e, false, true, false, now(), now())"
            ),
            {"e": shared},
        )

    res = team.client(user_id).post(
        CHANGE_EMAIL, json={"email": shared, "current_password": MEMBER_PASSWORD}
    )
    assert res.status_code == 204
    assert _user(user_id, "email") == shared


def test_the_session_survives_the_change(team):
    """You proved the password a moment ago; you are still the same person."""
    user_id, _ = team.member("MANAGER")
    client = team.client(user_id)

    client.post(CHANGE_EMAIL, json={"email": _email(), "current_password": MEMBER_PASSWORD})
    assert client.get(ME).status_code == 200


def test_the_change_is_written_down_without_spelling_the_address_out(team):
    """An audit trail of who changed their login, that is not also a list of
    everybody's addresses."""
    from app.db.session import system_session

    user_id, _ = team.member("CASHIER")
    before = _user(user_id, "email")
    after = _email()
    team.client(user_id).post(CHANGE_EMAIL, json={"email": after, "current_password": MEMBER_PASSWORD})

    with system_session() as session:
        scope = session.execute(
            text(
                "SELECT scope FROM platform_audit_logs WHERE action = 'STAFF_CHANGE_EMAIL' "
                "AND actor_user_id = :u ORDER BY created_at DESC LIMIT 1"
            ),
            {"u": user_id},
        ).scalar_one()

    assert scope["user_id"] == str(user_id)
    assert before not in scope["from"] and after not in scope["to"]
    assert scope["from"].startswith(before[0]) and "***@" in scope["from"]


# --------------------------------------------- and the database agrees ---
#
# Everything above is an application rule, and an application rule is one
# refactor away from not being a rule. Migration 0023 makes it structural.


def test_the_database_refuses_a_second_staff_login_for_one_address(team):
    from sqlalchemy.exc import IntegrityError

    from app.db.session import system_session

    user_id, _ = team.member("CASHIER")
    taken = _user(user_id, "email")

    with pytest.raises(IntegrityError):
        with system_session() as session:
            session.execute(
                text(
                    "INSERT INTO users (id, kind, email, password_hash, is_platform_admin, "
                    "is_active, must_change_password, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), 'STAFF', :e, 'x', false, true, false, "
                    "now(), now())"
                ),
                {"e": taken},
            )


def test_the_same_address_in_a_different_case_is_the_same_address(team):
    """The index is on lower(email), so a caller that forgets to normalise is
    refused rather than quietly making sign-in ambiguous."""
    from sqlalchemy.exc import IntegrityError

    from app.db.session import system_session

    user_id, _ = team.member("CASHIER")
    taken = _user(user_id, "email")

    with pytest.raises(IntegrityError):
        with system_session() as session:
            session.execute(
                text(
                    "INSERT INTO users (id, kind, email, password_hash, is_platform_admin, "
                    "is_active, must_change_password, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), 'STAFF', :e, 'x', false, true, false, "
                    "now(), now())"
                ),
                {"e": taken.upper()},
            )


def test_a_customer_may_still_hold_the_same_address(team):
    """The index is confined to staff. Customers are a separate population and
    an address that orders lunch here can also work here."""
    from app.db.session import system_session

    user_id, _ = team.member("CASHIER")
    shared = _user(user_id, "email")

    with system_session() as session:
        session.execute(
            text(
                "INSERT INTO users (id, kind, email, is_platform_admin, "
                "is_active, must_change_password, created_at, updated_at) "
                "VALUES (gen_random_uuid(), 'CUSTOMER', :e, false, true, false, "
                "now(), now())"
            ),
            {"e": shared},
        )


def _staff_rows(email) -> int:
    from app.db.session import system_session

    with system_session() as session:
        return session.execute(
            text("SELECT count(*) FROM users WHERE email = :e AND kind = 'STAFF'"),
            {"e": email},
        ).scalar_one()
