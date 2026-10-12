"""Promotion codes: what they take off, and what they refuse.

Three layers, because they fail differently and a bug in one is invisible
from the others:

  the arithmetic   what a code is worth, including every cap
  the pricing      where in the order of operations it lands
  the two APIs     the dashboard that creates one, and the checkout that
                   accepts or refuses it

The property worth the most here is that the quote and the order agree. A
code accepted on the summary page and refused at payment is worse than one
refused outright -- the customer has already decided to buy -- so several of
the tests below quote first and then order, and assert the same answer twice.
"""

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.core import errors
from app.services import coupons
from app.services.coupons import discount_for, normalise

from tests.test_admin_restaurants import (  # noqa: F401  (fixtures)
    _create,
    _email,
    _owner,
    _set_own_password,
    _staff_client,
    admin_user,
    cleanup,
)
from tests.test_checkout_contact import CONTACT, _guest, shop  # noqa: F401  (fixtures)
from tests.test_delivery_pricing import ADDRESS, DELIVERY, LOCATE, ORIGIN, PROFILE, ZONES, _place  # noqa: F401
from tests.test_order_delivery_fee import _an_item  # noqa: F401
from tests.test_staff_removal import team  # noqa: F401  (fixture)

COUPONS = "/api/v1/restaurant/coupons"
QUOTE = "/api/v1/orders/quote"


@pytest.fixture(autouse=True)
def no_rate_limits(monkeypatch):
    from app.core import ratelimit

    monkeypatch.setattr(ratelimit, "_consume", lambda *a, **k: None)


def _coupon(**overrides) -> SimpleNamespace:
    """A coupon row, as far as the arithmetic reads one."""
    values = dict(discount_kind="PERCENT", discount_value=1000, max_discount_minor=None)
    values.update(overrides)
    return SimpleNamespace(**values)


# --------------------------------------------------------- the arithmetic ---

def test_a_percentage_comes_off_the_cart():
    assert discount_for(_coupon(discount_value=1000), 2000) == 200


def test_a_percentage_rounds_half_up_like_tax_and_combos_do():
    """1250 bps of 1005 is 125.625. Two roundings in one total is how a
    receipt stops adding up."""
    assert discount_for(_coupon(discount_value=1250), 1005) == 126


def test_a_flat_amount_comes_off_the_cart():
    assert discount_for(_coupon(discount_kind="AMOUNT", discount_value=500), 2000) == 500


def test_a_flat_amount_never_exceeds_the_cart():
    """Ten off a six-pound order is six off, not a refund of the difference."""
    assert discount_for(_coupon(discount_kind="AMOUNT", discount_value=1000), 600) == 600


def test_a_ceiling_caps_a_percentage():
    """What a ceiling is for: 20% off is a different offer on a $10 order and
    on a $400 office lunch."""
    coupon = _coupon(discount_value=2000, max_discount_minor=500)
    assert discount_for(coupon, 10000) == 500
    assert discount_for(coupon, 1000) == 200


def test_nothing_comes_off_an_empty_cart():
    assert discount_for(_coupon(), 0) == 0


def test_an_unknown_kind_takes_nothing_off():
    """NONE is a real DiscountKind for a combo and meaningless here. A kind
    this cannot price must not fall through to a wrong number."""
    assert discount_for(_coupon(discount_kind="NONE", discount_value=9999), 1000) == 0


@pytest.mark.parametrize(
    "typed, stored",
    [("save10", "SAVE10"), ("  save10  ", "SAVE10"), ("SAVE 10", "SAVE10"),
     ("Save\t10", "SAVE10")],
)
def test_a_code_is_compared_in_one_form(typed, stored):
    """A customer reading a code off a poster types "SAVE 10" about as often
    as "SAVE10", and both are the same coupon."""
    assert normalise(typed) == stored


# ------------------------------------------------------------- the pricing ---
#
# Where the coupon lands in the order of operations, which is the part that
# decides money and the part an off-by-one would quietly bury.

from tests.test_combo_pricing import (  # noqa: E402  (after the fixtures above)
    DRINKS,
    FOOD,
    FakeCombo,
    FakeItem,
    FakeRestaurant,
    FakeSession,
    FakeSlot,
)


def _applied(minor: int) -> coupons.AppliedCoupon:
    return coupons.AppliedCoupon(
        coupon_id=uuid.uuid4(), code="SAVE", label=None, discount_minor=minor
    )


def test_a_coupon_comes_off_the_cart_and_the_subtotal_still_says_the_food():
    from app.services.pricing import price_cart

    burger = FakeItem("Burger", 1000)
    session = FakeSession([burger], [])
    cart = price_cart(
        session, FakeRestaurant(),
        [{"menu_item_id": str(burger.id), "quantity": 1, "modifiers": []}],
        coupon=_applied(250),
    )

    assert cart.subtotal_minor == 1000
    assert cart.discount_minor == 250
    assert cart.coupon.discount_minor == 250
    assert cart.total_minor == 750


