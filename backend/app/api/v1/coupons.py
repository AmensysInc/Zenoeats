"""The coupons dashboard: create a code, change it, retire it, see its use.

Its own module rather than another section of restaurant.py, which is four
and a half thousand lines and holds the menu builder, the order board, the
staff list and the delivery settings. A promotion is none of those.

WHO MAY DO WHAT. Reading is MENU_VIEW -- admin, manager, and IT support, the
same three who may look at the menu without being able to change it. Writing
is MANAGE: admin and manager only. Support can see that STUDENT10 exists and
has been used forty times, which is what answering a question about it needs,
and cannot invent a discount.

WHAT IS NOT HERE is a delete that deletes. Retiring a coupon is a soft
delete, because orders point at it and a manager asking "what was WELCOME10"
six months from now deserves an answer. The code becomes available again
immediately -- the unique index covers only the live rows -- so a seasonal
promotion can be retired in the autumn and created again in the spring.
"""

import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import StaffDb, current_restaurant_staff, require_staff
from app.core import errors
from app.db.base import utcnow
from app.models import Coupon, DiscountKind, Order, Restaurant, StaffRole
from app.services import coupons as coupon_service

log = logging.getLogger(__name__)
router = APIRouter(prefix="/restaurant", tags=["coupons"])

# The same two lists restaurant.py keeps, for the same two reasons. Repeated
# rather than imported: importing a module of that size to borrow two
# constants would pull the whole order board into this one's import graph.
MANAGE = require_staff(StaffRole.ADMIN, StaffRole.MANAGER)
MENU_VIEW = require_staff(StaffRole.ADMIN, StaffRole.MANAGER, StaffRole.IT_SUPPORT)

# Enough for every promotion a restaurant can keep track of, and a bound on
# what one page has to render.
MAX_COUPONS = 200


class CouponIn(BaseModel):
    """A new coupon.

    Only the code and the discount are required. Every limit is optional
    because a real promotion uses some and not others -- a launch offer has
    an end date and no cap, a referral code has a cap of one per customer and
    no end date at all.
    """

    code: str = Field(min_length=3, max_length=32)
    description: str | None = Field(default=None, max_length=160)
    # PERCENT is basis points (1000 = 10%), AMOUNT is minor units (500 =
    # $5.00) -- the same reading as a combo's discount, which is where a
    # manager has met these two words before.
    discount_kind: DiscountKind = DiscountKind.PERCENT
    discount_value: int = Field(gt=0)
    max_discount_minor: int | None = Field(default=None, gt=0)
    min_subtotal_minor: int = Field(default=0, ge=0)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    max_redemptions: int | None = Field(default=None, gt=0)
    max_per_customer: int | None = Field(default=None, gt=0)
    is_active: bool = True

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        """Normalised here, once, in the form it is stored and compared in.

        A manager typing "save 10" and a customer typing "SAVE10" have the
        same coupon in mind, and the service that looks it up normalises the
        same way.
        """
        code = coupon_service.normalise(value)
        if len(code) < 3:
            raise ValueError("A code needs at least three characters.")
        if not code.isalnum():
            # Letters and digits only. A code with a hyphen or a space in it
            # is read aloud, written down and typed back three different
            # ways, and every one of them has to match.
            raise ValueError("A code can only contain letters and numbers.")
        return code

    @field_validator("description")
    @classmethod
    def _described(cls, value: str | None) -> str | None:
        return (value or "").strip() or None

    @model_validator(mode="after")
    def _coherent(self):
        """The rules a single field cannot see on its own."""
        if self.discount_kind == DiscountKind.NONE:
            raise ValueError("Choose a percentage or an amount off.")
        if self.discount_kind == DiscountKind.PERCENT and self.discount_value > 10000:
            raise ValueError("A percentage cannot be more than 100%.")
        if (
            self.discount_kind == DiscountKind.AMOUNT
            and self.max_discount_minor is not None
        ):
            # A ceiling on a flat amount is the flat amount, or it is lower
            # than it and the amount was a lie. Either way it is not a
            # setting; accepting it would mean a coupon whose stated value is
            # not what it takes off.
            raise ValueError("A ceiling only applies to a percentage discount.")
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("The end has to come after the start.")
        if (
            self.max_redemptions is not None
            and self.max_per_customer is not None
            and self.max_per_customer > self.max_redemptions
        ):
            raise ValueError("Per-customer uses cannot exceed the total.")
        return self


