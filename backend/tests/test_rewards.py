"""The rewards program's own rules.

The arithmetic and the semantics, away from HTTP: what a point is, which
rewards a balance may take, and when an offer is on. The threshold model is
the thing to pin down -- reaching a rung unlocks it for good, nothing is
deducted -- because every screen is written against that and a change to it
would be silent.
"""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core import errors
from app.models import Offer
from app.models.identity import UserKind
from app.services import rewards
from app.services.rewards import RewardChoice

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def _choice(points: int, name: str = "Fries", available: bool = True) -> RewardChoice:
    return RewardChoice(
        id=uuid.uuid4(),
        item_id=uuid.uuid4(),
        item_name=name,
        points_required=points,
        image_path=None,
        is_available=available,
    )


def _user(kind=UserKind.CUSTOMER.value):
    return SimpleNamespace(id=uuid.uuid4(), kind=kind)


# --- what a point is -------------------------------------------------------


def test_a_guest_earns_nothing_without_the_database_being_asked():
    """A guest row is made per checkout and reachable only by the cookie that
    made it, so there is no identity to accrue against. Passing no session at
    all proves the short circuit happens before any query."""
    assert rewards.points(None, _user(UserKind.GUEST.value)) == 0


def test_nobody_signed_in_earns_nothing():
    assert rewards.points(None, None) == 0


def test_only_completed_orders_count():
    """Not paid, not preparing: completed. A program that paid out before the
    food was handed over would pay for orders that never happened."""
    seen = {}

    class Session:
        def execute(self, statement):
            seen["sql"] = str(statement.compile(compile_kwargs={"literal_binds": True}))
            return SimpleNamespace(scalar_one=lambda: 7)

    assert rewards.points(Session(), _user()) == 7
    assert "COMPLETED" in seen["sql"]
    # Any other state would mean a cancelled order kept its point.
    for state in ("PENDING_PAYMENT", "PREPARING", "CANCELLED", "REFUNDED"):
        assert state not in seen["sql"], state


# --- the ladder ------------------------------------------------------------


def test_the_rungs_are_the_numbers_the_rewards_use():
    """No table of tiers: a threshold exists because some dish names it."""
    assert rewards.thresholds([_choice(20), _choice(10), _choice(35)]) == [10, 20, 35]


def test_a_rung_named_twice_is_one_rung():
    """Two dishes free at ten points is one rung offering two things."""
    assert rewards.thresholds([_choice(10, "Fries"), _choice(10, "Drink")]) == [10]


def test_an_empty_ladder_has_no_rungs_and_no_next():
    assert rewards.thresholds([]) == []
    assert rewards.next_threshold(0, []) is None


# --- what a balance may take ----------------------------------------------


def test_reaching_a_rung_unlocks_it():
    ladder = [_choice(10), _choice(20)]
    assert len(rewards.claimable(10, ladder)) == 1
    assert len(rewards.claimable(20, ladder)) == 2


def test_a_rung_once_reached_stays_reached():
    """The threshold model: nothing is deducted, so a customer at fifty still
    has everything ten offered. This is the rule every screen is written
    against."""
    ladder = [_choice(10, "Fries"), _choice(20, "Sundae"), _choice(35, "Burger")]
    names = {c.item_name for c in rewards.claimable(50, ladder)}
    assert names == {"Fries", "Sundae", "Burger"}


def test_a_balance_below_the_first_rung_takes_nothing():
    assert rewards.claimable(9, [_choice(10)]) == []


def test_a_sold_out_dish_stays_on_the_ladder_but_cannot_be_taken():
    """Hiding it would make the ladder appear to change when a fryer breaks."""
    ladder = [_choice(10, "Fries", available=False), _choice(10, "Drink")]
    assert rewards.thresholds(ladder) == [10]
    assert [c.item_name for c in rewards.claimable(10, ladder)] == ["Drink"]


