"""The team's emails: welcome, joined, role changed, removed, password
reset, refund failed.

Each endpoint is driven for real and the email it queues is then sent with
SendGrid replaced by a recorder, so both which change emails whom and what
it says are checked.
"""

import uuid

import pytest

from app.core import errors
from app.services import staff_emails
from tests.test_admin_restaurants import (  # noqa: F401  (fixtures)
    _create,
    _email,
    _owner,
    _set_own_password,
    _staff_client,
    admin_user,
    cleanup,
)
from tests.test_deliveries import shop  # noqa: F401
from tests.test_notifications import sendgrid  # noqa: F401
from tests.test_staff_invite import _invite_new_cook, _signed_in
from tests.test_staff_invite_resend import _slug

pytestmark = pytest.mark.integration


def _staff_queued(queued_emails):
    return [(args[0], args[1], args[2]) for name, args in queued_emails
            if name == "send_staff_email"]


def _run(queued_emails):
    """Send everything queued, as the worker would."""
    return sum(staff_emails.send(kind, uuid.UUID(rid), args)
               for kind, rid, args in _staff_queued(queued_emails))


def _sent(sendgrid):
    return [
        (call["json"]["personalizations"][0]["to"][0]["email"], call["json"]["subject"],
         call["json"]["content"][0]["value"])
        for call in sendgrid.calls
    ]


def _address(user_id):
    from app.db.session import system_session
    from app.models import User

    with system_session() as session:
        return session.get(User, user_id).email


def test_accepting_welcomes_them_and_tells_whoever_invited_them(
    admin_user, cleanup, queued_emails, sendgrid
):
    restaurant_id, membership_id, _, cook = _invite_new_cook(admin_user, cleanup)
    _set_own_password(cook, "cook password 1234")
    accepted = _signed_in(_slug(restaurant_id), cook).post("/api/v1/restaurant/staff/accept")
    assert accepted.status_code == 200, accepted.text

    assert [kind for kind, _, _ in _staff_queued(queued_emails)] == [
        "staff_welcome", "staff_joined",
    ]
    assert _run(queued_emails) == 2
    (to_cook, welcome, _), (to_owner, joined, joined_body) = _sent(sendgrid)
    assert to_cook == cook
    assert welcome.startswith("Welcome to the team at")
    assert to_owner != cook
    assert joined.endswith("has joined " + welcome.removeprefix("Welcome to the team at "))
    assert "accepted your invitation" in joined_body and "kitchen staff" in joined_body

    # The worker running them again sends nothing more.
    assert _run(queued_emails) == 0


def test_a_role_change_tells_the_person_what_it_was_and_is(shop, queued_emails, sendgrid):
    res = shop.manager.patch(f"/api/v1/restaurant/staff/{shop.kitchen_membership}",
                             json={"role_code": "MANAGER"})
    assert res.status_code == 200, res.text
    assert _run(queued_emails) == 1
    [(to, subject, body)] = _sent(sendgrid)
    assert subject.startswith("Your role at")
    assert "changed your role from kitchen staff to a manager" in body


def test_setting_the_same_role_again_is_not_news(shop, queued_emails):
    shop.manager.patch(f"/api/v1/restaurant/staff/{shop.kitchen_membership}",
                       json={"role_code": "KITCHEN"})
    assert _staff_queued(queued_emails) == []


def test_someone_taken_off_the_team_is_told(shop, queued_emails, sendgrid):
    res = shop.manager.delete(f"/api/v1/restaurant/staff/{shop.driver_membership}")
    assert res.status_code == 200, res.text
    assert _run(queued_emails) == 1
    [(to, subject, body)] = _sent(sendgrid)
    assert to == _address(shop.driver_id)
    assert subject.startswith("You've been removed from the")
    assert "can no longer sign in" in body


def test_a_withdrawn_invitation_sends_nothing(admin_user, cleanup, queued_emails):
    from tests.test_staff_invite_resend import _owner_client

    restaurant_id, membership_id, _, _ = _invite_new_cook(admin_user, cleanup)
    queued_emails.clear()
    assert _owner_client(restaurant_id).delete(
        f"/api/v1/restaurant/staff/{membership_id}"
    ).status_code == 200
    assert _staff_queued(queued_emails) == []


def test_a_password_reset_emails_the_new_password_sealed_in_the_queue(
    shop, queued_emails, sendgrid
):
    res = shop.manager.post(f"/api/v1/restaurant/staff/{shop.kitchen_membership}/reset-password")
    assert res.status_code == 200, res.text
    password = res.json()["temporary_password"]
    # The portal says it went by email as well, not only on screen.
    assert res.json()["email_configured"] is True

    [(kind, _, args)] = _staff_queued(queued_emails)
    assert kind == "staff_password_reset"
    # The broker keeps what it holds on disk; the password is not in it.
    assert password not in str(args)

    assert _run(queued_emails) == 1
    [(_, subject, body)] = _sent(sendgrid)
    assert "was reset" in subject
    assert f"Temporary password: {password}" in body


def test_a_refused_refund_is_emailed_to_admins_and_managers(
    shop, queued_emails, sendgrid, monkeypatch
):
    from app.api.v1 import restaurant as api

    def refuse(payment, reason=None):
        raise errors.ApiError(502, "REFUND_FAILED", "Stripe refused the refund: balance too low.")

    monkeypatch.setattr(api.stripe_service, "refund_order", refuse)
    order_id = shop.order()
    res = shop.manager.post(f"/api/v1/restaurant/orders/{order_id}/cancel",
                            json={"reason": "Kitchen closed early"})
    assert res.status_code == 200 and res.json()["refund_problem"]

    assert [kind for kind, _, _ in _staff_queued(queued_emails)] == ["refund_failed"]
    # The shop's only admin or manager is its owner; the cook and drivers
    # cannot issue refunds and are not told.
    assert _run(queued_emails) == 1
    [(_, subject, body)] = _sent(sendgrid)
    assert "didn't go through" in subject
    assert "its refund of $15.00 didn't go through" in body
    assert "Stripe refused the refund: balance too low." in body