class CouponPatch(BaseModel):
    """An edit. Every field optional; the code is not one of them.

    The code is what customers have been given, printed on something, or
    told over the phone. Changing it would break every copy of it in the
    world while the coupon went on existing, so a new code is a new coupon
    and the old one gets retired.
    """

    description: str | None = Field(default=None, max_length=160)
    discount_kind: DiscountKind | None = None
    discount_value: int | None = Field(default=None, gt=0)
    max_discount_minor: int | None = Field(default=None, ge=0)
    min_subtotal_minor: int | None = Field(default=None, ge=0)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    max_redemptions: int | None = Field(default=None, ge=0)
    max_per_customer: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class CouponOut(BaseModel):
    id: UUID
    code: str
    description: str | None
    discount_kind: str
    discount_value: int
    max_discount_minor: int | None
    min_subtotal_minor: int
    starts_at: datetime | None
    ends_at: datetime | None
    max_redemptions: int | None
    max_per_customer: int | None
    is_active: bool
    # How many orders have used it, counted from the orders themselves.
    redemptions: int
    # What those orders took off in total, which is the number anybody
    # judging a promotion actually wants: a code used forty times for ten
    # cents each is not the same promotion as one used twice for fifty
    # dollars.
    discounted_minor: int
    created_at: datetime


def _out(coupon: Coupon, redemptions: int, discounted: int) -> CouponOut:
    return CouponOut(
        id=coupon.id,
        code=coupon.code,
        description=coupon.description,
        discount_kind=coupon.discount_kind,
        discount_value=coupon.discount_value,
        max_discount_minor=coupon.max_discount_minor,
        min_subtotal_minor=coupon.min_subtotal_minor,
        starts_at=coupon.starts_at,
        ends_at=coupon.ends_at,
        max_redemptions=coupon.max_redemptions,
        max_per_customer=coupon.max_per_customer,
        is_active=coupon.is_active,
        redemptions=redemptions,
        discounted_minor=discounted,
        created_at=coupon.created_at,
    )


class CouponListOut(BaseModel):
    """The coupons, and the currency every amount in them is in.

    An envelope rather than a bare list because the screen needs both and
    only one of them is a coupon. The alternative was for the dashboard to
    read /restaurant/profile for the currency, which is admin-only -- a
    manager, who may run promotions, cannot read it, and the page would have
    shown "Not authorized" beside a perfectly good list of coupons.
    """

    currency: str
    coupons: list[CouponOut]


@router.get("/coupons", response_model=CouponListOut)
def list_coupons(
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MENU_VIEW),
):
    """Every live coupon, newest first, with what each has been used for.

    The counts come from one grouped query over the orders rather than one
    query per coupon: a dashboard of thirty promotions used to be thirty
    round trips, every time the page was opened.
    """
    rows = db.execute(
        select(Coupon)
        .where(Coupon.deleted_at.is_(None))
        .order_by(Coupon.created_at.desc(), Coupon.id)
        .limit(MAX_COUPONS)
    ).scalars().all()

    used: dict[UUID, tuple[int, int]] = {}
    if rows:
        counted = db.execute(
            select(
                Order.coupon_id,
                func.count(),
                func.coalesce(func.sum(Order.coupon_discount_minor), 0),
            )
            .where(
                Order.coupon_id.in_([coupon.id for coupon in rows]),
                Order.status.notin_(coupon_service.SPENT_STATUSES),
            )
            .group_by(Order.coupon_id)
        ).all()
        used = {row[0]: (row[1], row[2]) for row in counted}

    return CouponListOut(
        currency=restaurant.currency,
        coupons=[_out(coupon, *used.get(coupon.id, (0, 0))) for coupon in rows],
    )


