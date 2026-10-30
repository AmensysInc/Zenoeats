"""One offer, minted as numbered codes.

"MORE", ten uses, gives MORE1 through MORE10. Each code goes to one person,
and because the orders name the code they used, the restaurant can tell
which of the ten people came back -- which is the whole reason to number
them rather than print one code ten times.

WHAT THIS MODULE IS NOT. It is not a second implementation of "is this code
valid?". The codes it mints are ordinary rows in `coupons`, so the checkout
reads them through services/coupons.py exactly as it reads a hand-made code:
same row lock, same redemptions-counted-from-orders, same refusal sentences.
Nothing here is on the checkout path, and nothing here runs while a customer
is waiting.

What it owns is minting and the two questions the dashboard asks: which
codes exist, and which have been spent.
"""

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import errors
from app.db.base import utcnow
from app.models import (
    Coupon, CouponGroup, CouponGroupItemType, CouponItemType, Order,
)
from app.services import coupons as coupon_service

log = logging.getLogger(__name__)

# Matches the check constraint in 0055. These codes are handed out one at a
# time -- printed, texted, read aloud -- so a group of thousands is a typo in
# the uses field rather than a promotion, and it would write thousands of
# rows before anybody noticed.
MAX_CODES = 500

# Leaves room for the number. A coupon code is 32 characters and the longest
# suffix is three digits; what is left over is slack rather than a budget.
MAX_PREFIX = 24


def normalise_prefix(prefix: str) -> str:
    """The form a prefix is stored and compared in.

    The same rule a coupon code follows, for the same reason: what is minted
    from this has to match what a customer types, and the customer is reading
    it off a till receipt on a phone keyboard.
    """
    return "".join(prefix.split()).upper()[:MAX_PREFIX]


def code_at(prefix: str, number: int) -> str:
    """The code for one position. The whole numbering rule, in one place.

    Plain decimal with no separator and no padding: MORE1, not MORE-1 or
    MORE001. A hyphen is read aloud, written down and typed back three
    different ways; padding makes somebody wonder whether MORE07 and MORE7
    are the same code.
    """
    return f"{prefix}{number}"


@dataclass(frozen=True)
class GroupUsage:
    """How much of a group has been spent."""

    issued: int
    # Codes with at least one order against them. Not the number of orders:
    # a child is minted good for one redemption, so the two are the same
    # number today, and `used` stays the honest word if that ever changes.
    used: int
    discounted_minor: int


def codes(db: Session, group_id: UUID) -> list[Coupon]:
    """This group's codes, in the order they were minted."""
    return list(
        db.execute(
            select(Coupon)
            .where(Coupon.group_id == group_id, Coupon.deleted_at.is_(None))
            .order_by(Coupon.group_number)
        ).scalars()
    )


def usage(db: Session, group_id: UUID) -> GroupUsage:
    """Issued, spent, and what it cost.

    Counted from the orders, like every other redemption figure in this
    system: a counter on the group would be a second truth about something
    the orders already know, and it would be wrong the first time one was
    cancelled.
    """
    issued = db.execute(
        select(func.count())
        .select_from(Coupon)
        .where(Coupon.group_id == group_id, Coupon.deleted_at.is_(None))
    ).scalar_one()

    spent = db.execute(
        select(
            func.count(func.distinct(Order.coupon_id)),
            func.coalesce(func.sum(Order.coupon_discount_minor), 0),
        )
        .select_from(Order)
        .join(Coupon, Coupon.id == Order.coupon_id)
        .where(
            Coupon.group_id == group_id,
            Order.status.notin_(coupon_service.SPENT_STATUSES),
        )
    ).one()
    return GroupUsage(issued=issued, used=spent[0], discounted_minor=spent[1])


def clashes(db: Session, prefix: str, numbers: range) -> list[str]:
    """Codes this minting would collide with, if any.

    Checked before writing rather than caught afterwards, because the
    alternative is a half-minted group: the unique index would stop the one
    bad row and leave the rows before it behind. The index is still what
    guarantees it -- this is for the sentence the operator reads.
    """
    wanted = [code_at(prefix, n) for n in numbers]
    found = db.execute(
        select(Coupon.code).where(
            Coupon.code.in_(wanted), Coupon.deleted_at.is_(None)
        )
    ).scalars().all()
    return sorted(found)


