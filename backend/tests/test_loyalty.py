"""The loyalty engine's rules, against a real database.

Earning is the part that must not be got wrong twice, so most of this is
about what happens when the same order is credited again, when two orders
land at once, and when money goes back. The arithmetic is simple; the
guarantees around it are the point.
"""

import uuid

import pytest
from sqlalchemy import text

from app.db.session import system_session, tenant_session
from app.models import (
    CustomerLoyalty, LoyaltyProgram, LoyaltyProgramItem, LoyaltyReward,
    LoyaltyTransaction, Order, OrderStatus, PurchaseType, RewardStatus,
)
from app.services import loyalty


# --------------------------------------------------------------- fixtures


def _make_restaurant(s, label):
    """A throwaway restaurant, rather than one a seed script may or may not
    have made: CI starts from an empty database. Removed by _drop_restaurant."""
    from app.models import Restaurant, RestaurantStatus

    restaurant = Restaurant(
        slug=f"{label}-{uuid.uuid4().hex[:8]}", name=f"{label.title()} Test",
        status=RestaurantStatus.ACTIVE.value, timezone="UTC", currency="USD",
    )
    s.add(restaurant)
    s.flush()
    return restaurant.id


def _make_dishes(db, rid, count):
    """A heading and `count` dishes under it, as (item_id, item_type_id)."""
    type_id = uuid.uuid4()
    db.execute(
        text("insert into item_types (id, restaurant_id, name, sort_order) "
             "values (:t, :r, 'Food', 0)"),
        {"t": str(type_id), "r": str(rid)},
    )
    dishes = []
    for n in range(count):
        item_id = uuid.uuid4()
        db.execute(
            text("insert into menu_items (id, restaurant_id, name, item_type_id, "
                 "base_price_minor, currency, is_available, tax_exempt, sort_order) "
                 "values (:i, :r, :n, :t, 1000, 'USD', true, false, :o)"),
            {"i": str(item_id), "r": str(rid), "n": f"Dish {n}", "t": str(type_id), "o": n},
        )
        dishes.append((item_id, type_id))
    return dishes


def _drop_restaurant(db, rid):
    for table in ("menu_items", "item_types"):
        db.execute(text(f"delete from {table} where restaurant_id = :r"), {"r": str(rid)})
    db.execute(text("delete from restaurants where id = :r"), {"r": str(rid)})


@pytest.fixture
def shop():
    """A restaurant, a customer, and two dishes to build programs from.

    The menu is read through a tenant session and the customer through the
    system one, because that is how the grants are drawn: the system role
    exists for cross-tenant admin and has no business reading a menu.
    Everything tenant-owned is cleaned up the same way it was made.
    """
    with system_session() as s:
        rid = _make_restaurant(s, "loyalty")
        # One well-known customer rather than a fresh one per test. The
        # system role may insert a users row and may not delete one, so a
        # new id each run would leave a trail; a fixed id is created once
        # and reused for ever after. Everything tenant-owned that hangs off
        # it is still cleaned up below.
        uid = uuid.UUID("44444444-4444-4444-4444-444444444444")
        s.execute(
            text(
                "insert into users (id, kind, email, full_name, is_active) "
                "values (:id,'CUSTOMER','loyalty-tester@example.test',"
                "'Loyalty Tester',true) on conflict (id) do nothing"
            ),
            {"id": str(uid)},
        )
        s.commit()

    with tenant_session(rid) as db:
        dishes = _make_dishes(db, rid, 2)
        db.commit()

    yield {
        "restaurant_id": rid,
        "user_id": uid,
        "item_id": dishes[0][0],
        "item_type_id": dishes[0][1],
        "other_item_id": dishes[1][0],
    }

    with tenant_session(rid) as db:
        for table in ("loyalty_rewards", "loyalty_transactions", "customer_loyalty"):
            db.execute(
                text(f"delete from {table} where customer_user_id = :u"), {"u": str(uid)}
            )
        db.execute(
            text("delete from order_items where order_id in "
                 "(select id from orders where customer_user_id = :u)"),
            {"u": str(uid)},
        )
        db.execute(text("delete from orders where customer_user_id = :u"), {"u": str(uid)})
        db.execute(
            text("delete from loyalty_program_items where program_id in "
                 "(select id from loyalty_programs where name like 'TEST %')")
        )
        db.execute(text("delete from loyalty_programs where name like 'TEST %'"))
        _drop_restaurant(db, rid)
        db.commit()


