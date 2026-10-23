"""Earning, winning and spending stamps.

A qualifying purchase adds stamps. Reaching the required number issues a
reward and takes those stamps back off the card. The customer picks which
dish the reward becomes when they spend it.

THREE RULES HOLD THIS TOGETHER, and all three are about not paying twice.

  Earning is idempotent by constraint. One EARN row per program per order,
  enforced by a unique index. A retried webhook, a double click or two
  workers racing all end with one credit, because the second insert fails
  rather than because a check in Python happened to win.

  Earning is atomic. The card row is locked before it is read, so two orders
  completing at the same instant cannot both read eight stamps and both
  write nine. Without the lock, a customer with one card gets two rewards
  and the restaurant finds out from its margins.

  The ledger is the truth. customer_loyalty is a cache of it. Everything
  here writes both in the same transaction, and recount() can rebuild the
  cache from the ledger -- which is what makes it a cache rather than a
  second opinion.

ONLY COMPLETED ORDERS EARN, and a refund takes the stamps back. A customer
who ordered and was refunded has not been to the restaurant, and a program
that paid out on orders that unhappened would pay for them twice over.

GUESTS EARN NOTHING, structurally: a guest users row is made per checkout
and reachable only by the cookie that made it, so there is no card to add a
stamp to. The spec asks for a logged-in customer id and this is that rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    CustomerLoyalty, Item, LoyaltyProgram, LoyaltyProgramItem, LoyaltyReward,
    LoyaltyTransaction, Order, OrderItem, OrderStatus, PurchaseType, RewardStatus,
    StampReason, User,
)
from app.models.identity import UserKind


@dataclass(frozen=True)
class Progress:
    """One card, as a customer reads it."""

    program_id: UUID
    name: str
    required_count: int
    current_count: int
    # Rewards won and not yet spent. The number matters more than the rows
    # to a progress bar, but the rows are what checkout offers.
    rewards_ready: int


@dataclass(frozen=True)
class Choice:
    """One dish a reward may be taken as."""

    item_id: UUID
    item_name: str
    image_path: str | None
    is_available: bool


def active_programs(session: Session) -> list[LoyaltyProgram]:
    """Every live program of the restaurant in session, in its own order."""
    return list(
        session.execute(
            select(LoyaltyProgram)
            .where(
                LoyaltyProgram.deleted_at.is_(None),
                LoyaltyProgram.is_active.is_(True),
            )
            .order_by(LoyaltyProgram.sort_order, LoyaltyProgram.created_at, LoyaltyProgram.id)
        ).scalars().all()
    )


def collects(user: User | None) -> bool:
    """Whether this caller has a card at all.

    A guest does not, and the reason is structural rather than a policy
    choice -- see the module docstring.
    """
    return user is not None and user.kind == UserKind.CUSTOMER.value


# ---------------------------------------------------------------- earning


def _stamps_for(session: Session, program: LoyaltyProgram, order: Order) -> int:
    """How many stamps this order earns on this program. Zero for none.

    The whole of what "qualifying" means, in one place, so the rule a
    restaurant configured is read the same way by earning and by any report
    that asks the same question later.
    """
    if program.purchase_type == PurchaseType.COMPLETED_ORDERS.value:
        # The subtotal, not the total: a delivery fee is not a purchase, and
        # a program written as "orders of ten dollars" means ten dollars of
        # food. Tax is excluded for the same reason.
        if order.subtotal_minor < program.minimum_spend_minor:
            return 0
        return 1

    if program.purchase_type == PurchaseType.MENU_ITEM.value:
        # Counted by quantity: three burgers in one order is three stamps.
        # A named dish counts itself; a named heading counts everything
        # filed under it.
        query = (
            select(func.coalesce(func.sum(OrderItem.quantity), 0))
            .where(OrderItem.order_id == order.id)
        )
        if program.qualifying_item_id is not None:
            query = query.where(OrderItem.menu_item_id == program.qualifying_item_id)
        else:
            # An order line records which dish it was, not which heading: a
            # receipt has no use for the heading and does not snapshot it.
            # So the heading is read from the dish as it stands now, which
            # means a dish moved between headings counts under where it
            # lives today. That is the reading a restaurant means by "the
            # Burgers card" -- the section, not a frozen membership list.
            query = query.join(Item, Item.id == OrderItem.menu_item_id).where(
                Item.item_type_id == program.qualifying_item_type_id
            )
        return int(session.execute(query).scalar_one() or 0)

    # A purchase type nobody taught this function about earns nothing rather
    # than guessing. The check constraint makes it unreachable from the
    # database; this is what happens if a later migration widens it and
    # forgets to come here.
    return 0


def _card(session: Session, program_id: UUID, user_id: UUID, restaurant_id: UUID) -> CustomerLoyalty:
    """This customer's card for this program, locked for update.

    Created on first use, then locked. Two orders completing at the same
    instant must not both read the same count: without this, one card pays
    out twice and the restaurant learns about it from its margins.
    """
    session.execute(
        insert(CustomerLoyalty)
        .values(
            restaurant_id=restaurant_id,
            program_id=program_id,
            customer_user_id=user_id,
            current_count=0,
        )
        .on_conflict_do_nothing(index_elements=["program_id", "customer_user_id"])
    )
    return session.execute(
        select(CustomerLoyalty)
        .where(
            CustomerLoyalty.program_id == program_id,
            CustomerLoyalty.customer_user_id == user_id,
        )
        .with_for_update()
    ).scalar_one()


def credit_order(session: Session, order: Order) -> list[LoyaltyReward]:
    """Add the stamps this completed order earns, and issue what it fills.

    Called once, where an order becomes COMPLETED. Safe to call again: the
    unique index refuses a second EARN for the same order and the attempt is
    dropped rather than raised, so a retry is a no-op instead of a 500.

    Returns the rewards issued, which is almost always none and occasionally
    one -- or two, for a customer who bought nine burgers in one go on a card
    that was already nearly full.
    """
    if order.status != OrderStatus.COMPLETED.value:
        return []
    holder = session.get(User, order.customer_user_id)
    if not collects(holder):
        return []

    issued: list[LoyaltyReward] = []
    now = datetime.now(timezone.utc)

    for program in active_programs(session):
        # A referral program is credited by whoever was brought, not by the
        # person ordering, so it is not this order's business -- see
        # services/referrals.py, which calls credit_program directly.
        if program.purchase_type == PurchaseType.REFERRAL.value:
            continue
        stamps = _stamps_for(session, program, order)
        if stamps <= 0:
            continue
        issued.extend(
            credit_program(
                session,
                program,
                customer_user_id=order.customer_user_id,
                order=order,
                stamps=stamps,
                now=now,
            )
        )

    session.flush()
    return issued


def credit_program(
    session: Session,
    program: LoyaltyProgram,
    *,
    customer_user_id: UUID,
    order: Order,
    stamps: int,
    now: datetime | None = None,
) -> list[LoyaltyReward]:
    """Put stamps on one card and issue whatever they fill.

    The whole of the earning guarantee, in one place so every kind of
    program gets it: a referral is credited through here too, which is why
    it inherits the idempotency and the lock without restating either.

    `order` is the purchase being credited -- for a referral that is the
    friend's order, not the referrer's, which is exactly what makes the
    ledger's unique index do the right thing: that order can credit this
    program once and never again.
    """
    moment = now or datetime.now(timezone.utc)
    issued: list[LoyaltyReward] = []

    # The ledger row first, and inside a savepoint: its unique index is what
    # makes this idempotent, and a duplicate must leave the rest of the
    # transaction usable rather than poisoning it.
    try:
        with session.begin_nested():
            session.add(
                LoyaltyTransaction(
                    restaurant_id=order.restaurant_id,
                    program_id=program.id,
                    customer_user_id=customer_user_id,
                    order_id=order.id,
                    stamps=stamps,
                    reason=StampReason.EARN.value,
                )
            )
    except IntegrityError:
        # Already credited. Nothing to do, and nothing wrong.
        return issued

    card = _card(session, program.id, customer_user_id, order.restaurant_id)
    card.current_count += stamps
    # A card that fills more than once in a single order pays out more than
    # once: nine burgers onto a card holding nine is two rewards, and a loop
    # rather than an if is the only honest reading of that.
    while card.current_count >= program.required_count:
        card.current_count -= program.required_count
        reward = LoyaltyReward(
            restaurant_id=order.restaurant_id,
            program_id=program.id,
            customer_user_id=customer_user_id,
            status=RewardStatus.ISSUED.value,
            issued_at=moment,
            expires_at=(
                moment + timedelta(days=program.reward_expires_days)
                if program.reward_expires_days
                else None
            ),
        )
        session.add(reward)
        issued.append(reward)
    card.updated_at = moment
    return issued


def reverse_order(session: Session, order: Order) -> None:
    """Take back the stamps a refunded order earned.

    A REVERSE row rather than deleting the EARN: what happened stays legible,
    and the count falls out of the sum either way. Floored at zero -- a card
    cannot hold negative stamps, and a customer who already spent the reward
    keeps the dish they were given. Clawing back food is not a thing a
    restaurant can do, and a negative card would quietly take the next one.
    """
    earned = session.execute(
        select(LoyaltyTransaction).where(
            LoyaltyTransaction.order_id == order.id,
            LoyaltyTransaction.reason == StampReason.EARN.value,
        )
    ).scalars().all()

    for entry in earned:
        try:
            with session.begin_nested():
                session.add(
                    LoyaltyTransaction(
                        restaurant_id=entry.restaurant_id,
                        program_id=entry.program_id,
                        customer_user_id=entry.customer_user_id,
                        order_id=entry.order_id,
                        stamps=-entry.stamps,
                        reason=StampReason.REVERSE.value,
                    )
                )
        except IntegrityError:
            continue  # already reversed

        card = _card(
            session, entry.program_id, entry.customer_user_id, entry.restaurant_id
        )
        card.current_count = max(0, card.current_count - entry.stamps)
        card.updated_at = datetime.now(timezone.utc)

    session.flush()


def recount(session: Session, program_id: UUID, user_id: UUID) -> int:
    """Rebuild one card from the ledger.

    The reason customer_loyalty is a cache and not a second truth: this can
    always be run, and what it writes is what the ledger said all along. It
    cannot see rewards already issued, so it floors at zero and is a repair
    tool rather than a routine.
    """
    total = int(
        session.execute(
            select(func.coalesce(func.sum(LoyaltyTransaction.stamps), 0)).where(
                LoyaltyTransaction.program_id == program_id,
                LoyaltyTransaction.customer_user_id == user_id,
            )
        ).scalar_one()
    )
    issued = int(
        session.execute(
            select(func.count())
            .select_from(LoyaltyReward)
            .where(
                LoyaltyReward.program_id == program_id,
                LoyaltyReward.customer_user_id == user_id,
            )
        ).scalar_one()
    )
    program = session.get(LoyaltyProgram, program_id)
    spent = issued * (program.required_count if program else 0)
    return max(0, total - spent)


# ----------------------------------------------------------- what is held


def progress(session: Session, user: User | None) -> list[Progress]:
    """Every card this customer holds at the restaurant in session.

    Programs with no card yet are included at zero: the point of the page is
    the card you have not started.
    """
    programs = active_programs(session)
    if not programs or not collects(user):
        return [
            Progress(
                program_id=p.id,
                name=p.name,
                required_count=p.required_count,
                current_count=0,
                rewards_ready=0,
            )
            for p in programs
        ]

    counts = {
        row.program_id: row.current_count
        for row in session.execute(
            select(CustomerLoyalty).where(CustomerLoyalty.customer_user_id == user.id)
        ).scalars().all()
    }
    now = datetime.now(timezone.utc)
    ready: dict[UUID, int] = {}
    for reward in _held(session, user, now):
        ready[reward.program_id] = ready.get(reward.program_id, 0) + 1

    return [
        Progress(
            program_id=p.id,
            name=p.name,
            required_count=p.required_count,
            current_count=counts.get(p.id, 0),
            rewards_ready=ready.get(p.id, 0),
        )
        for p in programs
    ]


def _held(session: Session, user: User, now: datetime) -> list[LoyaltyReward]:
    """Rewards won, unspent and not lapsed, oldest first.

    Oldest first so the one closest to expiring is offered before the rest:
    a customer holding two should spend the one about to lapse.
    """
    rows = session.execute(
        select(LoyaltyReward)
        .where(
            LoyaltyReward.customer_user_id == user.id,
            LoyaltyReward.status == RewardStatus.ISSUED.value,
        )
        .order_by(LoyaltyReward.issued_at, LoyaltyReward.id)
    ).scalars().all()
    return [row for row in rows if row.is_spendable(now)]


def spendable(session: Session, user: User | None) -> list[LoyaltyReward]:
    """What this customer could take free on an order right now."""
    if not collects(user):
        return []
    return _held(session, user, datetime.now(timezone.utc))


def choices_for(session: Session, program_id: UUID) -> list[Choice]:
    """The dishes one program's reward may be taken as."""
    rows = session.execute(
        select(LoyaltyProgramItem, Item)
        .join(Item, Item.id == LoyaltyProgramItem.item_id)
        .where(
            LoyaltyProgramItem.program_id == program_id,
            Item.deleted_at.is_(None),
        )
        .order_by(LoyaltyProgramItem.sort_order, LoyaltyProgramItem.id)
    ).all()
    return [
        Choice(
            item_id=item.id,
            item_name=item.name,
            image_path=item.image_path,
            is_available=item.is_available,
        )
        for _link, item in rows
    ]