@router.post("/coupons", response_model=CouponOut, status_code=201)
def create_coupon(
    body: CouponIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    """Create a coupon. It works from the moment it is saved, unless it
    carries a start date or was saved switched off."""
    live = db.execute(
        select(func.count()).select_from(Coupon).where(Coupon.deleted_at.is_(None))
    ).scalar_one()
    if live >= MAX_COUPONS:
        raise errors.validation_error(
            f"That is {MAX_COUPONS} coupons. Retire one before adding another."
        )

    # Checked here for the message. The partial unique index is what actually
    # guarantees it, and it covers the live rows only -- a retired code may be
    # created again.
    clash = coupon_service.find(db, body.code)
    if clash is not None:
        raise errors.validation_error(f"{body.code} already exists.")

    coupon = Coupon(
        restaurant_id=restaurant.id,
        code=body.code,
        description=body.description,
        discount_kind=body.discount_kind.value,
        discount_value=body.discount_value,
        max_discount_minor=body.max_discount_minor,
        min_subtotal_minor=body.min_subtotal_minor,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        max_redemptions=body.max_redemptions,
        max_per_customer=body.max_per_customer,
        is_active=body.is_active,
    )
    db.add(coupon)
    db.flush()
    log.info("coupon %s created for restaurant %s", coupon.code, restaurant.id)
    return _out(coupon, 0, 0)


@router.patch("/coupons/{coupon_id}", response_model=CouponOut)
def update_coupon(
    coupon_id: UUID,
    body: CouponPatch,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    """Edit a coupon, except its code.

    A limit may be lowered below what has already been redeemed, and that is
    deliberate: it is how a manager stops a promotion that is running away
    without retiring the code customers are holding. The orders already placed
    keep their discount -- they were charged that way -- and the next person to
    try the code is told it has been fully redeemed.
    """
    coupon = _find(db, coupon_id)
    sent = body.model_dump(exclude_unset=True)

    if "description" in sent:
        coupon.description = (sent["description"] or "").strip() or None

    # Read together: a value means nothing without its kind, so changing one
    # has to fall back on the other as it is stored.
    if "discount_kind" in sent or "discount_value" in sent:
        kind = DiscountKind(sent.get("discount_kind") or coupon.discount_kind)
        value = sent.get("discount_value") or coupon.discount_value
        if kind == DiscountKind.NONE:
            raise errors.validation_error("Choose a percentage or an amount off.")
        if kind == DiscountKind.PERCENT and value > 10000:
            raise errors.validation_error("A percentage cannot be more than 100%.")
        coupon.discount_kind = kind.value
        coupon.discount_value = value

    if "max_discount_minor" in sent:
        # Zero is how a form clears a ceiling: an empty number input sends
        # nothing useful, and "no ceiling" has to be expressible.
        ceiling = sent["max_discount_minor"] or None
        if ceiling is not None and coupon.discount_kind != DiscountKind.PERCENT.value:
            raise errors.validation_error("A ceiling only applies to a percentage discount.")
        coupon.max_discount_minor = ceiling

    if "min_subtotal_minor" in sent:
        coupon.min_subtotal_minor = sent["min_subtotal_minor"] or 0
    if "starts_at" in sent:
        coupon.starts_at = sent["starts_at"]
    if "ends_at" in sent:
        coupon.ends_at = sent["ends_at"]
    if "max_redemptions" in sent:
        coupon.max_redemptions = sent["max_redemptions"] or None
    if "max_per_customer" in sent:
        coupon.max_per_customer = sent["max_per_customer"] or None
    if sent.get("is_active") is not None:
        coupon.is_active = sent["is_active"]

    if coupon.starts_at and coupon.ends_at and coupon.ends_at <= coupon.starts_at:
        raise errors.validation_error("The end has to come after the start.")

    coupon.updated_at = utcnow()
    db.flush()
    return _out(coupon, *_usage(db, coupon.id))


@router.delete("/coupons/{coupon_id}", status_code=204)
def retire_coupon(
    coupon_id: UUID,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    """Retire a coupon. Soft, and the code becomes free again.

    Orders that used it keep their own snapshot of the code and the amount,
    so a receipt from last month still reads the same. The row stays for the
    same reason: a question about a promotion is asked after it has ended.
    """
    coupon = _find(db, coupon_id)
    coupon.deleted_at = utcnow()
    coupon.is_active = False
    db.flush()
    log.info("coupon %s retired for restaurant %s", coupon.code, restaurant.id)


def _find(db: Session, coupon_id: UUID) -> Coupon:
    coupon = db.get(Coupon, coupon_id)
    if coupon is None or coupon.deleted_at is not None:
        # RLS has already made another restaurant's coupon invisible, so
        # "not found" is the whole truth here.
        raise errors.validation_error("No such coupon.")
    return coupon


def _usage(db: Session, coupon_id: UUID) -> tuple[int, int]:
    row = db.execute(
        select(func.count(), func.coalesce(func.sum(Order.coupon_discount_minor), 0))
        .select_from(Order)
        .where(
            Order.coupon_id == coupon_id,
            Order.status.notin_(coupon_service.SPENT_STATUSES),
        )
    ).first()
    return (row[0], row[1]) if row else (0, 0)
