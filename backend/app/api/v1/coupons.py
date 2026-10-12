"""Coupons, created by the platform rather than by the restaurant.

A promotion code is a Zenoeats decision here, not a tenant's: the people who
run the platform set them up on a restaurant's behalf, and the restaurant's
own portal has no coupon screen at all. Which is why this is mounted on the
admin router, behind require_platform_admin, on admin.<root domain>.

THE CUSTOMER SIDE IS UNAFFECTED by that choice. A code is typed at checkout
and priced by services/coupons.py under the ordinary tenant session, like
every other thing a customer's cart touches. This module is only where
coupons come from.

ON THE SESSION, which is the thing to get right in here. A platform admin is
a member of no restaurant, so the obvious implementation is the cross-tenant
system surface -- and that is the wrong one. db/session.py says so in as many
words: "Never used for tenant business mutations: resolve the restaurant_id
here, then switch to tenant_session() to do the work."

A coupon is a tenant business mutation. So each endpoint takes the
restaurant_id from the URL, opens a tenant session on it, and does the work
under FORCE ROW LEVEL SECURITY like every other router in this codebase --
which means a query that forgot its restaurant_id would return nothing
rather than somebody else's promotion. The explicit predicates below are
belt and braces again, as they should be; the belt is the policy.

The audit line is the one thing that is genuinely platform-owned, so it is
written on a nested system session, exactly as the permanent purge writes its
location detach (admin.py).

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
from app.db.session import system_session, tenant_session
from app.models import Coupon, CouponItemType, DiscountKind, ItemType, Order, User
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

    THE DISCOUNT IS TWO OPTIONAL FIELDS, one of which has to be filled. A
    percentage and an amount are the two things a promotion can be, and an
    operator thinks in one or the other -- "10% off" or "$5 off" -- so the
    form asks for both and takes whichever was answered. What is stored is
    still a kind and a value, because a row that could hold both would have
    to be read by something that decided which won.

    Both at once is refused rather than guessed. "10% off and $5 off" has no
    single answer, and the thing it most resembles -- a percentage with a
    ceiling -- is what max_discount_minor is for.

    EVERYTHING ELSE IS OPTIONAL, because a real promotion uses some limits
    and not others: a launch offer has an end date and no cap, a referral
    code has a cap of one per customer and no end date at all.
    """

    code: str = Field(min_length=3, max_length=32)
    description: str | None = Field(default=None, max_length=160)

    # Basis points, so 1000 is 10%. The form converts; this is the unit the
    # database and every other discount in the system already use.
    percent_off_bps: int | None = Field(default=None, gt=0, le=10000)
    # Minor units, so 500 is $5.00.
    amount_off_minor: int | None = Field(default=None, gt=0)

    # A ceiling, for a percentage only: 20% off is a different offer on a $10
    # order and on a $400 office lunch.
    max_discount_minor: int | None = Field(default=None, gt=0)
    min_subtotal_minor: int = Field(default=0, ge=0)

    # Which menu headings it applies to. Empty is the common case and means
    # the whole cart; naming headings makes it "20% off burgers".
    applies_to: list[UUID] = Field(default_factory=list)

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
        if self.percent_off_bps is None and self.amount_off_minor is None:
            raise ValueError("Fill in either a percentage off or an amount off.")
        if self.percent_off_bps is not None and self.amount_off_minor is not None:
            raise ValueError(
                "Fill in a percentage off or an amount off, not both. "
                "For a percentage with a limit, set the ceiling instead."
            )
        if self.amount_off_minor is not None and self.max_discount_minor is not None:
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

    def stored(self) -> tuple[str, int]:
        """The kind and value as the row holds them.

        One place, so the two readings of `discount_value` cannot drift: the
        validator above has already established that exactly one of the two
        fields is set.
        """
        if self.percent_off_bps is not None:
            return DiscountKind.PERCENT.value, self.percent_off_bps
        return DiscountKind.AMOUNT.value, self.amount_off_minor


