"""Coupons, created by the platform rather than by the restaurant.

A promotion code is a Zenoeats decision here, not a tenant's: the people who
run the platform set them up on a restaurant's behalf, and the restaurant's
own portal has no coupon screen at all. Which is why this is mounted on the
admin router, behind require_platform_admin, on admin.<root domain>.

THE CUSTOMER SIDE IS UNAFFECTED by that choice. A code is typed at checkout
and priced by services/coupons.py under the ordinary tenant session, like
every other thing a customer's cart touches. This module is only where
coupons come from.

ON THE SYSTEM SESSION, which is the one thing to be careful about in here.
Every other router in this codebase runs under a tenant session, where
row-level security has already decided which restaurant's rows exist: a
forgotten WHERE clause returns nothing rather than somebody else's data. This
one does not. A platform admin is a member of no restaurant, so these
endpoints run on the declared cross-tenant surface, where the policy for
zenoeats_system is `USING (true)` -- it can see every coupon of every
restaurant.

So every query below names its restaurant_id explicitly, and that predicate
is the whole of the isolation. It is not belt and braces as it is elsewhere;
it is the belt. A query here that filters by coupon id alone would happily
edit another restaurant's promotion.

EVERY WRITE IS AUDITED, like every other super-admin action: a discount is
money, and the log records which operator created, changed or retired it.
"""

import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select, text

from app.api.deps import require_platform_admin
from app.core import errors
from app.db.base import utcnow
from app.db.session import system_session
from app.models import Coupon, DiscountKind, Order, User
from app.services import coupons as coupon_service

# The audit writer the rest of the admin surface uses. Imported rather than
# copied: two places writing platform_audit_logs is two places to get its
# shape wrong, and the shape is what makes the log worth keeping.
from app.api.v1.admin import _audit

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["coupons"])

# Enough for every promotion one restaurant can keep track of, and a bound on
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
    # $5.00) -- the same reading as a combo's discount.
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

        An operator typing "save 10" and a customer typing "SAVE10" have the
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
            # A ceiling on a flat amount is either the amount itself or lower
            # than it, in which case the amount was a lie. Either way it is
            # not a setting.
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


class CouponListOut(BaseModel):
    """The coupons, and what restaurant they belong to.

    The name and currency ride along because the screen shows both and
    neither is a coupon: a page of percentages with no currency on it cannot
    render "$5.00 off", and an operator who has several restaurants open
    needs to see which one they are editing.
    """

    restaurant_name: str
    currency: str
    coupons: list[CouponOut]


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


def _restaurant(session, restaurant_id: UUID):
    """The restaurant these coupons belong to, or 404.

    Read rather than taken on trust, so a coupon can never be created against
    an id that is not a live restaurant: the foreign key would refuse that
    anyway, with a message nobody could act on.

    Named columns rather than the mapped class, as the rest of the admin
    surface does: zenoeats_system holds column-level grants, and asking for a
    column outside them is refused by Postgres. Two is all this needs.
    """
    found = session.execute(
        text(
            "SELECT name, currency FROM restaurants "
            "WHERE id = :rid AND deleted_at IS NULL"
        ),
        {"rid": str(restaurant_id)},
    ).mappings().one_or_none()
    if found is None:
        raise errors.ApiError(404, "RESTAURANT_NOT_FOUND", "No such restaurant.")
    return found


def _usage(session, restaurant_id: UUID, coupon_ids: list[UUID]) -> dict:
    """How many orders used each coupon, and what they took off.

    One grouped query however many coupons there are: a page of thirty
    promotions used to be thirty round trips every time it was opened.

    Scoped by restaurant_id as well as by coupon id. The ids came from this
    restaurant's own coupons, so the extra predicate changes no answer -- it
    is here because on this session a query that forgets it is a query that
    reads every tenant, and that habit is the only thing keeping the next one
    correct.
    """
    if not coupon_ids:
        return {}
    rows = session.execute(
        select(
            Order.coupon_id,
            func.count(),
            func.coalesce(func.sum(Order.coupon_discount_minor), 0),
        )
        .where(
            Order.restaurant_id == restaurant_id,
            Order.coupon_id.in_(coupon_ids),
            Order.status.notin_(coupon_service.SPENT_STATUSES),
        )
        .group_by(Order.coupon_id)
    ).all()
    return {row[0]: (row[1], row[2]) for row in rows}


