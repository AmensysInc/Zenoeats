"""Coupons, run by the restaurant rather than by the platform.

The endpoints are new; what a coupon *is* is not, and is covered by
test_coupons.py. So what matters here is the surface: that a restaurant can
run its own promotions, that the gate is the one the delivery rings use, and
that row-level security still means a restaurant sees only its own.
"""

import uuid

import pytest

from tests.test_admin_restaurants import (  # noqa: F401  (fixtures)
    _create, _email, _owner, _set_own_password, _staff_client, admin_user, cleanup,
)
from tests.test_staff_removal import team  # noqa: F401  (fixture)

from app.core import staff_auth

AT = "/api/v1/restaurant/coupons"
GROUPS = "/api/v1/restaurant/coupon-groups"
OFFER = "/api/v1/restaurant/signup-offer"
USED = "/api/v1/restaurant/coupon-redemptions"


@pytest.fixture(autouse=True)
def no_rate_limits(monkeypatch):
    from app.core import ratelimit

    monkeypatch.setattr(ratelimit, "_consume", lambda *a, **k: None)


def _code() -> str:
    return f"SAVE{uuid.uuid4().hex[:6].upper()}"


def test_a_restaurant_admin_creates_its_own_coupon(team):
    made = team.owner.post(AT, json={"code": "TUESDAYS", "percent_off_bps": 2000})
    assert made.status_code == 201, made.text
    assert made.json()["code"] == "TUESDAYS"
    assert made.json()["discount_value"] == 2000

    listed = team.owner.get(AT).json()
    assert [c["code"] for c in listed["coupons"]] == ["TUESDAYS"]
    # The form needs the restaurant's own headings and currency, like the
    # platform screen does.
    assert "currency" in listed and "item_types" in listed


def test_a_coupon_can_be_paused_and_retired_by_the_restaurant(team):
    made = team.owner.post(AT, json={"code": _code(), "percent_off_bps": 1000}).json()

    paused = team.owner.patch(f"{AT}/{made['id']}", json={"is_active": False})
    assert paused.status_code == 200, paused.text
    assert paused.json()["is_active"] is False

    assert team.owner.delete(f"{AT}/{made['id']}").status_code == 204
    assert team.owner.get(AT).json()["coupons"] == []


def test_the_code_cannot_be_edited(team):
    """It has been given to customers. A new code is a new coupon."""
    made = team.owner.post(AT, json={"code": "KEEPME", "percent_off_bps": 1000}).json()
    team.owner.patch(f"{AT}/{made['id']}", json={"description": "Renamed"})
    assert team.owner.get(AT).json()["coupons"][0]["code"] == "KEEPME"


def test_a_duplicate_code_is_refused_with_the_code_named(team):
    team.owner.post(AT, json={"code": "ONLYONE", "percent_off_bps": 1000})
    again = team.owner.post(AT, json={"code": "onlyone", "percent_off_bps": 1000})
    assert again.status_code == 422
    assert "ONLYONE" in again.json()["detail"]["message"]


def test_a_restaurant_runs_its_own_group_coupons(team):
    made = team.owner.post(
        GROUPS, json={"code_prefix": "MORE", "uses": 4, "percent_off_bps": 1000}
    )
    assert made.status_code == 201, made.text
    assert [c["code"] for c in made.json()["codes"]] == ["MORE1", "MORE2", "MORE3", "MORE4"]

    # And they do not clutter the plain coupon list.
    assert team.owner.get(AT).json()["coupons"] == []


def test_a_restaurant_sets_up_its_own_welcome_offer(team):
    saved = team.owner.put(
        OFFER, json={"code_prefix": "WELCOME", "percent_off_bps": 1500, "valid_days": 30}
    )
    assert saved.status_code == 200, saved.text

    state = team.owner.get(OFFER).json()
    assert state["offer"]["code_prefix"] == "WELCOME"
    # Told plainly whether this deployment can actually run it.
    assert state["sms_configured"] is False


def test_the_redemption_record_is_readable_by_the_restaurant(team):
    listed = team.owner.get(USED)
    assert listed.status_code == 200, listed.text
    assert listed.json()["total_redemptions"] == 0


# ------------------------------------------------------------------ the gate ---


def test_a_cook_cannot_run_promotions(team):
    """The same gate the delivery rings use: a discount is money, so it sits
    with the people who already set the tax rate, not with everyone who can
    open the kitchen board."""
    cook_id, _ = team.member("COOK")
    cook = team.client(cook_id)

    assert cook.get(AT).status_code == 403
    assert cook.post(AT, json={"code": "NOPE1", "percent_off_bps": 1000}).status_code == 403
    assert cook.get(GROUPS).status_code == 403
    assert cook.get(USED).status_code == 403


def test_one_restaurant_cannot_touch_another_s_coupons(team, admin_user, cleanup):
    """Row-level security, not a predicate somebody remembered to write."""
    mine = team.owner.post(AT, json={"code": "MINEONLY", "percent_off_bps": 1000}).json()

    other = _create(admin_user, cleanup)
    other_email = _email()
    other_owner, _ = _owner(admin_user, other.id, other_email)
    # Clears must_change_password, which otherwise refuses every endpoint.
    _set_own_password(other_email, "owner password 123")
    theirs = _staff_client(other.slug)
    theirs.cookies.set(
        staff_auth.SESSION_COOKIE, staff_auth.issue_session(other_owner.user_id)
    )

    mine_list = theirs.get(AT)
    assert mine_list.status_code == 200, mine_list.text
    # Their own list is empty, and my coupon is not reachable by id.
    assert mine_list.json()["coupons"] == []
    # 422 "No such coupon", exactly as the platform screen answers for an id
    # it cannot see: row-level security filtered it out, so there is nothing
    # to find rather than something being refused.
    blocked = theirs.patch(f"{AT}/{mine['id']}", json={"is_active": False})
    assert blocked.status_code == 422, blocked.text
    assert theirs.delete(f"{AT}/{mine['id']}").status_code == 422
