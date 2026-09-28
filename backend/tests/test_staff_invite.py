"""A restaurant's staff invitation never touches an existing account's password.

Inviting an address used to reissue the password of any staff login that had
not yet replaced its temporary one, and hand it to the restaurant that sent
the invitation. A brand-new owner is exactly such a login -- so the admin of
one restaurant could invite another restaurant's new owner, receive a working
password, and sign in to that restaurant as its ADMIN.
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

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def no_rate_limits(monkeypatch):
    from app.core import ratelimit

    monkeypatch.setattr(ratelimit, "_consume", lambda *a, **k: None)


def _signed_in(slug, email):
    """A staff client for an account that has already chosen its password."""
    from app.db.session import system_session

    with system_session() as session:
        user_id = session.execute(
            text("SELECT id FROM users WHERE email = :e AND kind = 'STAFF'"), {"e": email}
        ).scalar_one()
    client = _staff_client(slug)
    client.cookies.set(staff_auth.SESSION_COOKIE, staff_auth.issue_session(user_id))
    return client


def _password_hash(email):
    from app.db.session import system_session

    with system_session() as session:
        return session.execute(
            text("SELECT password_hash FROM users WHERE email = :e AND kind = 'STAFF'"), {"e": email}
        ).scalar_one()


def test_another_restaurant_cannot_take_over_a_new_owner(admin_user, cleanup):
    attacker = _create(admin_user, cleanup)
    victim = _create(admin_user, cleanup)

    attacker_owner = _email()
    _owner(admin_user, attacker.id, attacker_owner)
    _set_own_password(attacker_owner, "attacker password 123")

    # The victim's owner has been issued a temporary password but not used it.
    victim_owner = _email()
    issued, _ = _owner(admin_user, victim.id, victim_owner)
    before = _password_hash(victim_owner)

    invite = _signed_in(attacker.slug, attacker_owner).post(
        "/api/v1/restaurant/staff", json={"email": victim_owner, "role_code": "CASHIER"}
    )
    assert invite.status_code == 201, invite.text
    assert invite.json()["temporary_password"] is None

    # Nothing about the victim's account changed.
    assert _password_hash(victim_owner) == before
    login = _staff_client(victim.slug).post(
        "/api/v1/restaurant/login",
        json={"email": victim_owner, "password": issued.temporary_password},
    )
    assert login.status_code == 200
    assert login.json()["role_code"] == "ADMIN"


def test_inviting_an_active_member_is_refused_and_changes_nothing(admin_user, cleanup):
    restaurant = _create(admin_user, cleanup)
    owner = _email()
    _owner(admin_user, restaurant.id, owner)
    _set_own_password(owner, "owner password 123")

    # A second admin, still holding a temporary password.
    colleague = _email()
    client = _signed_in(restaurant.slug, owner)
    first = client.post("/api/v1/restaurant/staff", json={"email": colleague, "role_code": "ADMIN"})
    assert first.status_code == 201 and first.json()["temporary_password"]
    from app.db.session import tenant_session

    with tenant_session(restaurant.id) as session:
        session.execute(
            text("UPDATE restaurant_users SET status = 'ACTIVE' WHERE id = :i"),
            {"i": first.json()["id"]},
        )
    before = _password_hash(colleague)

    again = client.post("/api/v1/restaurant/staff", json={"email": colleague, "role_code": "KITCHEN"})
    assert again.status_code == 409
    assert _password_hash(colleague) == before


def test_a_new_address_still_gets_a_temporary_password(admin_user, cleanup):
    restaurant = _create(admin_user, cleanup)
    owner = _email()
    _owner(admin_user, restaurant.id, owner)
    _set_own_password(owner, "owner password 123")

    invite = _signed_in(restaurant.slug, owner).post(
        "/api/v1/restaurant/staff", json={"email": _email(), "role_code": "KITCHEN"}
    )
    assert invite.status_code == 201
    assert invite.json()["temporary_password"]


def test_the_super_admin_can_give_a_passwordless_invitee_a_login(admin_user, cleanup):
    """A placeholder account is never given a password by a restaurant, so the
    super admin reset has to reach it or the invitee could never sign in."""
    from app.api.v1.admin import reset_owner_password
    from app.db.session import system_session
    from app.models import User, UserKind
    from app.schemas.api import CreateOwnerIn

    restaurant = _create(admin_user, cleanup)
    owner = _email()
    _owner(admin_user, restaurant.id, owner)
    _set_own_password(owner, "owner password 123")

    placeholder = _email()
    with system_session() as session:
        session.add(User(kind=UserKind.STAFF.value, email=placeholder))

    invite = _signed_in(restaurant.slug, owner).post(
        "/api/v1/restaurant/staff", json={"email": placeholder, "role_code": "KITCHEN"}
    )
    assert invite.status_code == 201
    assert invite.json()["temporary_password"] is None

    out = reset_owner_password(restaurant.id, CreateOwnerIn(email=placeholder), admin=admin_user)
    assert out.temporary_password
    login = _staff_client(restaurant.slug).post(
        "/api/v1/restaurant/login", json={"email": placeholder, "password": out.temporary_password}
    )
    assert login.status_code == 200
    assert login.json()["membership_status"] == "INVITED"


# --- whether an invitation email is claimed --------------------------------
#
# The portal used to say "We've emailed them the sign-in link" after every
# invitation. With no email provider configured nothing is sent -- the worker
# logs "SENDGRID_API_KEY is not set; not sending" and moves on -- so restaurants
# waited on invitations that never arrived and nothing told them why.

@pytest.mark.parametrize("key, expected", [("", False), ("SG.test_key", True)])
def test_an_invitation_says_whether_an_email_is_on_its_way(
    admin_user, cleanup, monkeypatch, key, expected
):
    from app.config import settings

    monkeypatch.setattr(settings, "SENDGRID_API_KEY", key)
    restaurant = _create(admin_user, cleanup)
    owner = _email()
    _owner(admin_user, restaurant.id, owner)
    _set_own_password(owner, "owner password 123")

    invite = _signed_in(restaurant.slug, owner).post(
        "/api/v1/restaurant/staff", json={"email": _email(), "role_code": "KITCHEN"}
    )
    assert invite.status_code == 201, invite.text
    assert invite.json()["email_configured"] is expected


@pytest.mark.parametrize("key, expected", [("", False), ("SG.test_key", True)])
def test_an_owner_invitation_says_whether_an_email_is_on_its_way(
    admin_user, cleanup, monkeypatch, key, expected
):
    """The super admin's side of the same claim: an existing staff login made
    owner of a second restaurant is invited rather than given a password."""
    from app.config import settings

    monkeypatch.setattr(settings, "SENDGRID_API_KEY", key)
    first, second = _create(admin_user, cleanup), _create(admin_user, cleanup)
    person = _email()
    _owner(admin_user, first.id, person)
    _set_own_password(person, "owner password 123")

    out, _ = _owner(admin_user, second.id, person)
    assert out.status == "INVITED"
    assert out.email_configured is expected


# --- the temporary password in the invitation email -------------------------

def _invite_new_cook(admin_user, cleanup):
    """A restaurant, its owner, and a brand-new cook they have just invited.
    Returns what the worker would be given, and the cook's address."""
    restaurant = _create(admin_user, cleanup)
    owner = _email()
    _owner(admin_user, restaurant.id, owner)
    _set_own_password(owner, "owner password 123")

    cook = _email()
    res = _signed_in(restaurant.slug, owner).post(
        "/api/v1/restaurant/staff", json={"email": cook, "role_code": "KITCHEN"}
    )
    assert res.status_code == 201, res.text
    out = res.json()
    assert out["temporary_password"]
    return restaurant.id, out["id"], out["temporary_password"], cook


