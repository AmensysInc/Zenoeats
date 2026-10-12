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
# Imported under another name so the fixture below can wrap it: a coupon is
# created by an operator on the admin surface, and the checkout tests need
# both that and the storefront the shop fixture builds.
from tests.test_checkout_contact import CONTACT, _guest, shop as base_shop  # noqa: F401
from tests.test_delivery_pricing import ADDRESS, DELIVERY, LOCATE, ORIGIN, PROFILE, ZONES, _place  # noqa: F401
from tests.test_order_delivery_fee import _an_item  # noqa: F401
from tests.test_staff_removal import team  # noqa: F401  (fixture)

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


# ---------------------------------------------- creating one, as an admin ---
#
# Coupons are the platform's to create, not a tenant's: these endpoints are on
# the admin router, behind a platform-admin session, on admin.<root domain>.
# The restaurant's own portal has no coupon screen, and the tests at the
# bottom of this section are what hold that line.

pytestmark = pytest.mark.integration


def _admin_client(admin_user, monkeypatch):
    """A signed-in platform admin, on the one hostname the admin API answers
    from."""
    from fastapi.testclient import TestClient

    from app.core import platform_auth
    from app.main import app

    monkeypatch.setattr(
        platform_auth.settings, "ADMIN_USERS",
        f"{admin_user.email}:{platform_auth.hash_password('x' * 16)}",
    )
    client = TestClient(app, base_url="http://admin.zenoeats.local")
    client.cookies.set(
        platform_auth.SESSION_COOKIE,
        platform_auth.issue_session(platform_auth.PlatformAdmin(email=admin_user.email)),
    )
    return client


@pytest.fixture
def operator(admin_user, cleanup, monkeypatch):
    """An admin, and a restaurant to put coupons on."""
    restaurant = _create(admin_user, cleanup)
    client = _admin_client(admin_user, monkeypatch)

    class Operator:
        id = restaurant.id
        slug = restaurant.slug
        at = f"/api/v1/admin/restaurants/{restaurant.id}/coupons"

        @staticmethod
        def make(**overrides):
            body = {
                "code": overrides.pop("code", f"save{uuid.uuid4().hex[:6]}"),
                "discount_kind": "PERCENT",
                "discount_value": 1000,
            }
            body.update(overrides)
            return client.post(Operator.at, json=body)

    Operator.client = client
    return Operator


def test_an_admin_creates_a_coupon_and_it_is_listed(operator):
    res = operator.make(code="welcome10", description="Launch offer")
    assert res.status_code == 201, res.text
    created = res.json()
    # Normalised on the way in, so the lookup at checkout can be exact.
    assert created["code"] == "WELCOME10"
    assert created["redemptions"] == 0
    assert created["discounted_minor"] == 0

    listed = operator.client.get(operator.at).json()
    assert listed["restaurant_name"]
    assert listed["currency"]
    assert [c["code"] for c in listed["coupons"]] == ["WELCOME10"]


def test_every_write_is_audited(operator):
    """A discount is money. The log says which operator made it."""
    from app.db.session import system_session

    created = operator.make(code="audited").json()
    operator.client.patch(f"{operator.at}/{created['id']}", json={"discount_value": 1500})
    operator.client.delete(f"{operator.at}/{created['id']}")

    with system_session() as session:
        actions = [
            row[0]
            for row in session.execute(
                text(
                    "SELECT action FROM platform_audit_logs "
                    "WHERE scope->>'coupon_id' = :c ORDER BY created_at"
                ),
                {"c": created["id"]},
            ).all()
        ]
    assert actions == [
        "SUPER_ADMIN_CREATE_COUPON",
        "SUPER_ADMIN_UPDATE_COUPON",
        "SUPER_ADMIN_RETIRE_COUPON",
    ]


def test_the_same_live_code_cannot_exist_twice_on_one_restaurant(operator):
    operator.make(code="twice")
    again = operator.make(code="TWICE")
    assert again.status_code == 422
    assert "already exists" in again.json()["detail"]["message"]


def test_two_restaurants_may_run_the_same_code(admin_user, cleanup, monkeypatch):
    """A code belongs to a restaurant. Two of them running WELCOME10 is two
    promotions, and neither has to know about the other."""
    client = _admin_client(admin_user, monkeypatch)
    first, second = _create(admin_user, cleanup), _create(admin_user, cleanup)

    body = {"code": "SHARED", "discount_kind": "PERCENT", "discount_value": 500}
    for restaurant in (first, second):
        res = client.post(f"/api/v1/admin/restaurants/{restaurant.id}/coupons", json=body)
        assert res.status_code == 201, res.text

    # And each sees only its own.
    for restaurant in (first, second):
        listed = client.get(f"/api/v1/admin/restaurants/{restaurant.id}/coupons").json()
        assert [c["code"] for c in listed["coupons"]] == ["SHARED"]


def test_a_coupon_cannot_be_edited_through_another_restaurant(
    admin_user, cleanup, monkeypatch, operator
):
    """The predicate that matters most in that module. These endpoints run on
    the cross-tenant surface, where row-level security is not scoping
    anything -- so a lookup by coupon id alone would reach this coupon from
    somebody else's restaurant."""
    created = operator.make(code="mineonly").json()
    other = _create(admin_user, cleanup)
    client = _admin_client(admin_user, monkeypatch)

    at = f"/api/v1/admin/restaurants/{other.id}/coupons/{created['id']}"
    assert client.patch(at, json={"discount_value": 9000}).status_code == 422
    assert client.delete(at).status_code == 422

    # Untouched, and still this restaurant's.
    mine = operator.client.get(operator.at).json()["coupons"]
    assert [(c["code"], c["discount_value"]) for c in mine] == [("MINEONLY", 1000)]