class CouponPatch(BaseModel):
    """An edit. Every field optional; the code is not one of them.

    The code is what customers have been given, printed on something, or
    told over the phone. Changing it would break every copy of it in the
    world while the coupon went on existing, so a new code is a new coupon
    and the old one gets retired.

    The discount is the same pair of fields as above and the same rule: send
    one of them to change it, neither to leave it alone, and both to be
    refused.
    """

    description: str | None = Field(default=None, max_length=160)
    percent_off_bps: int | None = Field(default=None, gt=0, le=10000)
    amount_off_minor: int | None = Field(default=None, gt=0)
    max_discount_minor: int | None = Field(default=None, ge=0)
    min_subtotal_minor: int | None = Field(default=None, ge=0)
    # Sent to replace the scope outright; an empty list means "the whole
    # cart again". Absent leaves it as it is, which is why this is None
    # rather than an empty default -- the two have to be tellable apart.
    applies_to: list[UUID] | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    max_redemptions: int | None = Field(default=None, ge=0)
    max_per_customer: int | None = Field(default=None, ge=0)
    is_active: bool | None = None

    @model_validator(mode="after")
    def _one_discount(self):
        if self.percent_off_bps is not None and self.amount_off_minor is not None:
            raise ValueError(
                "Fill in a percentage off or an amount off, not both. "
                "For a percentage with a limit, set the ceiling instead."
            )
        return self


class MenuHeadingOut(BaseModel):
    """One heading on a restaurant's menu, as the coupon form offers it."""

    id: UUID
    name: str


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
    # The headings it applies to. Empty means the whole cart.
    applies_to: list[MenuHeadingOut]
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
    # This restaurant's menu headings, for the "applies to" field. Sent with
    # the list so the form has everything it needs from one request.
    item_types: list[MenuHeadingOut]
    coupons: list[CouponOut]


def _out(
    coupon: Coupon, redemptions: int, discounted: int, scope: list[MenuHeadingOut]
) -> CouponOut:
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
        applies_to=scope,
        redemptions=redemptions,
        discounted_minor=discounted,
        created_at=coupon.created_at,
    )


def _restaurant(db, restaurant_id: UUID):
    """The restaurant these coupons belong to, or 404.

    Read inside the tenant session, so row-level security is what proves the
    row is the one the URL named -- the same thing current_restaurant does
    for every staff endpoint. A deleted or non-existent id finds nothing.
    """
    found = db.execute(
        text(
            "SELECT name, currency FROM restaurants "
            "WHERE id = :rid AND deleted_at IS NULL"
        ),
        {"rid": str(restaurant_id)},
    ).mappings().one_or_none()
    if found is None:
        raise errors.ApiError(404, "RESTAURANT_NOT_FOUND", "No such restaurant.")
    return found


def _headings(db) -> list[MenuHeadingOut]:
    """Every live heading on this restaurant's menu, in its own order."""
    rows = db.execute(
        select(ItemType)
        .where(ItemType.deleted_at.is_(None))
        .order_by(ItemType.sort_order, ItemType.id)
    ).scalars().all()
    return [MenuHeadingOut(id=row.id, name=row.name) for row in rows]


def _scopes(db, coupon_ids: list[UUID], headings: list[MenuHeadingOut]) -> dict:
    """coupon id -> the headings it applies to, for a page of coupons.

    One query for all of them, and the names come from the headings already
    read rather than a second join: a coupon's scope is a handful of ids and
    the menu is a handful of headings.
    """
    if not coupon_ids:
        return {}
    by_id = {heading.id: heading for heading in headings}
    out: dict[UUID, list[MenuHeadingOut]] = {cid: [] for cid in coupon_ids}
    rows = db.execute(
        select(CouponItemType.coupon_id, CouponItemType.item_type_id).where(
            CouponItemType.coupon_id.in_(coupon_ids)
        )
    ).all()
    for coupon_id, item_type_id in rows:
        heading = by_id.get(item_type_id)
        if heading is not None:
            out[coupon_id].append(heading)
    for scope in out.values():
        scope.sort(key=lambda heading: heading.name)
    return out