def test_a_coupon_comes_off_what_is_left_after_the_meal_deals():
    """A cart whose combos already took money off cannot also have a
    percentage of the undiscounted price taken off: the coupon is capped at
    what is actually still being charged for the food."""
    from app.services.pricing import price_cart

    burger = FakeItem("Burger", 1000)
    tea = FakeItem("Tea", 300, DRINKS)
    # 100% off, so the combo takes the food to nothing.
    combo = FakeCombo(
        "Everything Free",
        [FakeSlot(FOOD, [burger]), FakeSlot(DRINKS, [tea])],
        kind="PERCENT", value=10000,
    )
    session = FakeSession([burger, tea], [combo])

    cart = price_cart(
        session, FakeRestaurant(), [],
        [{
            "combo_id": str(combo.id), "quantity": 1,
            "selections": [
                {"slot_id": str(combo.slots[0].id), "menu_item_id": str(burger.id)},
                {"slot_id": str(combo.slots[1].id), "menu_item_id": str(tea.id)},
            ],
        }],
        coupon=_applied(500),
    )

    assert cart.subtotal_minor == 1300
    # The combo took all of it; the coupon has nothing left to take.
    assert cart.discount_minor == 1300
    assert cart.total_minor == 0
    # And the coupon reports what it actually took off, which is nothing --
    # so the order does not record a discount it did not give.
    assert cart.coupon is None


def test_a_coupon_never_comes_off_the_delivery_fee():
    """A coupon that discounted delivery would be a restaurant paying a
    driver out of a promotion."""
    from app.services.pricing import price_cart

    burger = FakeItem("Burger", 1000)
    session = FakeSession([burger], [])
    cart = price_cart(
        session, FakeRestaurant(),
        [{"menu_item_id": str(burger.id), "quantity": 1, "modifiers": []}],
        delivery_fee_minor=400,
        coupon=_applied(1000),
    )

    assert cart.discount_minor == 1000
    assert cart.delivery_fee_minor == 400
    # The food is free; the delivery is not.
    assert cart.total_minor == 400


# ------------------------------------------------------ the dashboard API ---

pytestmark = pytest.mark.integration


def _make(client, **overrides):
    body = {"code": f"save{uuid.uuid4().hex[:6]}", "discount_kind": "PERCENT",
            "discount_value": 1000}
    body.update(overrides)
    return client.post(COUPONS, json=body)


def test_a_manager_creates_a_coupon_and_it_is_listed(team):
    res = _make(team.owner, code="welcome10", description="Launch offer")
    assert res.status_code == 201
    created = res.json()
    # Normalised on the way in, so the lookup at checkout can be exact.
    assert created["code"] == "WELCOME10"
    assert created["redemptions"] == 0
    assert created["discounted_minor"] == 0

    listed = team.owner.get(COUPONS).json()
    assert listed["currency"]
    assert [c["code"] for c in listed["coupons"]] == ["WELCOME10"]


def test_the_same_live_code_cannot_exist_twice(team):
    _make(team.owner, code="twice")
    again = _make(team.owner, code="TWICE")
    assert again.status_code == 422
    assert "already exists" in again.json()["detail"]["message"]


def test_a_retired_code_can_be_created_again(team):
    """A seasonal promotion comes back. The unique index covers the live rows
    only, which is what makes the code free again the moment it is retired."""
    first = _make(team.owner, code="spring").json()
    assert team.owner.delete(f"{COUPONS}/{first['id']}").status_code == 204

    assert _make(team.owner, code="spring").status_code == 201
    # And the retired one is gone from the dashboard without taking its
    # history with it.
    assert len(team.owner.get(COUPONS).json()["coupons"]) == 1


@pytest.mark.parametrize(
    "overrides, because",
    [
        ({"discount_kind": "PERCENT", "discount_value": 10001}, "over 100%"),
        ({"discount_kind": "NONE", "discount_value": 100}, "no kind of discount"),
        ({"discount_value": 0}, "worth nothing"),
        ({"code": "ab"}, "too short"),
        ({"code": "save-10"}, "not alphanumeric"),
        ({"discount_kind": "AMOUNT", "discount_value": 500, "max_discount_minor": 100},
         "a ceiling on a flat amount"),
        ({"max_redemptions": 2, "max_per_customer": 3}, "per-customer over the total"),
    ],
)
def test_a_coupon_nobody_meant_is_refused(team, overrides, because):
    assert _make(team.owner, **overrides).status_code == 422, because