def mint(db: Session, group: CouponGroup, numbers: range) -> list[Coupon]:
    """Create one coupon per number, carrying the group's offer.

    MAX_REDEMPTIONS IS ONE, always, and is not the operator's to set. The
    numbering exists so that one code means one person; codes that could
    each be spent freely would be the same code repeated, and "ten uses"
    would mean ten goes at one code rather than ten codes.

    The group's `uses` is therefore how many codes exist, and the codes
    themselves are what limits redemption. There is no total-redemptions
    counter anywhere, and nothing needs one.
    """
    minted: list[Coupon] = []
    for number in numbers:
        coupon = Coupon(
            restaurant_id=group.restaurant_id,
            code=code_at(group.code_prefix, number),
            description=group.description,
            discount_kind=group.discount_kind,
            discount_value=group.discount_value,
            max_discount_minor=group.max_discount_minor,
            min_subtotal_minor=group.min_subtotal_minor,
            starts_at=group.starts_at,
            ends_at=group.ends_at,
            max_redemptions=1,
            max_per_customer=group.max_per_customer,
            is_active=group.is_active,
            group_id=group.id,
            group_number=number,
        )
        db.add(coupon)
        minted.append(coupon)
    db.flush()
    return minted


def copy_scope(db: Session, group: CouponGroup, minted: list[Coupon], headings: list[UUID]) -> None:
    """Give every minted code the group's "applies to" headings.

    Copied onto each child rather than read from the group at checkout. The
    scope has to live where services/coupons.py looks for it, and it looks on
    the coupon -- which is what keeps a group's code and a hand-made code
    being the same kind of thing to everything downstream.
    """
    if not headings:
        return
    for coupon in minted:
        for heading_id in headings:
            db.add(
                CouponItemType(
                    restaurant_id=group.restaurant_id,
                    coupon_id=coupon.id,
                    item_type_id=heading_id,
                )
            )
    db.flush()


def scope_of(db: Session, group_id: UUID) -> list[UUID]:
    """The headings this group's offer applies to.

    Held on the group because a sign-up offer has no codes until its first
    customer arrives, and "burgers only" has to live somewhere before then.
    Each minted code gets its own copy, so nothing downstream has to know
    the group exists.
    """
    return list(
        db.execute(
            select(CouponGroupItemType.item_type_id).where(
                CouponGroupItemType.group_id == group_id
            )
        ).scalars()
    )


def set_scope(db: Session, group: CouponGroup, headings: list[UUID]) -> None:
    """Record what the group's offer applies to. Replaces whatever was there."""
    db.execute(
        CouponGroupItemType.__table__.delete().where(
            CouponGroupItemType.group_id == group.id
        )
    )
    for heading_id in dict.fromkeys(headings):
        db.add(
            CouponGroupItemType(
                restaurant_id=group.restaurant_id,
                group_id=group.id,
                item_type_id=heading_id,
            )
        )
    db.flush()


def set_active(db: Session, group: CouponGroup, active: bool) -> None:
    """Switch the whole group, codes and all.

    A paused group whose codes still worked would be a pause that paused
    nothing: the codes are what customers hold, and the group is only where
    they came from.
    """
    group.is_active = active
    db.execute(
        Coupon.__table__.update()
        .where(Coupon.group_id == group.id, Coupon.deleted_at.is_(None))
        .values(is_active=active)
    )


def retire(db: Session, group: CouponGroup) -> None:
    """Soft-delete the group and every code it minted.

    Soft, like retiring a coupon: orders point at these rows and keep their
    own snapshot of the code and the amount, so a receipt from last month
    still reads the same. What retiring buys is that the codes stop working
    and the prefix can be used again next season.
    """
    now = utcnow()
    group.deleted_at = now
    group.is_active = False
    db.execute(
        Coupon.__table__.update()
        .where(Coupon.group_id == group.id, Coupon.deleted_at.is_(None))
        .values(deleted_at=now, is_active=False)
    )


def validate_prefix(prefix: str) -> str:
    """The prefix as it will be stored, or a refusal naming the rule broken.

    Letters and digits only, like a coupon code -- but a prefix that ended in
    a digit would be genuinely ambiguous rather than merely ugly: MORE2 with
    twelve uses mints MORE21 and MORE212, and MORE21 is also what MORE2's
    first code would be called. Refused rather than resolved, because any
    resolution would surprise somebody holding a printed code.
    """
    cleaned = normalise_prefix(prefix)
    if len(cleaned) < 2:
        raise errors.validation_error("A prefix needs at least two characters.")
    if not cleaned.isalnum():
        raise errors.validation_error("A prefix can only contain letters and numbers.")
    if cleaned[-1].isdigit():
        raise errors.validation_error(
            "A prefix cannot end in a number, because the codes end in one: "
            f"{cleaned} would mint {code_at(cleaned, 1)}, which could also be "
            f"{cleaned[:-1]} number {cleaned[-1]}1."
        )
    return cleaned