def _program(db, shop, **overrides) -> LoyaltyProgram:
    program = LoyaltyProgram(
        restaurant_id=shop["restaurant_id"],
        name=overrides.pop("name", "TEST Meal card"),
        purchase_type=overrides.pop("purchase_type", PurchaseType.COMPLETED_ORDERS.value),
        required_count=overrides.pop("required_count", 3),
        minimum_spend_minor=overrides.pop("minimum_spend_minor", 0),
        qualifying_item_id=overrides.pop("qualifying_item_id", None),
        qualifying_item_type_id=overrides.pop("qualifying_item_type_id", None),
        reward_expires_days=overrides.pop("reward_expires_days", None),
        is_active=overrides.pop("is_active", True),
    )
    db.add(program)
    db.flush()
    db.add(
        LoyaltyProgramItem(
            restaurant_id=shop["restaurant_id"],
            program_id=program.id,
            item_id=shop["item_id"],
        )
    )
    db.flush()
    return program


def _order(db, shop, *, subtotal=1000, status=OrderStatus.COMPLETED.value, items=None):
    """A completed order, with optional lines for item programs."""
    order_id = uuid.uuid4()
    db.execute(
        text(
            "insert into orders (id, restaurant_id, order_number, customer_user_id, "
            "fulfillment_type, status, payment_method, currency, subtotal_minor, "
            "tax_minor, total_minor) values (:id,:r,:n,:u,'PICKUP',:st,'CARD','USD',"
            ":sub,0,:sub)"
        ),
        {
            "id": str(order_id), "r": str(shop["restaurant_id"]),
            "n": uuid.uuid4().int % 1_000_000_000, "u": str(shop["user_id"]),
            "st": status, "sub": subtotal,
        },
    )
    for item_id, qty in items or []:
        db.execute(
            text(
                "insert into order_items (id, restaurant_id, order_id, menu_item_id, "
                "name_snapshot, unit_price_minor, quantity, line_total_minor) "
                "values (gen_random_uuid(),:r,:o,:i,'x',100,:q,:t)"
            ),
            {
                "r": str(shop["restaurant_id"]), "o": str(order_id),
                "i": str(item_id), "q": qty, "t": 100 * qty,
            },
        )
    db.flush()
    return db.get(Order, order_id)


def _count(db, program, shop) -> int:
    card = db.get(CustomerLoyalty, (program.id, shop["user_id"]))
    return card.current_count if card else 0


# ---------------------------------------------------------------- earning


def test_a_completed_order_earns_a_stamp(shop):
    with tenant_session(shop["restaurant_id"]) as db:
        program = _program(db, shop)
        loyalty.credit_order(db, _order(db, shop))
        assert _count(db, program, shop) == 1


def test_an_order_that_is_not_completed_earns_nothing(shop):
    """Paid is not completed. A program that paid out before the food was
    handed over would pay for orders that never happened."""
    with tenant_session(shop["restaurant_id"]) as db:
        program = _program(db, shop)
        loyalty.credit_order(db, _order(db, shop, status=OrderStatus.PREPARING.value))
        assert _count(db, program, shop) == 0


def test_crediting_the_same_order_twice_credits_once(shop):
    """The idempotency the spec asks for. A retried webhook or a double
    click must not fill a card twice, and the guarantee is a unique index
    rather than a check that could race."""
    with tenant_session(shop["restaurant_id"]) as db:
        program = _program(db, shop)
        order = _order(db, shop)
        loyalty.credit_order(db, order)
        loyalty.credit_order(db, order)
        loyalty.credit_order(db, order)
        assert _count(db, program, shop) == 1
        rows = db.execute(
            text("select count(*) from loyalty_transactions where order_id = :o"),
            {"o": str(order.id)},
        ).scalar_one()
        assert rows == 1


def test_an_order_below_the_minimum_spend_earns_nothing(shop):
    with tenant_session(shop["restaurant_id"]) as db:
        program = _program(db, shop, minimum_spend_minor=1000)
        loyalty.credit_order(db, _order(db, shop, subtotal=999))
        assert _count(db, program, shop) == 0
        loyalty.credit_order(db, _order(db, shop, subtotal=1000))
        assert _count(db, program, shop) == 1


def test_a_switched_off_program_earns_nothing(shop):
    with tenant_session(shop["restaurant_id"]) as db:
        program = _program(db, shop, is_active=False)
        loyalty.credit_order(db, _order(db, shop))
        assert _count(db, program, shop) == 0


def test_an_item_program_counts_quantity_not_orders(shop):
    """Three burgers in one order is three stamps."""
    with tenant_session(shop["restaurant_id"]) as db:
        program = _program(
            db, shop,
            name="TEST Burger card",
            purchase_type=PurchaseType.MENU_ITEM.value,
            qualifying_item_id=shop["item_id"],
            required_count=99,
        )
        loyalty.credit_order(db, _order(db, shop, items=[(shop["item_id"], 3)]))
        assert _count(db, program, shop) == 3


