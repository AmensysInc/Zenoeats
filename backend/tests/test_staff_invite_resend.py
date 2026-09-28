"""Resending a staff invitation, and saying what became of its email.

The case that prompted both: someone invited while email was not set up never
saw their temporary password. Re-inviting left the login alone, so the page
said "sign in with the password they have" and the email said "your manager
will give you a temporary password" -- and neither of them had it. The email
provider then refused the invitation too, and the page still said "we're
emailing them".
"""

from datetime import timedelta
from uuid import UUID

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
from tests.test_staff_invite import _invite_new_cook, _signed_in

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def no_rate_limits(monkeypatch):
    from app.core import ratelimit

    monkeypatch.setattr(ratelimit, "_consume", lambda *a, **k: None)


def _row(restaurant_id, membership_id):
    from app.db.session import tenant_session

    with tenant_session(restaurant_id) as session:
        return session.execute(
            text(
                "SELECT invited_at, invitation_email_status, invitation_email_problem "
                "FROM restaurant_users WHERE id = :m"
            ),
            {"m": membership_id},
        ).mappings().one()


def _slug(restaurant_id):
    from app.db.session import system_session

    with system_session() as session:
        return session.execute(
            text("SELECT slug FROM restaurants WHERE id = :r"), {"r": restaurant_id}
        ).scalar_one()


def _owner_client(restaurant_id):
    """The restaurant's active admin, signed in."""
    from app.db.session import tenant_session

    with tenant_session(restaurant_id) as session:
        owner_id = session.execute(
            text("SELECT user_id FROM restaurant_users "
                 "WHERE role_code = 'ADMIN' AND status = 'ACTIVE' LIMIT 1")
        ).scalar_one()
    client = _staff_client(_slug(restaurant_id))
    client.cookies.set(staff_auth.SESSION_COOKIE, staff_auth.issue_session(owner_id))
    return client


def _can_sign_in(slug, email, password):
    res = _staff_client(slug).post(
        "/api/v1/restaurant/login", json={"email": email, "password": password}
    )
    return res.status_code == 200


# --- resending -----------------------------------------------------------------

def test_resending_to_someone_who_never_signed_in_issues_a_new_password(
    admin_user, cleanup, queued_emails
):
    from app.core import crypto

    restaurant_id, membership_id, first, cook = _invite_new_cook(admin_user, cleanup)
    slug = _slug(restaurant_id)
    before = _row(restaurant_id, membership_id)

    res = _owner_client(restaurant_id).post(
        f"/api/v1/restaurant/staff/{membership_id}/resend-invite"
    )
    assert res.status_code == 200, res.text
    fresh = res.json()["temporary_password"]
    assert fresh and fresh != first

    # The old one is dead, the new one works.
    assert not _can_sign_in(slug, cook, first)
    assert _can_sign_in(slug, cook, fresh)

    # Handed to the worker sealed, for this membership.
    invitations = [args for name, args in queued_emails if name == "send_staff_invitation"]
    last = invitations[-1]
    assert last[1] == membership_id
    assert fresh not in last[2] and crypto.decrypt_field(last[2]) == fresh

    # Re-dated, so it is a new email rather than a duplicate the provider
    # would drop; and shown as queued rather than as the last attempt.
    after = _row(restaurant_id, membership_id)
    assert after["invited_at"] > before["invited_at"]
    assert after["invitation_email_status"] is None


def test_reinviting_someone_who_never_signed_in_issues_a_new_password(admin_user, cleanup):
    """The route actually taken: inviting the same address again."""
    restaurant_id, _, first, cook = _invite_new_cook(admin_user, cleanup)
    again = _owner_client(restaurant_id).post(
        "/api/v1/restaurant/staff", json={"email": cook, "role_code": "KITCHEN"}
    )
    assert again.status_code == 201, again.text
    fresh = again.json()["temporary_password"]
    assert fresh and fresh != first
    assert _can_sign_in(_slug(restaurant_id), cook, fresh)


def test_resending_to_someone_with_their_own_password_sends_only_the_link(
    admin_user, cleanup
):
    restaurant_id, membership_id, _, cook = _invite_new_cook(admin_user, cleanup)
    _set_own_password(cook, "their own password 456")

    res = _owner_client(restaurant_id).post(
        f"/api/v1/restaurant/staff/{membership_id}/resend-invite"
    )
    assert res.status_code == 200, res.text
    assert res.json()["temporary_password"] is None
    assert _can_sign_in(_slug(restaurant_id), cook, "their own password 456")