def test_the_window_has_to_run_forwards(team):
    from app.db.base import utcnow

    now = utcnow()
    res = _make(
        team.owner,
        starts_at=(now + timedelta(days=2)).isoformat(),
        ends_at=now.isoformat(),
    )
    assert res.status_code == 422


def test_a_coupon_can_be_edited_but_not_renamed(team):
    created = _make(team.owner, code="editme", discount_value=1000).json()

    res = team.owner.patch(
        f"{COUPONS}/{created['id']}",
        json={"discount_value": 1500, "description": "Better offer", "code": "RENAMED"},
    )
    assert res.status_code == 200
    edited = res.json()
    assert edited["discount_value"] == 1500
    assert edited["description"] == "Better offer"
    # The code customers were given is not editable: a new code is a new
    # coupon, and the extra field is ignored rather than honoured.
    assert edited["code"] == "EDITME"


def test_kitchen_cannot_create_or_see_coupons(team):
    user_id, _ = team.member("KITCHEN")
    kitchen = team.client(user_id)

    assert _make(kitchen).status_code == 403
    assert kitchen.get(COUPONS).status_code == 403


def test_support_can_look_but_not_touch(team):
    """The same split the menu has: support answers questions about a
    promotion without being able to invent a discount."""
    _make(team.owner, code="support1")
    user_id, _ = team.member("IT_SUPPORT")
    support = team.client(user_id)

    assert support.get(COUPONS).status_code == 200
    assert _make(support).status_code == 403


# -------------------------------------------------------- at the checkout ---


def _publish(shop, **overrides):
    """A coupon on the shop the checkout tests order from."""
    body = {"code": overrides.pop("code", "SAVE20"), "discount_kind": "PERCENT",
            "discount_value": 2000}
    body.update(overrides)
    res = shop.owner.post(COUPONS, json=body)
    assert res.status_code == 201, res.text
    return res.json()


def _quote(client, shop, **extra):
    return client.post(QUOTE, json={
        "items": [{"menu_item_id": str(shop.item_id), "quantity": 1, "modifiers": []}],
        **extra,
    })


def _order(client, shop, **extra):
    return client.post(
        "/api/v1/orders",
        json={
            "items": [{"menu_item_id": str(shop.item_id), "quantity": 1, "modifiers": []}],
            "contact": CONTACT,
            **extra,
        },
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )


def _row(shop, order_id, columns):
    from app.db.session import tenant_session

    with tenant_session(shop.id) as session:
        return session.execute(
            text(f"SELECT {columns} FROM orders WHERE id = :o"), {"o": order_id}
        ).mappings().one()


def test_a_good_code_shows_its_discount_on_the_quote(shop):
    _publish(shop)
    plain = _quote(_guest(shop), shop).json()
    discounted = _quote(_guest(shop), shop, coupon_code="save20").json()

    assert discounted["coupon"]["code"] == "SAVE20"
    assert discounted["coupon_error"] is None

    expected = plain["amounts"]["subtotal_minor"] // 5
    assert discounted["amounts"]["coupon_discount_minor"] == expected

    # The total falls by more than the discount, because the tax on the
    # discounted food goes with it: a coupon reduces the taxable base rather
    # than being taken off a total that was already taxed. Written out as the
    # sum rather than as a single number, so it reads as the rule it is.
    after = discounted["amounts"]
    assert after["tax_minor"] < plain["amounts"]["tax_minor"]
    assert after["total_minor"] == (
        after["subtotal_minor"] - after["discount_minor"] + after["tax_minor"]
    )
    assert after["total_minor"] < plain["amounts"]["total_minor"] - expected


def test_a_bad_code_still_leaves_the_cart_with_a_price(shop):
    """The reason belongs beside the field they typed in, not in place of the
    order summary."""
    plain = _quote(_guest(shop), shop).json()
    res = _quote(_guest(shop), shop, coupon_code="NOPE")

    assert res.status_code == 200
    body = res.json()
    assert body["coupon"] is None
    assert body["coupon_error"]
    assert body["amounts"]["total_minor"] == plain["amounts"]["total_minor"]


def test_an_order_records_the_code_and_what_it_took_off(shop):
    _publish(shop)
    client = _guest(shop)
    quoted = _quote(client, shop, coupon_code="SAVE20").json()

    res = _order(client, shop, coupon_code="SAVE20",
                 expected_total_minor=quoted["amounts"]["total_minor"])
    assert res.status_code == 201, res.text
    body = res.json()

    # The quote and the order agree, which is the whole point.
    assert body["amounts"]["total_minor"] == quoted["amounts"]["total_minor"]
    assert body["coupon_code"] == "SAVE20"

    row = _row(shop, body["order_id"],
               "coupon_code, coupon_discount_minor, discount_minor, coupon_id")
    assert row["coupon_code"] == "SAVE20"
    assert row["coupon_discount_minor"] == quoted["amounts"]["coupon_discount_minor"]
    # The coupon's share is inside the order's total discount.
    assert row["discount_minor"] == row["coupon_discount_minor"]
    assert row["coupon_id"] is not None


