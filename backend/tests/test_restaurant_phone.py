"""A restaurant's phone number: how a customer reaches it about an order.

The refunds policy sends a customer with a problem to the restaurant first,
so there has to be a way to reach it. The restaurant sets the number in
Settings, and customers see it on their order page and in every order email.
It is not required to go live: a restaurant is put live to build and test its
storefront before everything is filled in.
"""

import uuid
from types import SimpleNamespace

import pytest

from app.core import errors
from app.services import order_emails, restaurant_profile
from tests.test_admin_restaurants import (  # noqa: F401  (fixtures)
    _create,
    _email,
    _owner,
    _set_own_password,
    _staff_client,
    admin_user,
    cleanup,
)
from tests.test_customer_profile import shop  # noqa: F401  (fixture: an active team)
from tests.test_restaurant_profile import URL, _row, no_rate_limits  # noqa: F401
from tests.test_staff_removal import team  # noqa: F401  (fixture)

integration = pytest.mark.integration


@pytest.mark.parametrize("typed, stored", [
    ("+1 (214) 555-0100", "+1 (214) 555-0100"),
    ("  214   555 0100 ", "214 555 0100"),
    ("", None),
    (None, None),
])
def test_a_number_is_tidied_or_cleared(typed, stored):
    assert restaurant_profile.clean_phone(typed) == stored


@pytest.mark.parametrize("typed", ["call the front desk", "12345", "555-CALL-NOW"])
def test_something_that_is_not_a_number_is_refused(typed):
    with pytest.raises(errors.ApiError):
        restaurant_profile.clean_phone(typed)


@integration
def test_the_restaurant_sets_it_and_customers_see_it(shop):
    res = shop.owner.patch(URL, json={"phone": "+1 214 555 0100"})
    assert res.status_code == 200, res.text
    assert res.json()["phone"] == "+1 214 555 0100"
    assert _row(shop.id, "phone") == "+1 214 555 0100"

    portal = _staff_client(shop.slug).get("/api/v1/portal")
    assert portal.status_code == 200, portal.text
    assert portal.json()["phone"] == "+1 214 555 0100"

    assert shop.owner.patch(URL, json={"phone": "not a phone"}).status_code == 422


@integration
def test_a_restaurant_without_one_can_still_go_live(team, admin_user):
    from app.api.v1.admin import activate_restaurant
    from app.db.session import system_session
    from sqlalchemy import text

    try:
        assert activate_restaurant(team.id, admin=admin_user)["status"] == "ACTIVE"
    finally:
        # The team fixture deletes the restaurant, which the location's
        # RESTRICT key would refuse.
        with system_session() as session:
            session.execute(text("DELETE FROM locations WHERE restaurant_id = :r"),
                            {"r": team.id})


def test_every_order_email_says_how_to_reach_the_restaurant():
    order = SimpleNamespace(id=uuid.uuid4(), order_number=1042, currency="USD",
                            fulfillment_type="PICKUP")
    for kind in order_emails.KINDS:
        _, body_html, body_text = order_emails.compose(
            kind, restaurant_name="Spice House", slug="spicehouse", order=order,
            customer_name="Sam", for_guest=False, refund=500,
            restaurant_phone="+1 (214) 555-0100",
        )
        assert "Call Spice House on +1 (214) 555-0100" in body_text, kind
        assert 'href="tel:+12145550100"' in body_html, kind


def test_no_number_means_no_line():
    order = SimpleNamespace(id=uuid.uuid4(), order_number=1, currency="USD",
                            fulfillment_type="PICKUP")
    _, body_html, body_text = order_emails.compose(
        "order_ready", restaurant_name="Spice House", slug="spicehouse", order=order,
        customer_name=None, for_guest=False,
    )
    assert "Questions about this order" not in body_text + body_html