def _set_scope(db, restaurant_id: UUID, coupon: Coupon, wanted: list[UUID]) -> None:
    """Replace a coupon's scope with the headings named.

    Rebuilt rather than merged: a scope is a set with no history, and the
    form sends the whole of it. Every id has to be a live heading of this
    restaurant -- which row-level security already guarantees it would have
    to be to be found at all, so what this check adds is the message.
    """
    db.execute(
        text("DELETE FROM coupon_item_types WHERE coupon_id = :c"), {"c": coupon.id}
    )
    if not wanted:
        return

    live = {
        row
        for row in db.execute(
            select(ItemType.id).where(
                ItemType.id.in_(wanted), ItemType.deleted_at.is_(None)
            )
        ).scalars()
    }
    missing = [str(one) for one in wanted if one not in live]
    if missing:
        raise errors.validation_error(
            "One of those menu headings is not on this restaurant's menu."
        )
    for item_type_id in dict.fromkeys(wanted):
        db.add(
            CouponItemType(
                restaurant_id=restaurant_id,
                coupon_id=coupon.id,
                item_type_id=item_type_id,
            )
        )


def _usage(db, coupon_ids: list[UUID]) -> dict:
    """How many orders used each coupon, and what they took off.

    One grouped query however many coupons there are: a page of thirty
    promotions used to be thirty round trips every time it was opened.
    """
    if not coupon_ids:
        return {}
    rows = db.execute(
        select(
            Order.coupon_id,
            func.count(),
            func.coalesce(func.sum(Order.coupon_discount_minor), 0),
        )
        .where(
            Order.coupon_id.in_(coupon_ids),
            Order.status.notin_(coupon_service.SPENT_STATUSES),
        )
        .group_by(Order.coupon_id)
    ).all()
    return {row[0]: (row[1], row[2]) for row in rows}


def _find(db, coupon_id: UUID) -> Coupon:
    """One live coupon of this restaurant's.

    Scoped by the tenant session, so a coupon id belonging to another
    restaurant finds nothing here.
    """
    coupon = db.execute(
        select(Coupon).where(Coupon.id == coupon_id, Coupon.deleted_at.is_(None))
    ).scalar_one_or_none()
    if coupon is None:
        raise errors.validation_error("No such coupon.")
    return coupon


@router.get("/restaurants/{restaurant_id}/coupons", response_model=CouponListOut)
def list_coupons(restaurant_id: UUID, admin: User = Depends(require_platform_admin)):
    """Every live coupon of one restaurant, newest first, with what each has
    been used for -- and the restaurant's menu headings, which the form needs
    for the "applies to" field.

    Not audited. It is a read of the platform's own configuration, it exposes
    nothing a customer typed and no money that moved, and an audit line per
    page view would bury the writes that matter.
    """
    with tenant_session(restaurant_id) as db:
        restaurant = _restaurant(db, restaurant_id)
        headings = _headings(db)
        rows = db.execute(
            select(Coupon)
            .where(Coupon.deleted_at.is_(None))
            .order_by(Coupon.created_at.desc(), Coupon.id)
            .limit(MAX_COUPONS)
        ).scalars().all()

        ids = [coupon.id for coupon in rows]
        used = _usage(db, ids)
        scopes = _scopes(db, ids, headings)
        return CouponListOut(
            restaurant_name=restaurant["name"],
            currency=restaurant["currency"],
            item_types=headings,
            coupons=[
                _out(coupon, *used.get(coupon.id, (0, 0)), scopes.get(coupon.id, []))
                for coupon in rows
            ],
        )