def test_the_next_rung_is_the_first_one_above_the_balance():
    ladder = [_choice(10), _choice(20), _choice(35)]
    assert rewards.next_threshold(0, ladder) == 10
    assert rewards.next_threshold(10, ladder) == 20
    assert rewards.next_threshold(19, ladder) == 20


def test_there_is_no_next_rung_once_the_top_is_reached():
    """Null rather than the top rung again: "0 more orders" is not progress."""
    assert rewards.next_threshold(35, [_choice(10), _choice(35)]) is None
    assert rewards.next_threshold(99, [_choice(10), _choice(35)]) is None


# --- the gate an order passes through -------------------------------------


def test_an_unearned_reward_is_refused(monkeypatch):
    """Read fresh rather than trusted from the request: the browser was told
    what it could have when the page loaded, and the balance, the menu and
    the ladder can all have moved since."""
    wanted = _choice(20)
    monkeypatch.setattr(rewards, "points", lambda *_: 5)
    monkeypatch.setattr(rewards, "ladder", lambda *_: [wanted])
    with pytest.raises(errors.ApiError) as caught:
        rewards.refuse_unless_claimable(None, _user(), wanted.item_id)
    assert caught.value.status_code == 409


def test_one_message_for_every_reason_a_reward_is_unavailable(monkeypatch):
    """Not earned, withdrawn, sold out: none is actionable at checkout, and
    saying which would describe a program somebody is mid-way through
    gaming."""
    sold_out = _choice(10, available=False)
    monkeypatch.setattr(rewards, "points", lambda *_: 50)
    monkeypatch.setattr(rewards, "ladder", lambda *_: [sold_out])
    with pytest.raises(errors.ApiError) as caught:
        rewards.refuse_unless_claimable(None, _user(), sold_out.item_id)
    assert caught.value.detail["code"] == "REWARD_NOT_AVAILABLE"
    assert caught.value.detail["message"] == "That reward is not available on this order."


def test_an_earned_reward_passes_and_says_which_rung_paid_for_it(monkeypatch):
    earned = _choice(10)
    monkeypatch.setattr(rewards, "points", lambda *_: 12)
    monkeypatch.setattr(rewards, "ladder", lambda *_: [earned])
    got = rewards.refuse_unless_claimable(None, _user(), earned.item_id)
    assert got.points_required == 10


# --- when an offer is on --------------------------------------------------


def _offer(**overrides) -> Offer:
    offer = Offer(
        restaurant_id=uuid.uuid4(),
        title="Two for Tuesday",
        is_active=overrides.pop("is_active", True),
        starts_at=overrides.pop("starts_at", None),
        ends_at=overrides.pop("ends_at", None),
    )
    offer.deleted_at = overrides.pop("deleted_at", None)
    return offer


def test_an_offer_with_no_dates_is_on():
    """No start means already running, no end means until switched off."""
    assert _offer().is_live(NOW) is True


def test_an_offer_switched_off_is_not_on_whatever_its_dates_say():
    assert _offer(is_active=False).is_live(NOW) is False


def test_a_retired_offer_is_not_on():
    assert _offer(deleted_at=NOW).is_live(NOW) is False


def test_an_offer_is_not_on_before_it_starts():
    assert _offer(starts_at=NOW + timedelta(days=1)).is_live(NOW) is False


def test_an_offer_is_on_at_the_moment_it_starts():
    assert _offer(starts_at=NOW).is_live(NOW) is True


def test_an_offer_is_off_at_the_moment_it_ends():
    """Exclusive, so an offer that "ends at noon" is not on at noon -- the
    same way a shift that ends at five is not worked at five."""
    assert _offer(ends_at=NOW).is_live(NOW) is False


def test_an_offer_inside_its_window_is_on():
    assert _offer(
        starts_at=NOW - timedelta(days=1), ends_at=NOW + timedelta(days=1)
    ).is_live(NOW) is True