def test_an_item_program_ignores_dishes_it_does_not_name(shop):
    with tenant_session(shop["restaurant_id"]) as db:
        program = _program(
            db, shop,
            name="TEST Burger card",
            purchase_type=PurchaseType.MENU_ITEM.value,
            qualifying_item_id=shop["item_id"],
            required_count=99,
        )
        loyalty.credit_order(db, _order(db, shop, items=[(shop["other_item_id"], 5)]))
        assert _count(db, program, shop) == 0


def test_a_heading_program_counts_every_dish_filed_under_it(shop):
    """"Nine burgers" may mean the section rather than one dish."""
    with tenant_session(shop["restaurant_id"]) as db:
        program = _program(
            db, shop,
            name="TEST Section card",
            purchase_type=PurchaseType.MENU_ITEM.value,
            qualifying_item_type_id=shop["item_type_id"],
            required_count=99,
        )
        loyalty.credit_order(db, _order(db, shop, items=[(shop["item_id"], 2)]))
        assert _count(db, program, shop) >= 2


# --------------------------------------------------------------- winning


def test_filling_a_card_issues_a_reward_and_takes_the_stamps_off(shop):
    """A stamp card, not a threshold: the card is handed over and the next
    one starts empty."""
    with tenant_session(shop["restaurant_id"]) as db:
        program = _program(db, shop, required_count=3)
        for _ in range(3):
            loyalty.credit_order(db, _order(db, shop))
        assert _count(db, program, shop) == 0
        rewards = db.execute(
            text("select count(*) from loyalty_rewards where customer_user_id = :u"),
            {"u": str(shop["user_id"])},
        ).scalar_one()
        assert rewards == 1


def test_a_fourth_order_starts_the_next_card(shop):
    with tenant_session(shop["restaurant_id"]) as db:
        program = _program(db, shop, required_count=3)
        for _ in range(4):
            loyalty.credit_order(db, _order(db, shop))
        assert _count(db, program, shop) == 1


def test_one_order_can_fill_a_card_more_than_once(shop):
    """Nine burgers onto a card holding nine is two rewards. A loop rather
    than an if is the only honest reading of that."""
    with tenant_session(shop["restaurant_id"]) as db:
        _program(
            db, shop,
            name="TEST Burger card",
            purchase_type=PurchaseType.MENU_ITEM.value,
            qualifying_item_id=shop["item_id"],
            required_count=2,
        )
        loyalty.credit_order(db, _order(db, shop, items=[(shop["item_id"], 5)]))
        rewards = db.execute(
            text("select count(*) from loyalty_rewards where customer_user_id = :u"),
            {"u": str(shop["user_id"])},
        ).scalar_one()
        assert rewards == 2


# ------------------------------------------------------------- reversing


def test_a_refund_takes_the_stamps_back(shop):
    with tenant_session(shop["restaurant_id"]) as db:
        program = _program(db, shop, required_count=5)
        order = _order(db, shop)
        loyalty.credit_order(db, order)
        assert _count(db, program, shop) == 1
        loyalty.reverse_order(db, order)
        assert _count(db, program, shop) == 0


def test_a_reversal_is_written_rather_than_the_earning_deleted(shop):
    """What happened stays legible: the ledger keeps both rows."""
    with tenant_session(shop["restaurant_id"]) as db:
        _program(db, shop, required_count=5)
        order = _order(db, shop)
        loyalty.credit_order(db, order)
        loyalty.reverse_order(db, order)
        reasons = db.execute(
            text("select reason from loyalty_transactions where order_id = :o order by reason"),
            {"o": str(order.id)},
        ).scalars().all()
        assert reasons == ["EARN", "REVERSE"]


def test_reversing_twice_takes_the_stamps_back_once(shop):
    with tenant_session(shop["restaurant_id"]) as db:
        program = _program(db, shop, required_count=5)
        order = _order(db, shop)
        loyalty.credit_order(db, order)
        loyalty.credit_order(db, _order(db, shop))
        loyalty.reverse_order(db, order)
        loyalty.reverse_order(db, order)
        assert _count(db, program, shop) == 1


def test_a_card_never_goes_negative(shop):
    """A customer who already spent the reward keeps the dish. Clawing back
    food is not a thing a restaurant can do."""
    with tenant_session(shop["restaurant_id"]) as db:
        program = _program(db, shop, required_count=1)
        order = _order(db, shop)
        loyalty.credit_order(db, order)       # fills, issues, resets to 0
        loyalty.reverse_order(db, order)      # would take it to -1
        assert _count(db, program, shop) == 0
