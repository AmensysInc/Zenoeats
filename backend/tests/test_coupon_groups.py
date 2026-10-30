"""One offer, handed out as numbered codes.

"MORE", ten uses, mints MORE1 through MORE10. The thing worth testing is
not the arithmetic -- a minted code is an ordinary coupon and
test_coupons.py already covers what a coupon is worth -- but the two
properties that are new:

  the codes really are ordinary coupons, so the checkout prices and
  refuses them through exactly the path it already uses, and

  a group is one thing: pausing it pauses every code, retiring it retires
  every code, and a half-minted group never exists.

The third is the point of the feature at all: an order names the code it
used, so MORE7 is traceable to whoever was handed MORE7, where one shared
code makes every redemption look alike.
"""

import uuid

import pytest

from app.services import coupon_groups as groups

from tests.test_admin_restaurants import (  # noqa: F401  (fixtures)
    _create, _email, _owner, _set_own_password, _staff_client, admin_user, cleanup,
)
from tests.test_checkout_contact import CONTACT, _guest, shop as base_shop  # noqa: F401
from tests.test_coupons import (  # noqa: F401  (fixtures)
    _admin_client, _order, _quote, _row, no_rate_limits, operator, shop, team,
    ADDRESS, DELIVERY, LOCATE, ORIGIN, PROFILE, ZONES, _place,
)


def _at(restaurant_id) -> str:
    return f"/api/v1/admin/restaurants/{restaurant_id}/coupon-groups"


def _make(client, restaurant_id, **overrides):
    body = {
        "code_prefix": overrides.pop("code_prefix", f"more{uuid.uuid4().hex[:4]}"),
        "percent_off_bps": 1000,
        "uses": 3,
    }
    body.update(overrides)
    return client.post(_at(restaurant_id), json=body)


# ----------------------------------------------------------- the numbering ---


def test_the_number_goes_on_the_end_with_nothing_between():
    # Read aloud, written down and typed back: a separator would be heard
    # three different ways and every one of them has to match.
    assert groups.code_at("MORE", 1) == "MORE1"
    assert groups.code_at("MORE", 10) == "MORE10"


def test_a_prefix_cannot_end_in_a_number():
    # MORE2 with twelve uses would mint MORE21, which is also what MORE2's
    # first code would be called if the prefix were MORE.
    with pytest.raises(Exception) as refused:
        groups.validate_prefix("MORE2")
    assert "cannot end in a number" in str(refused.value)


def test_a_prefix_is_normalised_the_way_a_code_is():
    assert groups.validate_prefix(" more ") == "MORE"


# -------------------------------------------------------------- the minting ---


def test_creating_a_group_mints_one_code_per_use(operator):
    res = _make(operator.client, operator.id, code_prefix="MORE", uses=10)
    assert res.status_code == 201, res.text
    group = res.json()

    assert group["issued"] == 10
    assert [code["code"] for code in group["codes"]] == [
        f"MORE{n}" for n in range(1, 11)
    ]
    assert [code["number"] for code in group["codes"]] == list(range(1, 11))
    # Nothing used yet, and the screen should be able to say so.
    assert group["used"] == 0
    assert all(code["redeemed_by"] is None for code in group["codes"])


def test_every_minted_code_carries_the_group_offer(operator):
    created = _make(
        operator.client, operator.id, code_prefix="SUMMER", uses=2,
        percent_off_bps=2500, min_subtotal_minor=1500,
    ).json()
    assert created["discount_value"] == 2500
    assert created["min_subtotal_minor"] == 1500


def test_the_codes_do_not_clutter_the_ordinary_coupon_list(operator):
    _make(operator.client, operator.id, code_prefix="MORE", uses=5)
    listed = operator.client.get(operator.at).json()["coupons"]
    # A group of five hundred would otherwise bury every hand-made coupon
    # on the page it shares.
    assert [c for c in listed if c["code"].startswith("MORE")] == []


def test_a_group_whose_codes_already_exist_is_refused_whole(operator):
    # A hand-made MORE2 is in the way of a MORE group of three.
    assert operator.make(code="MORE2").status_code == 201
    res = _make(operator.client, operator.id, code_prefix="MORE", uses=3)
    assert res.status_code == 422
    assert "MORE2" in res.json()["detail"]["message"]

    # And nothing was written: a half-minted group is the one state worth
    # ruling out entirely.
    assert operator.client.get(_at(operator.id)).json()["groups"] == []


def test_two_groups_cannot_share_a_prefix(operator):
    assert _make(operator.client, operator.id, code_prefix="MORE").status_code == 201
    clash = _make(operator.client, operator.id, code_prefix="MORE")
    assert clash.status_code == 422
    assert "already exists" in clash.json()["detail"]["message"]


def test_a_retired_prefix_can_be_used_again(operator):
    first = _make(operator.client, operator.id, code_prefix="MORE").json()
    operator.client.delete(f"{_at(operator.id)}/{first['id']}")
    again = _make(operator.client, operator.id, code_prefix="MORE")
    assert again.status_code == 201, again.text