# -------------------------------------------------------------- redeeming


def refuse_unless_spendable(
    session: Session, user: User | None, item_id: UUID
) -> LoyaltyReward:
    """The gate an order passes through to be given a dish free.

    Read fresh rather than trusted from the request: the browser was told
    what it could have when the page loaded, and a reward, a menu and a
    program can all have moved since. Returns the reward the dish will be
    charged against, which the caller marks redeemed once the order exists.
    """
    from app.core import errors

    for reward in spendable(session, user):
        for choice in choices_for(session, reward.program_id):
            if choice.item_id == item_id and choice.is_available:
                return reward
    # One message for every reason -- nothing won yet, lapsed, already
    # spent, not on this program's list, sold out. None is actionable at
    # checkout, and naming which would describe a program somebody is
    # mid-way through gaming.
    raise errors.ApiError(
        409, "REWARD_NOT_AVAILABLE", "That reward is not available on this order."
    )


def mark_redeemed(
    session: Session, reward: LoyaltyReward, order: Order, item_id: UUID
) -> None:
    """Spend a reward on an order that now exists.

    Separate from the gate because an order has to be written before it can
    be pointed at, and the two together would mean checking eligibility
    against a half-made order.
    """
    reward.status = RewardStatus.REDEEMED.value
    reward.redeemed_order_id = order.id
    reward.redeemed_item_id = item_id
    reward.redeemed_at = datetime.now(timezone.utc)
    session.flush()