@router.post(
    "/restaurants/{restaurant_id}/coupons", response_model=CouponOut, status_code=201
)
def create_coupon(
    restaurant_id: UUID, body: CouponIn, admin: User = Depends(require_platform_admin)
):
    """Create a coupon on a restaurant. It works from the moment it is saved,
    unless it carries a start date or was saved switched off."""
    with tenant_session(restaurant_id) as db:
        _restaurant(db, restaurant_id)

        live = db.execute(
            select(func.count()).select_from(Coupon).where(Coupon.deleted_at.is_(None))
        ).scalar_one()
        if live >= MAX_COUPONS:
            raise errors.validation_error(
                f"That is {MAX_COUPONS} coupons. Retire one before adding another."
            )

        # Checked here for the message. The partial unique index is what
        # actually guarantees it, and it covers the live rows only -- a
        # retired code may be created again.
        clash = db.execute(
            select(Coupon.id).where(
                Coupon.code == body.code, Coupon.deleted_at.is_(None)
            )
        ).scalar_one_or_none()
        if clash is not None:
            raise errors.validation_error(f"{body.code} already exists here.")

        kind, value = body.stored()
        coupon = Coupon(
            restaurant_id=restaurant_id,
            code=body.code,
            description=body.description,
            discount_kind=kind,
            discount_value=value,
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
        _set_scope(db, restaurant_id, coupon, body.applies_to)
        db.flush()

        headings = _headings(db)
        scope = _scopes(db, [coupon.id], headings).get(coupon.id, [])

        with system_session() as platform:
            _audit(
                platform, admin, "SUPER_ADMIN_CREATE_COUPON",
                {
                    "restaurant_id": str(restaurant_id),
                    "coupon_id": str(coupon.id),
                    "code": coupon.code,
                    "discount_kind": kind,
                    "discount_value": value,
                    "applies_to": [heading.name for heading in scope],
                },
            )
        log.info("coupon %s created on restaurant %s", coupon.code, restaurant_id)
        return _out(coupon, 0, 0, scope)


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
    with tenant_session(restaurant_id) as db:
        _restaurant(db, restaurant_id)
        coupon = _find(db, coupon_id)
        sent = body.model_dump(exclude_unset=True)

        if "description" in sent:
            coupon.description = (sent["description"] or "").strip() or None

        # Whichever of the two was sent replaces both columns. The validator
        # has already refused both at once.
        if sent.get("percent_off_bps") is not None:
            coupon.discount_kind = DiscountKind.PERCENT.value
            coupon.discount_value = sent["percent_off_bps"]
        elif sent.get("amount_off_minor") is not None:
            coupon.discount_kind = DiscountKind.AMOUNT.value
            coupon.discount_value = sent["amount_off_minor"]
            # A flat amount cannot carry a ceiling, so changing to one drops
            # any ceiling the percentage had rather than leaving a setting
            # behind that no longer means anything.
            coupon.max_discount_minor = None

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

        if "applies_to" in sent:
            _set_scope(db, restaurant_id, coupon, sent["applies_to"] or [])

        coupon.updated_at = utcnow()
        db.flush()

        headings = _headings(db)
        scope = _scopes(db, [coupon.id], headings).get(coupon.id, [])

        with system_session() as platform:
            _audit(
                platform, admin, "SUPER_ADMIN_UPDATE_COUPON",
                {
                    "restaurant_id": str(restaurant_id),
                    "coupon_id": str(coupon.id),
                    "code": coupon.code,
                    # What was asked for, not the whole row: the log is a
                    # record of the decision somebody made.
                    "changed": sorted(sent),
                },
            )
        used = _usage(db, [coupon.id])
        return _out(coupon, *used.get(coupon.id, (0, 0)), scope)


@router.delete("/restaurants/{restaurant_id}/coupons/{coupon_id}", status_code=204)
def retire_coupon(
    restaurant_id: UUID, coupon_id: UUID, admin: User = Depends(require_platform_admin)
):
    """Retire a coupon. Soft, and the code becomes free again.

    Orders that used it keep their own snapshot of the code and the amount,
    so a receipt from last month still reads the same. The row stays for the
    same reason: a question about a promotion is asked after it has ended.
    """
    with tenant_session(restaurant_id) as db:
        _restaurant(db, restaurant_id)
        coupon = _find(db, coupon_id)
        coupon.deleted_at = utcnow()
        coupon.is_active = False
        db.flush()

        with system_session() as platform:
            _audit(
                platform, admin, "SUPER_ADMIN_RETIRE_COUPON",
                {
                    "restaurant_id": str(restaurant_id),
                    "coupon_id": str(coupon.id),
                    "code": coupon.code,
                },
            )
        log.info("coupon %s retired on restaurant %s", coupon.code, restaurant_id)
