"""Whether a code works, and what it takes off.

One module, because "is this coupon valid?" is asked in three places and
must not be answered three ways: the quote the browser shows, the order that
is actually created, and the dashboard listing a manager reads. A coupon
accepted by the quote and refused at checkout is worse than one refused
outright -- the customer has already decided to buy.

WHAT A CUSTOMER IS TOLD. Each refusal carries its own sentence, because they
are not the same problem and the customer can act on three of them: a code
with a typo should be retyped, a minimum spend can be reached by adding
something, and a window that has not opened yet is worth coming back for.
What they are never told is anything they could not have worked out from
their own cart -- not how many redemptions remain, not what the limits are on
a code they do not have, and not whether a code they guessed exists but is
switched off. An unknown code and a deactivated one answer identically, so
the error cannot be used to go fishing for codes.

WHAT IT APPLIES TO is the food, after any meal deals, and never the delivery
fee or the tax. A coupon that took money off a delivery fee would be a
restaurant paying a driver out of a promotion, and one applied before the
combos would discount a price nobody was being charged.

A coupon may also be narrower than that: "20% off burgers" names the
headings it applies to, and then only the lines under those headings are
discounted. Two different numbers come into it, and keeping them apart is
the whole of the arithmetic here:

    the minimum spend   is about the order -- "spend $20 to use this"
    the discount basis  is about the eligible part -- 20% of the burgers

Measuring the minimum against the eligible part instead would mean "spend
$20 on burgers", which is a different offer and not the one anybody typed.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import errors
from app.core.money import apply_rate_bps
from app.models import Coupon, CouponItemType, DiscountKind, Order, OrderStatus

log = logging.getLogger(__name__)

# The code as it travels: upper case, no spaces anywhere inside. Long enough
# for a readable promotion name, short enough to type on a phone.
MAX_CODE_LENGTH = 32

# An order in one of these never happened, commercially: it was never paid
# for, or it was cancelled and refunded. Counting it as a redemption would
# let an abandoned checkout use up a coupon with a hundred-order limit, and
# an unpaid cart is not a promotion anybody received.
SPENT_STATUSES = (OrderStatus.EXPIRED.value, OrderStatus.CANCELLED.value)

# What a customer is told when the code itself is the problem -- it does not
# exist, or it has been switched off, which read identically on purpose (see
# the module docstring). It asks for the one thing they can do about it.
#
# The other refusals below say what is specifically wrong instead, because a
# customer can act on those differently: a minimum spend can be reached by
# adding something, and a window that has not opened yet is worth coming back
# for. Telling all of them to "check the code" would send somebody hunting
# for a typo in a code they typed correctly.
UNKNOWN = "Please check again and re-enter the correct coupon code."


class CouponRefused(errors.ApiError):
    """The code cannot be used on this cart, and here is the sentence why.

    A 422 rather than a 404: the request was well-formed and the answer is
    about the cart, which is exactly what the customer can change. The code
    `COUPON_REFUSED` is what the storefront keys on to show the message
    beside the field rather than as a page-level failure.
    """

    def __init__(self, message: str):
        super().__init__(422, "COUPON_REFUSED", message)


@dataclass(frozen=True)
class AppliedCoupon:
    """A coupon that works on this cart, and what it takes off."""

    coupon_id: UUID
    code: str
    # The manager's words for the promotion, for the line on the receipt.
    # None when they did not give it any.
    label: str | None
    discount_minor: int


def normalise(code: str) -> str:
    """The form a code is stored and compared in.

    Upper case and with every space removed, not merely trimmed: a customer
    reading a code off a poster types "SAVE 10" about as often as "SAVE10",
    and both are the same coupon. Doing this in one function is what
    guarantees the lookup and the row agree.
    """
    return "".join(code.split()).upper()[:MAX_CODE_LENGTH]


def discount_for(coupon: Coupon, base_minor: int) -> int:
    """What this coupon takes off `base_minor`.

    Never more than the base: a flat $10 off a $6 order is $6 off, not a
    refund of the difference. PERCENT goes through the same basis-point
    helper as tax and combo discounts, so every number on a receipt rounds
    the same way -- HALF_UP, on Decimal, never a float.
    """
    if base_minor <= 0:
        return 0

    if coupon.discount_kind == DiscountKind.PERCENT.value:
        amount = apply_rate_bps(base_minor, coupon.discount_value)
        if coupon.max_discount_minor is not None:
            amount = min(amount, coupon.max_discount_minor)
    elif coupon.discount_kind == DiscountKind.AMOUNT.value:
        amount = max(coupon.discount_value, 0)
    else:
        # NONE, or anything a future kind adds that this does not know how to
        # price. Taking nothing off is the only safe reading.
        return 0

    return min(amount, base_minor)


def find(db: Session, code: str) -> Coupon | None:
    """The live coupon with this code, or None.

    Scoped by RLS to the current tenant, which is what makes two restaurants
    able to run the same code. Soft-deleted rows are not found: the unique
    index only covers the live ones, so there may be several and only one of
    them is the coupon anybody means.
    """
    normalised = normalise(code)
    if not normalised:
        return None
    return db.execute(
        select(Coupon).where(
            Coupon.code == normalised,
            Coupon.deleted_at.is_(None),
        )
    ).scalar_one_or_none()


def redemptions(db: Session, coupon_id: UUID) -> int:
    """How many orders have used this coupon.

    Counted from the orders rather than kept as a number on the coupon. A
    counter would be a second truth about something the orders already know,
    and it would be wrong the first time one was cancelled.
    """
    return db.execute(
        select(func.count())
        .select_from(Order)
        .where(Order.coupon_id == coupon_id, Order.status.notin_(SPENT_STATUSES))
    ).scalar_one()


def eligible_base(db: Session, coupon: Coupon, lines: list[tuple[UUID | None, int]]) -> int:
    """How much of a cart this coupon is allowed to reduce.

    `lines` is (item_type_id, amount) per priced line, already net of any
    meal-deal discount -- the caller allocates that, because the combos take
    money off the cart rather than off a line.

    A coupon with no headings applies to all of it. One with headings applies
    to the lines filed under them, and a dish whose heading is not named
    contributes nothing: that is what makes "20% off burgers" cost 20% of the
    burgers rather than 20% of the order.
    """
    scope = {
        row
        for row in db.execute(
            select(CouponItemType.item_type_id).where(
                CouponItemType.coupon_id == coupon.id
            )
        ).scalars()
    }
    if not scope:
        return sum(amount for _type_id, amount in lines)
    return sum(amount for type_id, amount in lines if type_id in scope)


def apply(
    db: Session,
    code: str,
    *,
    subtotal_minor: int,
    eligible_minor: int | None = None,
    customer_user_id: UUID | None,
    lock: bool = False,
) -> AppliedCoupon:
    """Price `code` against a cart, or raise CouponRefused saying why.

    `subtotal_minor` is the food after any meal-deal discounts, and is what
    the minimum spend is measured against: a minimum is a fact about the
    order.

    `eligible_minor` is the part of that the coupon may actually reduce --
    smaller than the subtotal when the coupon names headings. Defaulted to
    the subtotal, so a caller that has no scope to work out need not say the
    same number twice.

    `lock` takes the coupon's row for the rest of the transaction, and is for
    the one caller that is about to create an order. Two customers reaching
    checkout at the same moment with the last redemption of a limited coupon
    would otherwise both be told yes: each counts the orders before either
    has written one. Locking makes the second wait for the first, read the
    count it created, and be refused. The quote does not lock -- it writes
    nothing, and holding a row while a browser thinks would serialise every
    storefront on one popular code.
    """
    coupon = find(db, code)
    if coupon is None:
        raise CouponRefused(UNKNOWN)

    if lock:
        # Re-read under a row lock, then re-check everything below with the
        # count that lock makes trustworthy.
        db.execute(select(Coupon.id).where(Coupon.id == coupon.id).with_for_update())
        db.refresh(coupon)

    # Switched off reads exactly like a code that does not exist. A manager
    # pausing a promotion has not published a new fact about it.
    if not coupon.is_active:
        raise CouponRefused(UNKNOWN)

    now = datetime.now(timezone.utc)
    if coupon.starts_at is not None and now < coupon.starts_at:
        raise CouponRefused("That code isn't active yet.")
    if coupon.ends_at is not None and now >= coupon.ends_at:
        raise CouponRefused("That code has expired.")

    if subtotal_minor < coupon.min_subtotal_minor:
        # The one refusal that says a number, because it is the one the
        # customer can act on: it is a fact about their own cart.
        short = _money(coupon.min_subtotal_minor)
        raise CouponRefused(f"That code needs an order of {short} or more.")

    if coupon.max_redemptions is not None:
        if redemptions(db, coupon.id) >= coupon.max_redemptions:
            raise CouponRefused("That code has been fully redeemed.")

    if coupon.max_per_customer is not None and customer_user_id is not None:
        used = db.execute(
            select(func.count())
            .select_from(Order)
            .where(
                Order.coupon_id == coupon.id,
                Order.customer_user_id == customer_user_id,
                Order.status.notin_(SPENT_STATUSES),
            )
        ).scalar_one()
        if used >= coupon.max_per_customer:
            raise CouponRefused(
                "You've already used that code."
                if coupon.max_per_customer == 1
                else "You've used that code as many times as it allows."
            )

    basis = subtotal_minor if eligible_minor is None else eligible_minor
    if basis <= 0 and subtotal_minor > 0:
        # The code is good and this cart has nothing it applies to: a burger
        # coupon against an order of milkshakes. Said as its own sentence,
        # because unlike the refusals above it is not about the code at all.
        raise CouponRefused("That code doesn't apply to anything in your order.")

    discount = discount_for(coupon, basis)
    if discount <= 0:
        # The coupon is valid and worth nothing on this cart -- a flat amount
        # against a cart that combos have already taken to zero. Saying
        # "applied" and changing no number is the one answer that would look
        # like a bug to the customer.
        raise CouponRefused("That code takes nothing off this order.")

    return AppliedCoupon(
        coupon_id=coupon.id,
        code=coupon.code,
        label=coupon.description,
        discount_minor=discount,
    )


def _money(minor: int) -> str:
    """A minimum spend, for the one message that quotes one.

    Currency-symbol free on purpose: the restaurant's currency is on every
    other number on the page, and a service that guessed "$" would be wrong
    for the first restaurant that is not in dollars.
    """
    return f"{minor // 100}.{minor % 100:02d}"
