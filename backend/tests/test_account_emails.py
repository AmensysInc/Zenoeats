"""A customer's account emails: the welcome, and the account closed.

The welcome comes from the storefront a brand-new account first opens, and
only for a brand-new account. Closing is told once, by whichever path closed
the account -- the customer's own button, or a deletion in Clerk -- with the
address read before closing erased it.
"""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.core import crypto
from app.services import account_emails, clerk_customers
from tests.test_admin_restaurants import (  # noqa: F401  (fixtures)
    _create,
    _email,
    _owner,
    _set_own_password,
    _staff_client,
    admin_user,
    cleanup,
)
from tests.test_close_account import clerk  # noqa: F401  (fixture)
from tests.test_customer_profile import no_rate_limits, shop, signed_in  # noqa: F401
from tests.test_notifications import sendgrid  # noqa: F401
from tests.test_order_delivery_fee import _customer
from tests.test_staff_removal import team  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration


def _account_queued(queued_emails):
    return [args for name, args in queued_emails if name == "send_account_email"]


def _user(user_id):
    from app.db.session import system_session
    from app.models import User

    with system_session() as session:
        user = session.get(User, user_id)
        session.expunge(user)
        return user


def _restaurant_name(restaurant_id):
    from app.db.session import system_session

    with system_session() as session:
        return session.execute(
            text("SELECT name FROM restaurants WHERE id = :r"), {"r": restaurant_id}
        ).scalar_one()


def _age(user_id, days):
    from app.db.session import system_session

    with system_session() as session:
        session.execute(
            text("UPDATE users SET created_at = now() - make_interval(days => :d) WHERE id = :i"),
            {"d": days, "i": user_id},
        )


# ---------------------------------------------------------------- welcome ---

def test_a_new_account_is_welcomed_by_the_storefront_it_opens(
    shop, signed_in, queued_emails, sendgrid
):
    customer_id = _customer()
    signed_in(customer_id)
    client = _staff_client(shop.slug)

    assert client.get("/api/v1/orders/session").status_code == 200
    assert client.get("/api/v1/orders/session").status_code == 200  # another page
    queued = _account_queued(queued_emails)
    assert queued == [("customer_welcome", {"slug": shop.slug, "user_id": str(customer_id)})]

    assert account_emails.send(*queued[0]) is True
    [call] = sendgrid.calls
    assert call["json"]["personalizations"][0]["to"][0]["email"] == _user(customer_id).email
    assert call["json"]["subject"] == f"Welcome to {_restaurant_name(shop.id)}"
    # Once per restaurant, however the queue behaves.
    assert account_emails.send(*queued[0]) is False
    assert len(sendgrid.calls) == 1


def test_an_account_older_than_a_day_is_not_welcomed(shop, signed_in, queued_emails):
    """Nobody who already orders here is greeted as new."""
    customer_id = _customer()
    _age(customer_id, 2)
    signed_in(customer_id)
    _staff_client(shop.slug).get("/api/v1/orders/session")
    assert _account_queued(queued_emails) == []


def test_a_guest_is_not_welcomed(shop, queued_emails):
    client = _staff_client(shop.slug)
    client.post("/api/v1/orders/guest-session", json={"email": "sam@example.com"})
    assert client.get("/api/v1/orders/session").status_code == 200
    assert _account_queued(queued_emails) == []


# ----------------------------------------------------------------- closed ---

def test_closing_tells_them_once_with_the_address_sealed(
    shop, signed_in, clerk, queued_emails, sendgrid
):
    customer_id = _customer()
    address = _user(customer_id).email
    signed_in(customer_id)
    assert _staff_client(shop.slug).delete("/api/v1/customer/account").status_code == 204

    [(kind, args)] = _account_queued(queued_emails)
    assert kind == "account_closed"
    # The broker keeps what it holds on disk, and the address is what
    # closing promised to remove.
    assert address not in str(args)
    assert crypto.decrypt_field(args["sealed_to"]) == address
    assert args["restaurant_id"] == str(shop.id)

    assert account_emails.send(kind, args) is True
    [call] = sendgrid.calls
    assert call["json"]["personalizations"][0]["to"][0]["email"] == address
    body = call["json"]["content"][0]["value"]
    assert f"which you used at {_restaurant_name(shop.id)}" in body
    assert "We've removed your name, phone number, address, email and saved favourites" in body

    # Clerk's user.deleted webhook for the same deletion finds it closed.
    from app.db.session import system_session

    with system_session() as session:
        assert clerk_customers.deactivate(session, _user(customer_id).clerk_user_id) is None


def test_an_account_deleted_in_clerk_is_told_by_the_webhook(queued_emails):
    """No storefront was involved, so the email is Zenoeats', not a
    restaurant's."""
    from app.db.base import utcnow
    from app.db.session import system_session
    from app.models import ClerkEvent
    from app.workers import tasks

    customer_id = _customer()
    user = _user(customer_id)
    with system_session() as session:
        event = ClerkEvent(
            clerk_event_id=f"msg_{uuid.uuid4().hex}", type="user.deleted",
            payload={"data": {"id": user.clerk_user_id}}, status="RECEIVED",
            received_at=utcnow(),
        )
        session.add(event)
        session.flush()
        event_id = str(event.id)

    tasks.process_clerk_event.run(event_id)
    tasks.process_clerk_event.run(event_id)  # redelivered

    [(kind, args)] = _account_queued(queued_emails)
    assert kind == "account_closed"
    assert crypto.decrypt_field(args["sealed_to"]) == user.email
    assert args["restaurant_id"] is None


def test_the_closed_email_without_a_restaurant_reads_on_its_own():
    _, body_html, body_text = account_emails.compose("account_closed", name="Sam",
                                                     restaurant_name=None)
    assert "Your Zenoeats account has been closed." in body_text
    assert "which you used" not in body_text


def test_is_new_is_only_a_real_recent_account():
    from types import SimpleNamespace

    from app.db.base import utcnow

    def user(**kw):
        values = dict(kind="CUSTOMER", created_at=utcnow(), email="sam@example.com",
                      clerk_user_id="user_x")
        values.update(kw)
        return SimpleNamespace(**values)

    assert account_emails.is_new(user())
    assert not account_emails.is_new(user(created_at=utcnow() - timedelta(days=2)))
    assert not account_emails.is_new(user(kind="GUEST"))
    assert not account_emails.is_new(user(email=f"{uuid.uuid4().hex}@pending.local"))
