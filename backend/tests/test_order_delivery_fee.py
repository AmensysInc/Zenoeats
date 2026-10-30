"""An order remembers what its delivery cost, and why.

Two facts survive on the order: the fee, because it is money the customer paid
and a receipt has to explain it, and the distance, because it is what chose the
fee. Neither is a pointer at the ring that applied -- a restaurant replaces its
rings as a set whenever it edits them, so the rule is gone by the next save
while "3.2 miles, $4" stays true.

The rest of this file is about the pairing that must not come apart: a fee
without a delivery. Nothing in the application would create one, which is
exactly why it is checked in three places -- the service names it, and the
database refuses it even if a future refactor stops asking.
"""

import pytest
from sqlalchemy import text

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


@pytest.fixture
def kitchen(team):
    """A restaurant that can take an order, and one item on its menu."""
    from app.db.session import system_session, tenant_session

    with system_session() as session:
        session.execute(
            text("UPDATE restaurants SET status = 'ACTIVE' WHERE id = :r"), {"r": team.id}
        )
    with tenant_session(team.id) as session:
        item_id = _an_item(session, team.id)
    team.item_id = item_id
    return team


def _an_item(session, restaurant_id):
    from app.models import Item, ItemType, Meal, MealItem
    from sqlalchemy import select

    item_type = session.execute(select(ItemType).limit(1)).scalars().first()
    item = Item(
        restaurant_id=restaurant_id, item_type_id=item_type.id, name="Smash Burger",
        base_price_minor=1200, is_available=True,
    )
    session.add(item)
    session.flush()
    meal = Meal(restaurant_id=restaurant_id, name="All day", sort_order=0)
    session.add(meal)
    session.flush()
    session.add(MealItem(restaurant_id=restaurant_id, meal_id=meal.id, item_id=item.id))
    session.flush()
    return item.id


def _customer():
    from app.db.session import system_session

    with system_session() as session:
        return session.execute(
            text(
                "INSERT INTO users (id, kind, email, is_platform_admin, "
                "is_active, must_change_password, created_at, updated_at) "
                "VALUES (gen_random_uuid(), 'CUSTOMER', :e, false, true, false, "
                "now(), now()) RETURNING id"
            ),
            {"e": _email()},
        ).scalar_one()


def _order(kitchen, *, fee=0, delivery=None):
    """Price a one-item cart and create the pending order it describes."""
    from app.db.session import tenant_session
    from app.models import Restaurant
    from app.services.orders import create_pending_order
    from app.services.pricing import price_cart

    customer_id = _customer()
    with tenant_session(kitchen.id) as session:
        restaurant = session.get(Restaurant, kitchen.id)
        cart = price_cart(
            session, restaurant,
            [{"menu_item_id": kitchen.item_id, "quantity": 1, "note": None, "modifiers": []}],
            delivery_fee_minor=fee,
        )
        order = create_pending_order(
            session, restaurant=restaurant, customer_user_id=customer_id,
            cart=cart, customer_note=None, delivery=delivery,
        )
        session.flush()
        return order.id


def _row(kitchen, order_id, columns):
    """Read as the tenant, not as the system role.

    zenoeats_system holds column-level grants on orders -- enough for platform
    counts and nothing more -- so asking it for a delivery fee is refused by
    the database rather than answered.
    """
    from app.db.session import tenant_session

    with tenant_session(kitchen.id) as session:
        return session.execute(
            text(f"SELECT {columns} FROM orders WHERE id = :o"), {"o": order_id}
        ).mappings().one()


# ----------------------------------------------------------- a collection ---

def test_a_collection_carries_no_fee_and_no_distance(kitchen):
    order_id = _order(kitchen)
    row = _row(kitchen, order_id, "fulfillment_type, delivery_fee_minor, delivery_miles, delivery_address")

    assert row["fulfillment_type"] == "PICKUP"
    assert row["delivery_fee_minor"] == 0
    assert row["delivery_miles"] is None
    assert row["delivery_address"] is None


# ------------------------------------------------------------ a delivery ---

def test_a_delivery_keeps_the_fee_the_distance_and_the_address(kitchen):
    from app.services.orders import DeliveryDetails

    order_id = _order(
        kitchen, fee=400,
        delivery=DeliveryDetails(address="12 Oak Street, Chicago IL", miles=3.2),
    )
    row = _row(
        kitchen, order_id,
        "fulfillment_type, delivery_fee_minor, delivery_miles, delivery_address, total_minor",
    )

    assert row["fulfillment_type"] == "DELIVERY"
    assert row["delivery_fee_minor"] == 400
    assert row["delivery_miles"] == pytest.approx(3.2)
    assert row["delivery_address"] == "12 Oak Street, Chicago IL"


def test_the_fee_is_inside_the_total_the_customer_pays(kitchen):
    """It is charged on top of the food, so the total has to include it --
    otherwise the card is charged one number and the receipt shows another."""
    from app.services.orders import DeliveryDetails

    collection = _row(kitchen, _order(kitchen), "total_minor")["total_minor"]
    delivered = _row(
        kitchen,
        _order(kitchen, fee=400,
               delivery=DeliveryDetails(address="12 Oak Street", miles=1.1)),
        "total_minor, subtotal_minor, delivery_fee_minor",
    )

    assert delivered["total_minor"] == collection + 400
    # ...and stays out of the subtotal, which is the food alone.
    assert delivered["subtotal_minor"] + 400 == delivered["total_minor"] - _tax(kitchen)


def _tax(kitchen):
    """The tax on one burger, so the arithmetic above can be checked exactly."""
    from app.core.money import apply_rate_bps

    return apply_rate_bps(1200, 825)


# --------------------------------------------- the pairing that must hold ---

def test_a_fee_without_a_delivery_is_refused_by_name(kitchen):
    """Nothing would do this today. It is checked because the mistake it
    guards against is a future refactor, not today's code."""
    from app.core.errors import ApiError

    with pytest.raises(ApiError) as raised:
        _order(kitchen, fee=400, delivery=None)
    assert "delivery address" in raised.value.detail["message"]


def test_the_database_refuses_it_too(kitchen):
    """Belt and braces, deliberately: the service check is one refactor from
    being skipped, and this one is not."""
    from sqlalchemy.exc import IntegrityError

    from app.db.session import tenant_session

    order_id = _order(kitchen)
    with pytest.raises(IntegrityError):
        with tenant_session(kitchen.id) as session:
            session.execute(
                text("UPDATE orders SET delivery_fee_minor = 400 WHERE id = :o"),
                {"o": order_id},
            )


def test_a_negative_fee_is_refused_by_the_database(kitchen):
    from sqlalchemy.exc import IntegrityError

    from app.db.session import tenant_session
    from app.services.orders import DeliveryDetails

    order_id = _order(
        kitchen, fee=400, delivery=DeliveryDetails(address="12 Oak Street", miles=1.1)
    )
    with pytest.raises(IntegrityError):
        with tenant_session(kitchen.id) as session:
            session.execute(
                text("UPDATE orders SET delivery_fee_minor = -1 WHERE id = :o"),
                {"o": order_id},
            )
