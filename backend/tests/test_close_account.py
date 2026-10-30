"""Closing a customer account from the customer's own page.

legal/data-deletion.html is the specification: the sign-in goes, and with it
the name, phone number, address, email and favourites. Orders already placed
stay, carrying the details they were placed with, because they are the
restaurant's record of a sale.
"""

import pytest
from sqlalchemy import text

from app.core import customer_auth
from tests.test_customer_profile import (  # noqa: F401  (fixtures)
    CONTACT,
    _order,
    no_rate_limits,
    shop,
    signed_in,
)
from tests.test_admin_restaurants import (  # noqa: F401  (fixtures)
    _create,
    _email,
    _owner,
    _set_own_password,
    _staff_client,
    admin_user,
    cleanup,
)
from tests.test_order_delivery_fee import _customer
from tests.test_staff_removal import team  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration


def _user(user_id):
    from app.db.session import system_session

    with system_session() as session:
        return session.execute(
            text(
                "SELECT email, full_name, phone, address, is_active, deleted_at "
                "FROM users WHERE id = :i"
            ),
            {"i": user_id},
        ).mappings().one()


def _favourites(shop_id, user_id):
    from app.db.session import tenant_session

    with tenant_session(shop_id) as session:
        return session.execute(
            text("SELECT count(*) FROM customer_favourites WHERE user_id = :u"), {"u": user_id}
        ).scalar_one()


def _close(shop, customer_id, signed_in):
    signed_in(customer_id)
    client = _staff_client(shop.slug)
    return client.delete("/api/v1/customer/account")


def test_closing_takes_the_details_off_and_keeps_the_orders(shop, signed_in):
    customer_id = _customer()
    signed_in(customer_id)
    client = _staff_client(shop.slug)
    assert client.put("/api/v1/customer/profile", json=CONTACT).status_code == 200
    assert client.put(f"/api/v1/customer/favourites/{shop.item_id}").status_code in (200, 201, 204)
    order_id = _order(shop, customer_id)
    before = _user(customer_id)

    assert _close(shop, customer_id, signed_in).status_code == 204

    after = _user(customer_id)
    # Gone: everything the page promises.
    assert after["full_name"] is None
    assert after["phone"] is None
    assert after["address"] is None
    assert after["email"] != before["email"]
    assert after["is_active"] is False and after["deleted_at"] is not None
    assert _favourites(shop.id, customer_id) == 0

    # Kept: the sale.
    from app.db.session import tenant_session

    with tenant_session(shop.id) as session:
        assert session.execute(
            text("SELECT count(*) FROM orders WHERE id = :o"), {"o": order_id}
        ).scalar_one() == 1


def test_a_closed_account_cannot_be_signed_in_to_again(shop, signed_in):
    """The row survives for the orders that point at it; the account does not
    survive as something anyone can act through.

    Asserted through a real cookie rather than the fixture above, which replaces
    the dependency that refuses a closed account.
    """
    customer_id = _customer()
    assert _close(shop, customer_id, signed_in).status_code == 204

    from app.main import app
    from app.api.deps import get_current_user

    app.dependency_overrides.pop(get_current_user, None)
    client = _staff_client(shop.slug)
    client.cookies.set(customer_auth.SESSION_COOKIE, customer_auth.issue_session(customer_id))
    assert client.get("/api/v1/orders/session").status_code == 401


def test_closing_twice_is_the_same_as_closing_once(shop, signed_in):
    """A double press is the same operation twice, and the second is harmless."""
    from app.db.session import system_session
    from app.services import customer_records

    customer_id = _customer()
    signed_in(customer_id)
    _staff_client(shop.slug).put("/api/v1/customer/profile", json=CONTACT)
    assert _close(shop, customer_id, signed_in).status_code == 204
    after_first = _user(customer_id)

    with system_session() as session:
        from app.models import User

        customer_records.close_account(session, session.get(User, customer_id))

    assert dict(_user(customer_id)) == dict(after_first)


def test_a_guest_has_no_account_to_close(shop):
    """A guest session ends on its own, and the record behind it is cleared
    out with the rest."""
    client = _staff_client(shop.slug)
    client.post("/api/v1/orders/guest-session", json={"email": "sam@example.com"})
    res = client.delete("/api/v1/customer/account")
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "ACCOUNT_REQUIRED"