def _find(session, restaurant_id: UUID, coupon_id: UUID) -> Coupon:
    """One live coupon of this restaurant's.

    Both halves of that are load-bearing. On the system session there is no
    tenant context to fall back on, so a lookup by coupon id alone would
    reach another restaurant's promotion.
    """
    coupon = session.execute(
        select(Coupon).where(
            Coupon.id == coupon_id,
            Coupon.restaurant_id == restaurant_id,
            Coupon.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if coupon is None:
        raise errors.validation_error("No such coupon.")
    return coupon


@router.get("/restaurants/{restaurant_id}/coupons", response_model=CouponListOut)
def list_coupons(restaurant_id: UUID, admin: User = Depends(require_platform_admin)):
    """Every live coupon of one restaurant, newest first, with what each has
    been used for.

    Not audited. It is a read of the platform's own configuration, it exposes
    nothing a customer typed and no money that moved, and an audit line per
    page view would bury the writes that matter.
    """
    with system_session() as session:
        restaurant = _restaurant(session, restaurant_id)
        rows = session.execute(
            select(Coupon)
            .where(Coupon.restaurant_id == restaurant_id, Coupon.deleted_at.is_(None))
            .order_by(Coupon.created_at.desc(), Coupon.id)
            .limit(MAX_COUPONS)
        ).scalars().all()
        used = _usage(session, restaurant_id, [coupon.id for coupon in rows])
        return CouponListOut(
            restaurant_name=restaurant["name"],
            currency=restaurant["currency"],
            coupons=[_out(coupon, *used.get(coupon.id, (0, 0))) for coupon in rows],
        )


@router.post(
    "/restaurants/{restaurant_id}/coupons", response_model=CouponOut, status_code=201
)
def create_coupon(
    restaurant_id: UUID, body: CouponIn, admin: User = Depends(require_platform_admin)
):
    """Create a coupon on a restaurant. It works from the moment it is saved,
    unless it carries a start date or was saved switched off."""
    with system_session() as session:
        _restaurant(session, restaurant_id)

        live = session.execute(
            select(func.count())
            .select_from(Coupon)
            .where(Coupon.restaurant_id == restaurant_id, Coupon.deleted_at.is_(None))
        ).scalar_one()
        if live >= MAX_COUPONS:
            raise errors.validation_error(
                f"That is {MAX_COUPONS} coupons. Retire one before adding another."
            )

        # Checked here for the message. The partial unique index is what
        # actually guarantees it, and it covers the live rows only -- a
        # retired code may be created again.
        clash = session.execute(
            select(Coupon.id).where(
                Coupon.restaurant_id == restaurant_id,
                Coupon.code == body.code,
                Coupon.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if clash is not None:
            raise errors.validation_error(f"{body.code} already exists here.")

        coupon = Coupon(
            restaurant_id=restaurant_id,
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
        session.add(coupon)
        session.flush()

        _audit(
            session, admin, "SUPER_ADMIN_CREATE_COUPON",
            {
                "restaurant_id": str(restaurant_id),
                "coupon_id": str(coupon.id),
                "code": coupon.code,
                "discount_kind": coupon.discount_kind,
                "discount_value": coupon.discount_value,
            },
        )
        log.info("coupon %s created on restaurant %s", coupon.code, restaurant_id)
        return _out(coupon, 0, 0)


@router.patch(
    "/restaurants/{restaurant_id}/coupons/{coupon_id}", response_model=CouponOut
)
def update_coupon(
    restaurant_id: UUID,
    coupon_id: UUID,
    body: CouponPatch,
    admin: User = Depends(require_platform_admin),
):
    """Edit a coupon, except its code.

    A limit may be lowered below what has already been redeemed, and that is
    deliberate: it is how a promotion that is running away gets stopped
    without retiring a code customers are holding. The orders already placed
    keep their discount -- they were charged that way -- and the next person
    to try the code is told it has been fully redeemed.
    """
    with system_session() as session:
        _restaurant(session, restaurant_id)
        coupon = _find(session, restaurant_id, coupon_id)
        sent = body.model_dump(exclude_unset=True)

        if "description" in sent:
            coupon.description = (sent["description"] or "").strip() or None

        # Read together: a value means nothing without its kind, so changing
        # one has to fall back on the other as it is stored.
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
            # Zero is how a form clears a ceiling: an empty number input
            # sends nothing useful, and "no ceiling" has to be expressible.
            ceiling = sent["max_discount_minor"] or None
            if ceiling is not None and coupon.discount_kind != DiscountKind.PERCENT.value:
                raise errors.validation_error(
                    "A ceiling only applies to a percentage discount."
                )
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
        session.flush()

        _audit(
            session, admin, "SUPER_ADMIN_UPDATE_COUPON",
            {
                "restaurant_id": str(restaurant_id),
                "coupon_id": str(coupon.id),
                "code": coupon.code,
                # What was asked for, not the whole row: the log is a record
                # of the decision somebody made.
                "changed": sorted(sent),
            },
        )
        used = _usage(session, restaurant_id, [coupon.id])
        return _out(coupon, *used.get(coupon.id, (0, 0)))


@router.delete(
    "/restaurants/{restaurant_id}/coupons/{coupon_id}", status_code=204
)
def retire_coupon(
    restaurant_id: UUID, coupon_id: UUID, admin: User = Depends(require_platform_admin)
):
    """Retire a coupon. Soft, and the code becomes free again.

    Orders that used it keep their own snapshot of the code and the amount,
    so a receipt from last month still reads the same. The row stays for the
    same reason: a question about a promotion is asked after it has ended.
    """
    with system_session() as session:
        _restaurant(session, restaurant_id)
        coupon = _find(session, restaurant_id, coupon_id)
        coupon.deleted_at = utcnow()
        coupon.is_active = False
        session.flush()

        _audit(
            session, admin, "SUPER_ADMIN_RETIRE_COUPON",
            {
                "restaurant_id": str(restaurant_id),
                "coupon_id": str(coupon.id),
                "code": coupon.code,
            },
        )
        log.info("coupon %s retired on restaurant %s", coupon.code, restaurant_id)