# ------------------------------------------------------- one thing, switched ---


def test_pausing_a_group_pauses_every_code(operator):
    group = _make(operator.client, operator.id, code_prefix="MORE", uses=3).json()
    operator.client.patch(
        f"{_at(operator.id)}/{group['id']}", json={"is_active": False}
    )
    after = operator.client.get(_at(operator.id)).json()["groups"][0]
    assert after["is_active"] is False
    # The codes are what customers hold; a pause that left them working
    # would have paused nothing.
    assert after["issued"] == 3


def test_retiring_a_group_takes_its_codes_with_it(operator):
    group = _make(operator.client, operator.id, code_prefix="MORE", uses=3).json()
    assert operator.client.delete(
        f"{_at(operator.id)}/{group['id']}"
    ).status_code == 204
    assert operator.client.get(_at(operator.id)).json()["groups"] == []


def test_a_group_of_another_restaurant_is_not_reachable(operator, admin_user, cleanup, monkeypatch):
    group = _make(operator.client, operator.id, code_prefix="MORE").json()
    other = _create(admin_user, cleanup)
    # Same operator, wrong restaurant in the URL: row-level security is what
    # makes this a 404 rather than somebody else's promotion.
    assert operator.client.get(
        f"{_at(other.id)}"
    ).json()["groups"] == []
    assert operator.client.delete(
        f"{_at(other.id)}/{group['id']}"
    ).status_code == 404


# ------------------------------------------------------------ at the checkout ---


def _group_on(shop, **overrides):
    body = {"code_prefix": "MORE", "uses": 3, "percent_off_bps": 1000}
    body.update(overrides)
    res = shop.admin.post(_at(shop.id), json=body)
    assert res.status_code == 201, res.text
    return res.json()


def test_a_minted_code_is_priced_like_any_other_coupon(shop):
    """The point of making the codes real coupon rows: nothing on the
    checkout path knows groups exist, so MORE2 is priced by the same
    function that prices WELCOME10."""
    _group_on(shop)
    priced = _quote(_guest(shop), shop, coupon_code="MORE2").json()

    assert priced["coupon_error"] is None
    assert priced["coupon"]["code"] == "MORE2"
    assert priced["amounts"]["coupon_discount_minor"] > 0


def test_a_number_nobody_minted_is_an_unknown_code(shop):
    # Three were minted, so MORE9 is a code that does not exist -- and it
    # reads exactly like any other code that does not exist.
    _group_on(shop, uses=3)
    refused = _quote(_guest(shop), shop, coupon_code="MORE9").json()
    assert refused["coupon_error"]


def test_each_code_is_good_once(shop):
    """What the numbering is for. If every code could be spent freely they
    would be one code repeated, and the group would be a worse version of a
    single coupon with a redemption limit."""
    _group_on(shop, uses=2)
    codes = shop.admin.get(_at(shop.id)).json()["groups"][0]["codes"]
    assert all(code["redemptions"] == 0 for code in codes)

    customer = _guest(shop)
    assert _order(customer, shop, coupon_code="MORE1").status_code == 201
    # The same code again is refused: it was good for one.
    assert _quote(customer, shop, coupon_code="MORE1").json()["coupon_error"]
    # Its sibling is untouched.
    assert _quote(_guest(shop), shop, coupon_code="MORE2").json()["coupon_error"] is None


def test_the_dashboard_says_which_code_was_spent(shop):
    """The whole reason to number them: one shared code makes every
    redemption look alike."""
    _group_on(shop, uses=3)
    assert _order(_guest(shop), shop, coupon_code="MORE2").status_code == 201

    group = shop.admin.get(_at(shop.id)).json()["groups"][0]
    assert group["used"] == 1
    spent = {code["code"]: code for code in group["codes"]}
    assert spent["MORE2"]["redemptions"] == 1
    assert spent["MORE2"]["discounted_minor"] > 0
    assert spent["MORE2"]["redeemed_at"] is not None
    assert spent["MORE1"]["redemptions"] == 0
    assert spent["MORE3"]["redemptions"] == 0


def test_a_paused_group_refuses_its_codes_at_the_checkout(shop):
    group = _group_on(shop, uses=2)
    shop.admin.patch(f"{_at(shop.id)}/{group['id']}", json={"is_active": False})
    assert _quote(_guest(shop), shop, coupon_code="MORE1").json()["coupon_error"]


def test_a_retired_group_stops_working_and_the_old_order_still_reads(shop):
    group = _group_on(shop, uses=2)
    placed = _order(_guest(shop), shop, coupon_code="MORE1").json()

    assert shop.admin.delete(f"{_at(shop.id)}/{group['id']}").status_code == 204
    # Gone for the next customer, siblings included.
    assert _quote(_guest(shop), shop, coupon_code="MORE2").json()["coupon_error"]
    # The order keeps its own snapshot, so last month's receipt still reads.
    row = _row(shop, placed["order_id"], "coupon_code, coupon_discount_minor")
    assert row["coupon_code"] == "MORE1"
    assert row["coupon_discount_minor"] > 0