def test_resending_never_reissues_a_login_on_another_restaurants_team(admin_user, cleanup):
    """The takeover this whole area guards against: another restaurant's
    brand-new owner is exactly a login still on its temporary password."""
    theirs = _create(admin_user, cleanup)
    owner_elsewhere = _email()
    out, _ = _owner(admin_user, theirs.id, owner_elsewhere)
    original = out.temporary_password
    assert original

    ours = _create(admin_user, cleanup)
    our_owner = _email()
    _owner(admin_user, ours.id, our_owner)
    _set_own_password(our_owner, "owner password 123")
    client = _signed_in(ours.slug, our_owner)

    invite = client.post(
        "/api/v1/restaurant/staff", json={"email": owner_elsewhere, "role_code": "KITCHEN"}
    )
    assert invite.status_code == 201, invite.text
    assert invite.json()["temporary_password"] is None

    resend = client.post(f"/api/v1/restaurant/staff/{invite.json()['id']}/resend-invite")
    assert resend.status_code == 200, resend.text
    assert resend.json()["temporary_password"] is None

    # And the same again by re-inviting.
    again = client.post(
        "/api/v1/restaurant/staff", json={"email": owner_elsewhere, "role_code": "KITCHEN"}
    )
    assert again.status_code == 201, again.text
    assert again.json()["temporary_password"] is None

    # Their own restaurant's password still works where it belongs.
    assert _can_sign_in(theirs.slug, owner_elsewhere, original)


def test_an_accepted_member_has_no_invitation_to_resend(admin_user, cleanup):
    restaurant = _create(admin_user, cleanup)
    owner = _email()
    _owner(admin_user, restaurant.id, owner)
    _set_own_password(owner, "owner password 123")
    client = _signed_in(restaurant.slug, owner)
    [mine] = [m for m in client.get("/api/v1/restaurant/staff").json() if m["is_you"]]

    res = client.post(f"/api/v1/restaurant/staff/{mine['id']}/resend-invite")
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "NOT_PENDING"


# --- what became of the email --------------------------------------------------

@pytest.mark.parametrize("status, problem", [
    ("SENT", None),
    ("FAILED", "Not delivered: the email provider says this address is not valid."),
    ("NOT_CONFIGURED", "Not sent: email is not set up on this server."),
])
def test_the_team_list_says_what_became_of_the_invitation_email(
    admin_user, cleanup, monkeypatch, status, problem
):
    from app.services import email, notifications

    monkeypatch.setattr(email, "deliver", lambda message: email.Outcome(status, problem))
    restaurant_id, membership_id, password, _ = _invite_new_cook(admin_user, cleanup)
    notifications.send_staff_invitation(restaurant_id, UUID(membership_id), password)

    rows = _owner_client(restaurant_id).get("/api/v1/restaurant/staff").json()
    [row] = [r for r in rows if r["id"] == membership_id]
    assert row["invitation_email_status"] == status
    assert row["invitation_email_problem"] == problem
    assert row["invitation_email_at"]


def test_a_late_answer_does_not_overwrite_a_newer_resend(admin_user, cleanup):
    """An older email's outcome arriving after a resend was queued must not
    be reported as the new attempt's."""
    from app.services import email, notifications

    restaurant_id, membership_id, _, _ = _invite_new_cook(admin_user, cleanup)
    older = _row(restaurant_id, membership_id)["invited_at"] - timedelta(minutes=5)
    notifications.record_invitation_outcome(
        restaurant_id, UUID(membership_id), email.Outcome("FAILED", "old"), older
    )
    assert _row(restaurant_id, membership_id)["invitation_email_status"] is None


def test_a_provider_refusal_is_explained_without_its_raw_reply():
    """The provider's reply names the platform's own sender address. That
    belongs in the worker log, not in front of a restaurant admin."""
    from app.services import email

    reply = (
        '{"errors":[{"message":"The from address does not match a verified Sender '
        'Identity. Mail cannot be sent until this error is resolved. Visit '
        'https://sendgrid.com/docs/for-developers/sending-email/sender-identity/ to see '
        'the Sender Identity requirements","field":"from","help":null}]}'
    )
    for_admin = email._problem(403, reply)
    assert "sendgrid.com" not in for_admin
    assert "sending address is not verified" in for_admin


@pytest.mark.parametrize("status, reply, expected", [
    (401, '{"errors":[{"message":"The provided authorization grant is invalid, expired, '
          'or revoked","field":null}]}', "did not accept Zenoeats' credentials"),
    (400, '{"errors":[{"message":"Does not contain a valid address.",'
          '"field":"personalizations.0.to.0.email"}]}', "address is not valid"),
    (413, '{"errors":[{"message":"too big"}]}', "refused it (HTTP 413)"),
])
def test_each_refusal_is_worded_for_the_admin(status, reply, expected):
    from app.services import email

    assert expected in email._problem(status, reply)