def test_an_order_with_a_bad_code_is_refused_rather_than_charged_in_full(shop):
    res = _order(_guest(shop), shop, coupon_code="NOTACODE")
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "COUPON_REFUSED"


def test_a_switched_off_code_reads_exactly_like_one_that_does_not_exist(shop):
    """So the error cannot be used to go fishing for codes."""
    created = _publish(shop, code="PAUSED")
    shop.owner.patch(f"{COUPONS}/{created['id']}", json={"is_active": False})

    off = _quote(_guest(shop), shop, coupon_code="PAUSED").json()["coupon_error"]
    unknown = _quote(_guest(shop), shop, coupon_code="NEVEREXISTED").json()["coupon_error"]
    assert off == unknown == coupons.UNKNOWN


def test_a_code_that_has_not_started_says_so(shop):
    from app.db.base import utcnow

    _publish(shop, code="SOON", starts_at=(utcnow() + timedelta(days=1)).isoformat())
    error = _quote(_guest(shop), shop, coupon_code="SOON").json()["coupon_error"]
    assert "isn't active yet" in error


def test_an_expired_code_says_so(shop):
    from app.db.base import utcnow

    _publish(shop, code="GONE", ends_at=(utcnow() - timedelta(minutes=1)).isoformat())
    error = _quote(_guest(shop), shop, coupon_code="GONE").json()["coupon_error"]
    assert "expired" in error


def test_a_minimum_spend_is_the_one_refusal_that_quotes_a_number(shop):
    """Because it is a fact about the customer's own cart, and the thing they
    can act on by adding something."""
    _publish(shop, code="BIGORDER", min_subtotal_minor=100_000)
    error = _quote(_guest(shop), shop, coupon_code="BIGORDER").json()["coupon_error"]
    assert "1000.00 or more" in error


def test_a_fully_redeemed_code_is_refused_on_the_next_order(shop):
    _publish(shop, code="ONEONLY", max_redemptions=1)

    first = _order(_guest(shop), shop, coupon_code="ONEONLY")
    assert first.status_code == 201

    second = _order(_guest(shop), shop, coupon_code="ONEONLY")
    assert second.status_code == 422
    assert "fully redeemed" in second.json()["detail"]["message"]


def test_one_per_customer_stops_the_same_customer_twice(shop):
    _publish(shop, code="ONCEEACH", max_per_customer=1)
    client = _guest(shop)

    assert _order(client, shop, coupon_code="ONCEEACH").status_code == 201

    again = _order(client, shop, coupon_code="ONCEEACH")
    assert again.status_code == 422
    assert "already used" in again.json()["detail"]["message"]

    # Somebody else is unaffected.
    assert _order(_guest(shop), shop, coupon_code="ONCEEACH").status_code == 201


def test_the_dashboard_counts_what_the_promotion_actually_cost(shop):
    created = _publish(shop, code="COUNTME")
    client = _guest(shop)
    quoted = _quote(client, shop, coupon_code="COUNTME").json()
    assert _order(client, shop, coupon_code="COUNTME").status_code == 201

    listed = {c["code"]: c for c in shop.owner.get(COUPONS).json()["coupons"]}["COUNTME"]
    assert listed["redemptions"] == 1
    assert listed["discounted_minor"] == quoted["amounts"]["coupon_discount_minor"]


def test_a_cancelled_order_gives_the_redemption_back(shop):
    """An order that never happened commercially did not spend a redemption.
    Counting one would let an abandoned checkout use up a limited coupon."""
    _publish(shop, code="GIVEBACK", max_redemptions=1)
    placed = _order(_guest(shop), shop, coupon_code="GIVEBACK").json()

    from app.db.session import tenant_session

    with tenant_session(shop.id) as session:
        session.execute(
            text("UPDATE orders SET status = 'EXPIRED' WHERE id = :o"),
            {"o": placed["order_id"]},
        )

    assert _order(_guest(shop), shop, coupon_code="GIVEBACK").status_code == 201


def test_a_retired_coupon_stops_working_and_the_old_order_still_reads(shop):
    created = _publish(shop, code="RETIRED")
    client = _guest(shop)
    placed = _order(client, shop, coupon_code="RETIRED").json()

    assert shop.owner.delete(f"{COUPONS}/{created['id']}").status_code == 204

    # Gone for the next customer.
    assert _order(_guest(shop), shop, coupon_code="RETIRED").status_code == 422
    # And the order that used it still says what it was charged.
    row = _row(shop, placed["order_id"], "coupon_code, coupon_discount_minor")
    assert row["coupon_code"] == "RETIRED"
    assert row["coupon_discount_minor"] > 0