@pytest.fixture
def outbox(monkeypatch):
    """Every email handed to the provider, without sending any."""
    from app.services import email

    sent = []

    def deliver(message):
        sent.append(message)
        return email.Outcome("SENT")

    monkeypatch.setattr(email, "deliver", deliver)
    return sent


def test_a_new_member_is_emailed_their_temporary_password(admin_user, cleanup, outbox):
    from uuid import UUID

    from app.services import notifications

    restaurant_id, membership_id, password, cook = _invite_new_cook(admin_user, cleanup)
    assert notifications.send_staff_invitation(restaurant_id, UUID(membership_id), password)

    [message] = outbox
    assert message.to == cook
    assert password in message.text and password in message.html


def test_a_password_already_replaced_is_never_emailed(admin_user, cleanup, outbox):
    """A retry can run long after the invitation. By then the person may have
    signed in and chosen their own; the old one no longer works, and an inbox
    is no place to leave it."""
    from uuid import UUID

    from app.services import notifications

    restaurant_id, membership_id, password, cook = _invite_new_cook(admin_user, cleanup)
    _set_own_password(cook, "their own password 456")

    assert notifications.send_staff_invitation(restaurant_id, UUID(membership_id), password)
    [message] = outbox
    assert password not in message.text and password not in message.html
    assert "password you already use" in message.text


def test_the_worker_unseals_the_password_before_composing(admin_user, cleanup, outbox):
    """The whole hand-off: sealed on the way into the queue, opened by the
    task, and in the email -- as the real task runs it, minus the broker."""
    from app.core import crypto
    from app.workers import tasks

    restaurant_id, membership_id, password, _ = _invite_new_cook(admin_user, cleanup)
    tasks.send_staff_invitation.run(
        str(restaurant_id), membership_id, crypto.encrypt_field(password)
    )
    [message] = outbox
    assert password in message.text