def test_a_retired_code_can_be_created_again(operator):
    """A seasonal promotion comes back. The unique index covers the live rows
    only, which is what makes the code free again the moment it is retired."""
    first = operator.make(code="spring").json()
    assert operator.client.delete(f"{operator.at}/{first['id']}").status_code == 204

    assert operator.make(code="spring").status_code == 201
    # And the retired one is gone from the list without taking its history.
    assert len(operator.client.get(operator.at).json()["coupons"]) == 1


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
def test_a_coupon_nobody_meant_is_refused(operator, overrides, because):
    assert operator.make(**overrides).status_code == 422, because


def test_the_window_has_to_run_forwards(operator):
    from app.db.base import utcnow

    now = utcnow()
    res = operator.make(
        starts_at=(now + timedelta(days=2)).isoformat(),
        ends_at=now.isoformat(),
    )
    assert res.status_code == 422


def test_a_coupon_can_be_edited_but_not_renamed(operator):
    created = operator.make(code="editme", discount_value=1000).json()

    res = operator.client.patch(
        f"{operator.at}/{created['id']}",
        json={"discount_value": 1500, "description": "Better offer", "code": "RENAMED"},
    )
    assert res.status_code == 200
    edited = res.json()
    assert edited["discount_value"] == 1500
    assert edited["description"] == "Better offer"
    # The code customers were given is not editable: a new code is a new
    # coupon, and the extra field is ignored rather than honoured.
    assert edited["code"] == "EDITME"


def test_coupons_cannot_be_reached_from_a_restaurant_hostname(operator, admin_user):
    """Even holding a valid admin session. The endpoint is not served there,
    which is what stops a restaurant calling it from its own portal."""
    from fastapi.testclient import TestClient

    from app.core import platform_auth
    from app.main import app

    token = platform_auth.issue_session(platform_auth.PlatformAdmin(email=admin_user.email))
    for host in (f"{operator.slug}.zenoeats.local", "zenoeats.local"):
        client = TestClient(app, base_url=f"http://{host}")
        client.cookies.set(platform_auth.SESSION_COOKIE, token)
        assert client.get(operator.at).status_code == 404, host


def test_a_restaurant_login_cannot_create_a_coupon(team, operator):
    """The restaurant's own portal has no coupon screen, and no endpoint
    behind one either. A staff session on the admin host is not an admin."""
    from app.core import staff_auth
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app, base_url="http://admin.zenoeats.local")
    client.cookies.set(staff_auth.SESSION_COOKIE, staff_auth.issue_session(team.owner_id))
    res = client.post(operator.at, json={
        "code": "SNEAKY", "discount_kind": "PERCENT", "discount_value": 5000,
    })
    assert res.status_code in (401, 403)


def test_the_restaurant_portal_serves_no_coupon_endpoint(team):
    """Belt and braces on the line above: whatever a staff session tries on
    its own hostname, there is nothing there."""
    for path in ("/api/v1/restaurant/coupons", "/api/v1/restaurant/coupons/x"):
        assert team.owner.get(path).status_code == 404, path
        assert team.owner.post(path, json={}).status_code in (404, 405), path


def test_coupons_on_a_restaurant_that_does_not_exist(operator):
    missing = uuid.uuid4()
    res = operator.client.get(f"/api/v1/admin/restaurants/{missing}/coupons")
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "RESTAURANT_NOT_FOUND"


# -------------------------------------------------------- at the checkout ---


@pytest.fixture
def shop(base_shop, admin_user, monkeypatch):
    """The checkout tests' restaurant, plus the operator who gives it codes."""
    base_shop.admin = _admin_client(admin_user, monkeypatch)
    base_shop.coupons_at = f"/api/v1/admin/restaurants/{base_shop.id}/coupons"
    return base_shop


def _publish(shop, **overrides):
    """A coupon on the shop the checkout tests order from.

    Created the way coupons are actually created -- by the platform, on the
    admin surface -- so these tests exercise the same path an operator uses
    rather than a shortcut into the table.
    """
    body = {"code": overrides.pop("code", "SAVE20"), "discount_kind": "PERCENT",
            "discount_value": 2000}
    body.update(overrides)
    res = shop.admin.post(shop.coupons_at, json=body)
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
    shop.admin.patch(f"{shop.coupons_at}/{created['id']}", json={"is_active": False})

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

    listed = {
        c["code"]: c for c in shop.admin.get(shop.coupons_at).json()["coupons"]
    }["COUNTME"]
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

    assert shop.admin.delete(f"{shop.coupons_at}/{created['id']}").status_code == 204

    # Gone for the next customer.
    assert _order(_guest(shop), shop, coupon_code="RETIRED").status_code == 422
    # And the order that used it still says what it was charged.
    row = _row(shop, placed["order_id"], "coupon_code, coupon_discount_minor")
    assert row["coupon_code"] == "RETIRED"
    assert row["coupon_discount_minor"] > 0
