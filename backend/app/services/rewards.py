"""The rewards program: what a customer has earned, and what it is worth.

A point is an order. Not a stored number -- the balance is the count of
orders that customer has completed at this restaurant, which the orders table
already knows. A column beside it would be a second truth about the same
fact, and the two would part company the first time an order was cancelled.
That is the rule coupon redemptions already follow, and it is the reason a
refund or a cancellation needs no code here at all: the order stops counting
because it stopped being completed.

ONLY COMPLETED ORDERS COUNT. Not paid, not preparing: completed. A customer
whose order is cancelled before it is made has not been to the restaurant,
and a program that paid out before the food was handed over would pay for
orders that never happened.

REACHING A THRESHOLD UNLOCKS IT FOR GOOD. Nothing is deducted, so the
question a reward answers is "is my balance at least this?" rather than "can
I afford this?". The limit that keeps that sustainable is one free dish per
order, which lives on the order (orders.reward_item_id) because it is a fact
about a visit rather than about a balance.

A GUEST EARNS NOTHING, and the reason is structural rather than a policy: a
guest users row is created per checkout and reachable only by the cookie that
made it, so there is no identity to accrue against. The storefront is
expected to say so, which is why the progress read answers for an anonymous
caller instead of refusing them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Item, Order, OrderStatus, Reward, User
from app.models.identity import UserKind


@dataclass(frozen=True)
class RewardChoice:
    """One dish at one threshold, as a customer needs to see it."""

    id: UUID
    item_id: UUID
    item_name: str
    points_required: int
    image_path: str | None
    # Whether the kitchen can actually serve it right now. A reward on a
    # sold-out dish is still shown -- it is part of the program, and hiding
    # it would make the ladder appear to change -- but it cannot be claimed.
    is_available: bool


def points(session: Session, user: User | None) -> int:
    """How many points this customer has at the tenant in session.

    Counted, never read from a column. The session is already scoped to one
    restaurant, so this is "their completed orders here".
    """
    if user is None or user.kind == UserKind.GUEST.value:
        return 0
    return int(
        session.execute(
            select(func.count())
            .select_from(Order)
            .where(
                Order.customer_user_id == user.id,
                Order.status == OrderStatus.COMPLETED.value,
            )
        ).scalar_one()
    )


def ladder(session: Session) -> list[RewardChoice]:
    """Every live reward of this restaurant, lowest threshold first.

    One query with the dish joined: the names and the pictures are what the
    customer reads, and a reward whose dish has been deleted is not a reward
    anybody can be given.
    """
    rows = session.execute(
        select(Reward, Item)
        .join(Item, Item.id == Reward.item_id)
        .where(
            Reward.deleted_at.is_(None),
            Reward.is_active.is_(True),
            Item.deleted_at.is_(None),
        )
        .order_by(Reward.points_required, Reward.sort_order, Reward.created_at, Reward.id)
    ).all()
    return [
        RewardChoice(
            id=reward.id,
            item_id=item.id,
            item_name=item.name,
            points_required=reward.points_required,
            image_path=item.image_path,
            is_available=item.is_available,
        )
        for reward, item in rows
    ]


def thresholds(choices: list[RewardChoice]) -> list[int]:
    """The rungs of the ladder, in order and without repeats.

    Derived from the rewards rather than configured: a threshold exists
    because some dish names it, so the ladder cannot show a rung that offers
    nothing.
    """
    seen: dict[int, None] = {}
    for choice in choices:
        seen.setdefault(choice.points_required, None)
    return sorted(seen)


def next_threshold(balance: int, choices: list[RewardChoice]) -> int | None:
    """The next rung above the balance, or None when they are all reached."""
    return next((rung for rung in thresholds(choices) if rung > balance), None)


def claimable(balance: int, choices: list[RewardChoice]) -> list[RewardChoice]:
    """What this balance may actually be given, free, right now.

    Every reward at or below the balance, because a threshold once reached
    stays reached -- a customer at fifty points still has what ten offered.
    A dish the kitchen has run out of is not claimable even though it stays
    on the ladder.
    """
    return [c for c in choices if c.points_required <= balance and c.is_available]


def refuse_unless_claimable(
    session: Session, user: User | None, item_id: UUID
) -> RewardChoice:
    """The gate an order passes through to be given a dish free.

    Read fresh from the database rather than trusted from the request: the
    browser was told what it could have when the page loaded, and a balance,
    a menu and a ladder can all have moved since. Returns the reward so the
    caller can say which threshold paid for it.
    """
    from app.core import errors

    balance = points(session, user)
    allowed = claimable(balance, ladder(session))
    match = next((c for c in allowed if c.item_id == item_id), None)
    if match is None:
        # Deliberately one message for every reason -- not earned yet, no
        # longer on the ladder, sold out. None of them is actionable at
        # checkout, and spelling out which would describe a program the
        # customer is mid-way through gaming.
        raise errors.ApiError(
            409, "REWARD_NOT_AVAILABLE", "That reward is not available on this order."
        )
    return match


def live_offers(session: Session, now: datetime | None = None) -> list:
    """The offers a customer should see at this moment, in the restaurant's
    own order.

    The window is read by Offer.is_live so that the storefront and the admin
    list agree about what "running" means.
    """
    from app.models import Offer

    moment = now or datetime.now(timezone.utc)
    rows = session.execute(
        select(Offer)
        .where(Offer.deleted_at.is_(None))
        .order_by(Offer.sort_order, Offer.created_at, Offer.id)
    ).scalars().all()
    return [offer for offer in rows if offer.is_live(moment)]
