"""Coupons, created by the platform rather than by the restaurant.

A promotion code is a ZenoEats decision here, not a tenant's: the people who
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
from app.models import (
    Coupon, CouponGroup, CouponItemType, DiscountKind, ItemType, Order, User,
)
from app.services import coupon_groups as group_service
from app.services import sms
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


class OfferIn(BaseModel):
    """What a coupon and a group of coupons both are: a discount and its limits.

    Shared because a group mints coupons, so the two must describe a
    discount the same way or a group would promise one thing and its codes
    deliver another. The subclasses add what is genuinely theirs -- a code
    and a redemption cap for one coupon, a prefix and a count for a group.

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
    max_per_customer: int | None = Field(default=None, gt=0)
    is_active: bool = True

    @field_validator("description")
    @classmethod
    def _described(cls, value: str | None) -> str | None:
        return (value or "").strip() or None

    @model_validator(mode="after")
    def _discount_coherent(self):
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


class CouponIn(OfferIn):
    """A new coupon: one code, typed by whoever was given it."""

    code: str = Field(min_length=3, max_length=32)
    max_redemptions: int | None = Field(default=None, gt=0)

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

    @model_validator(mode="after")
    def _within_total(self):
        """A per-customer cap above the total cap is not a cap at all."""
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


def _check_headings(db, wanted: list[UUID]) -> None:
    """Every id names a live heading of this restaurant, or say so.

    Row-level security already guarantees a heading found here belongs to
    this tenant, so what this adds is the message rather than the safety.
    Its own function because a group checks the same thing before it has a
    coupon to hang a scope on.
    """
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
    if any(one not in live for one in wanted):
        raise errors.validation_error(
            "One of those menu headings is not on this restaurant's menu."
        )


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

    _check_headings(db, wanted)
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
            .where(Coupon.deleted_at.is_(None), Coupon.group_id.is_(None))
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
            select(func.count())
            .select_from(Coupon)
            .where(Coupon.deleted_at.is_(None), Coupon.group_id.is_(None))
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


# ------------------------------------------------------------ group coupons ---
#
# One offer, handed out as numbered codes: "MORE", ten uses, mints MORE1
# through MORE10. See services/coupon_groups.py for why the codes are
# ordinary coupon rows and not a pattern match, and 0055 for the schema.
#
# Everything below is the same shape as the coupon endpoints above, on
# purpose: same tenant session, same audit line per write, same soft delete.


class CouponGroupIn(OfferIn):
    """A new group, and the codes to mint from it."""

    code_prefix: str = Field(min_length=2, max_length=group_service.MAX_PREFIX)
    # How many codes to mint, which is what "uses" means for a group: each
    # code is good once, so ten uses is ten people rather than ten goes.
    uses: int = Field(gt=0, le=group_service.MAX_CODES)


class CouponGroupPatch(BaseModel):
    """An edit to a group.

    Neither the prefix nor the count is here, and for the same reason the
    coupon's code is not editable: the codes have been handed out. Changing
    the prefix would rename codes people are holding, and lowering the count
    would have to decide which of them to cancel.

    Adding more codes to a live group is a different request with a
    different answer, and is the `extra` endpoint below.
    """

    description: str | None = Field(default=None, max_length=160)
    is_active: bool | None = None


class GroupCodeOut(BaseModel):
    """One minted code, and whether it has been spent."""

    id: UUID
    code: str
    number: int
    redemptions: int
    discounted_minor: int
    # Who used it. A group exists so that a code can be traced to a person,
    # so this is the field the whole feature is for. None where the code is
    # unused, or where it was spent by a guest with no account behind it.
    redeemed_by: str | None
    redeemed_at: datetime | None


class CouponGroupOut(BaseModel):
    id: UUID
    code_prefix: str
    description: str | None
    discount_kind: str
    discount_value: int
    max_discount_minor: int | None
    min_subtotal_minor: int
    starts_at: datetime | None
    ends_at: datetime | None
    uses: int | None
    max_per_customer: int | None
    is_active: bool
    applies_to: list[MenuHeadingOut]
    # How many of the minted codes have been used, and what they cost.
    issued: int
    used: int
    discounted_minor: int
    codes: list[GroupCodeOut]
    created_at: datetime


class CouponGroupListOut(BaseModel):
    restaurant_name: str
    currency: str
    item_types: list[MenuHeadingOut]
    groups: list[CouponGroupOut]


def _code_usage(db, group_id: UUID) -> dict:
    """Per code: how many orders, what they took off, and who placed the
    first of them.

    One query rather than one per code. A group of five hundred codes on a
    dashboard is five hundred round trips the obvious way, and the obvious
    way is what gets written when this is left to the caller.
    """
    rows = db.execute(
        select(
            Order.coupon_id,
            func.count().label("orders"),
            func.coalesce(func.sum(Order.coupon_discount_minor), 0).label("off"),
            func.min(Order.created_at).label("first_at"),
        )
        .select_from(Order)
        .join(Coupon, Coupon.id == Order.coupon_id)
        .where(
            Coupon.group_id == group_id,
            Order.status.notin_(coupon_service.SPENT_STATUSES),
        )
        .group_by(Order.coupon_id)
    ).all()

    # The customer who used each code, named separately: an order carries a
    # user id, and turning five hundred of those into names is a join we
    # only want to pay for on the codes that were actually used.
    who = {}
    if rows:
        used_ids = [row.coupon_id for row in rows]
        for coupon_id, name, email in db.execute(
            select(Order.coupon_id, User.full_name, User.email)
            .select_from(Order)
            .join(User, User.id == Order.customer_user_id)
            .where(
                Order.coupon_id.in_(used_ids),
                Order.status.notin_(coupon_service.SPENT_STATUSES),
            )
            .order_by(Order.created_at)
        ).all():
            who.setdefault(coupon_id, name or email)

    return {
        row.coupon_id: (row.orders, row.off, who.get(row.coupon_id), row.first_at)
        for row in rows
    }


def _group_out(db, group: CouponGroup, scope: list[MenuHeadingOut]) -> CouponGroupOut:
    minted = group_service.codes(db, group.id)
    spent = _code_usage(db, group.id)
    used = sum(1 for coupon in minted if coupon.id in spent)
    return CouponGroupOut(
        id=group.id,
        code_prefix=group.code_prefix,
        description=group.description,
        discount_kind=group.discount_kind,
        discount_value=group.discount_value,
        max_discount_minor=group.max_discount_minor,
        min_subtotal_minor=group.min_subtotal_minor,
        starts_at=group.starts_at,
        ends_at=group.ends_at,
        uses=group.uses,
        max_per_customer=group.max_per_customer,
        is_active=group.is_active,
        applies_to=scope,
        issued=len(minted),
        used=used,
        discounted_minor=sum(value[1] for value in spent.values()),
        codes=[
            GroupCodeOut(
                id=coupon.id,
                code=coupon.code,
                number=coupon.group_number or 0,
                redemptions=spent.get(coupon.id, (0, 0, None, None))[0],
                discounted_minor=spent.get(coupon.id, (0, 0, None, None))[1],
                redeemed_by=spent.get(coupon.id, (0, 0, None, None))[2],
                redeemed_at=spent.get(coupon.id, (0, 0, None, None))[3],
            )
            for coupon in minted
        ],
        created_at=group.created_at,
    )


def _find_group(db, group_id: UUID) -> CouponGroup:
    group = db.execute(
        select(CouponGroup).where(
            CouponGroup.id == group_id, CouponGroup.deleted_at.is_(None)
        )
    ).scalar_one_or_none()
    if group is None:
        raise errors.ApiError(404, "COUPON_GROUP_NOT_FOUND", "No such coupon group.")
    return group


def _group_scope(db, group: CouponGroup, headings: list[MenuHeadingOut]) -> list[MenuHeadingOut]:
    """The headings a group's offer applies to.

    Read from the group, which is where it is stored: a sign-up offer has no
    codes until its first customer arrives, so a scope read off a minted code
    would be empty for exactly the offer that most needs one.
    """
    wanted = set(group_service.scope_of(db, group.id))
    return [heading for heading in headings if heading.id in wanted]


@router.get(
    "/restaurants/{restaurant_id}/coupon-groups", response_model=CouponGroupListOut
)
def list_coupon_groups(restaurant_id: UUID, admin: User = Depends(require_platform_admin)):
    """Every live group of one restaurant, newest first, with each code and
    who spent it."""
    with tenant_session(restaurant_id) as db:
        restaurant = _restaurant(db, restaurant_id)
        headings = _headings(db)
        groups = db.execute(
            select(CouponGroup)
            .where(
                CouponGroup.deleted_at.is_(None),
                CouponGroup.kind == "NUMBERED",
            )
            .order_by(CouponGroup.created_at.desc(), CouponGroup.id)
            .limit(MAX_COUPONS)
        ).scalars().all()
        return CouponGroupListOut(
            restaurant_name=restaurant["name"],
            currency=restaurant["currency"],
            item_types=headings,
            groups=[
                _group_out(db, group, _group_scope(db, group, headings))
                for group in groups
            ],
        )


@router.post(
    "/restaurants/{restaurant_id}/coupon-groups",
    response_model=CouponGroupOut,
    status_code=201,
)
def create_coupon_group(
    restaurant_id: UUID,
    body: CouponGroupIn,
    admin: User = Depends(require_platform_admin),
):
    """Create a group and mint its codes.

    The codes are written here, in one transaction with the group: a group
    that existed without them would be an offer nobody could be given, and
    the half-minted state is the one thing worth ruling out entirely.
    """
    prefix = group_service.validate_prefix(body.code_prefix)

    with tenant_session(restaurant_id) as db:
        _restaurant(db, restaurant_id)

        live = db.execute(
            select(func.count())
            .select_from(CouponGroup)
            .where(CouponGroup.deleted_at.is_(None))
        ).scalar_one()
        if live >= MAX_COUPONS:
            raise errors.validation_error(
                f"That is {MAX_COUPONS} groups. Retire one before adding another."
            )
        if db.execute(
            select(CouponGroup.id).where(
                CouponGroup.code_prefix == prefix, CouponGroup.deleted_at.is_(None)
            )
        ).scalar_one_or_none() is not None:
            raise errors.validation_error(f"A {prefix} group already exists here.")

        # Before writing anything: a hand-made coupon may already hold one of
        # the codes this would mint, and a half-written group is worse than a
        # refused one.
        taken = group_service.clashes(db, prefix, range(1, body.uses + 1))
        if taken:
            shown = ", ".join(taken[:5])
            more = "" if len(taken) <= 5 else f" and {len(taken) - 5} more"
            raise errors.validation_error(
                f"That would mint codes that already exist here: {shown}{more}."
            )

        _check_headings(db, body.applies_to)

        kind, value = body.stored()
        group = CouponGroup(
            restaurant_id=restaurant_id,
            code_prefix=prefix,
            description=body.description,
            discount_kind=kind,
            discount_value=value,
            max_discount_minor=body.max_discount_minor,
            min_subtotal_minor=body.min_subtotal_minor,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
            uses=body.uses,
            max_per_customer=body.max_per_customer,
            is_active=body.is_active,
            kind="NUMBERED",
        )
        db.add(group)
        db.flush()

        group_service.set_scope(db, group, body.applies_to)
        minted = group_service.mint(db, group, range(1, body.uses + 1))
        group_service.copy_scope(db, group, minted, body.applies_to)
        db.flush()

        headings = _headings(db)
        scope = _group_scope(db, group, headings)

        with system_session() as platform:
            _audit(
                platform, admin, "SUPER_ADMIN_CREATE_COUPON_GROUP",
                {
                    "restaurant_id": str(restaurant_id),
                    "group_id": str(group.id),
                    "code_prefix": prefix,
                    "uses": body.uses,
                    "discount_kind": kind,
                    "discount_value": value,
                    "applies_to": [heading.name for heading in scope],
                },
            )
        log.info(
            "coupon group %s minted %d codes on restaurant %s",
            prefix, body.uses, restaurant_id,
        )
        return _group_out(db, group, scope)


@router.patch(
    "/restaurants/{restaurant_id}/coupon-groups/{group_id}",
    response_model=CouponGroupOut,
)
def update_coupon_group(
    restaurant_id: UUID,
    group_id: UUID,
    body: CouponGroupPatch,
    admin: User = Depends(require_platform_admin),
):
    """Pause, resume, or re-describe a group.

    Switching it carries to every code it minted: a paused group whose codes
    still worked would be a pause that paused nothing.
    """
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise errors.validation_error("No changes to save.")

    with tenant_session(restaurant_id) as db:
        _restaurant(db, restaurant_id)
        group = _find_group(db, group_id)

        if "description" in changes:
            group.description = (changes["description"] or "").strip() or None
        if "is_active" in changes:
            group_service.set_active(db, group, bool(changes["is_active"]))
        db.flush()

        headings = _headings(db)
        scope = _group_scope(db, group, headings)

        with system_session() as platform:
            _audit(
                platform, admin, "SUPER_ADMIN_UPDATE_COUPON_GROUP",
                {
                    "restaurant_id": str(restaurant_id),
                    "group_id": str(group.id),
                    "code_prefix": group.code_prefix,
                    "changed": sorted(changes),
                },
            )
        return _group_out(db, group, scope)


@router.delete(
    "/restaurants/{restaurant_id}/coupon-groups/{group_id}", status_code=204
)
def retire_coupon_group(
    restaurant_id: UUID, group_id: UUID, admin: User = Depends(require_platform_admin)
):
    """Retire a group and every code it minted.

    A soft delete, like retiring a coupon: the orders that used these codes
    keep their own snapshot of the code and the amount, so last month's
    receipt still reads the same. What it buys is that the codes stop
    working and the prefix is free to be used again.
    """
    with tenant_session(restaurant_id) as db:
        _restaurant(db, restaurant_id)
        group = _find_group(db, group_id)
        group_service.retire(db, group)
        db.flush()

        with system_session() as platform:
            _audit(
                platform, admin, "SUPER_ADMIN_RETIRE_COUPON_GROUP",
                {
                    "restaurant_id": str(restaurant_id),
                    "group_id": str(group.id),
                    "code_prefix": group.code_prefix,
                },
            )
        log.info("coupon group %s retired on restaurant %s", group.code_prefix, restaurant_id)


# ------------------------------------------------------------ welcome offer ---
#
# One code per new customer, earned by proving a mobile number. A coupon
# group of kind SIGNUP (0056), so everything after minting is what a coupon
# already does -- see services/signup_offers.py for what is actually new.
#
# A singleton per restaurant rather than a list: two live welcome offers
# would both fire on the same sign-up and hand one account two codes.


class SignupOfferIn(OfferIn):
    """The welcome offer, as the form sets it up."""

    # The codes are this plus a random suffix: WELCOME7K3M9Q. Random rather
    # than numbered, because a personal code that read WELCOME7 would tell
    # its holder they were the seventh customer and invite a guess at
    # WELCOME8.
    code_prefix: str = Field(min_length=2, max_length=group_service.MAX_PREFIX)
    # How long one customer's code lasts from the day it is issued. A date
    # would be a different and meaner offer: everybody signs up on a
    # different day, so "30 days to use it" is the promise, not "until
    # March".
    valid_days: int | None = Field(default=None, gt=0, le=3650)


class SignupOfferOut(BaseModel):
    id: UUID
    code_prefix: str
    description: str | None
    discount_kind: str
    discount_value: int
    max_discount_minor: int | None
    min_subtotal_minor: int
    starts_at: datetime | None
    ends_at: datetime | None
    valid_days: int | None
    is_active: bool
    applies_to: list[MenuHeadingOut]
    # How many customers have been given a code, how many spent it, and what
    # that cost -- the three numbers that say whether the offer is working.
    issued: int
    used: int
    discounted_minor: int
    created_at: datetime


class SignupOfferStateOut(BaseModel):
    """The offer, plus whether this deployment can actually run it.

    `sms_configured` is false here and the screen says so rather than
    letting somebody switch on an offer whose verification step cannot
    send -- see services/sms.py.
    """

    restaurant_name: str
    currency: str
    item_types: list[MenuHeadingOut]
    sms_configured: bool
    offer: SignupOfferOut | None


def _signup_out(db, group: CouponGroup, headings: list[MenuHeadingOut]) -> SignupOfferOut:
    spent = _code_usage(db, group.id)
    issued = db.execute(
        select(func.count())
        .select_from(Coupon)
        .where(Coupon.group_id == group.id, Coupon.deleted_at.is_(None))
    ).scalar_one()
    return SignupOfferOut(
        id=group.id,
        code_prefix=group.code_prefix,
        description=group.description,
        discount_kind=group.discount_kind,
        discount_value=group.discount_value,
        max_discount_minor=group.max_discount_minor,
        min_subtotal_minor=group.min_subtotal_minor,
        starts_at=group.starts_at,
        ends_at=group.ends_at,
        valid_days=group.valid_days,
        is_active=group.is_active,
        applies_to=_group_scope(db, group, headings),
        issued=issued,
        used=len(spent),
        discounted_minor=sum(value[1] for value in spent.values()),
        created_at=group.created_at,
    )


def _signup_offer(db) -> CouponGroup | None:
    return db.execute(
        select(CouponGroup).where(
            CouponGroup.kind == "SIGNUP", CouponGroup.deleted_at.is_(None)
        )
    ).scalar_one_or_none()


@router.get(
    "/restaurants/{restaurant_id}/signup-offer", response_model=SignupOfferStateOut
)
def read_signup_offer(restaurant_id: UUID, admin: User = Depends(require_platform_admin)):
    """The welcome offer, if this restaurant has one."""
    with tenant_session(restaurant_id) as db:
        restaurant = _restaurant(db, restaurant_id)
        headings = _headings(db)
        group = _signup_offer(db)
        return SignupOfferStateOut(
            restaurant_name=restaurant["name"],
            currency=restaurant["currency"],
            item_types=headings,
            sms_configured=sms.configured(),
            offer=None if group is None else _signup_out(db, group, headings),
        )


@router.put(
    "/restaurants/{restaurant_id}/signup-offer", response_model=SignupOfferOut
)
def set_signup_offer(
    restaurant_id: UUID,
    body: SignupOfferIn,
    admin: User = Depends(require_platform_admin),
):
    """Create the welcome offer, or change it.

    Changing it does not touch codes already issued. A customer was given
    theirs on the terms that were running at the time, and rewriting those
    afterwards would change what somebody was promised -- so an edit decides
    what the next customer gets, and the codes already out keep their own.
    """
    prefix = group_service.validate_prefix(body.code_prefix)

    with tenant_session(restaurant_id) as db:
        _restaurant(db, restaurant_id)
        _check_headings(db, body.applies_to)

        # A prefix a hand-made coupon or a numbered group already uses would
        # make two different offers answer to codes that look alike.
        clash = db.execute(
            select(CouponGroup.id).where(
                CouponGroup.code_prefix == prefix,
                CouponGroup.kind == "NUMBERED",
                CouponGroup.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if clash is not None:
            raise errors.validation_error(
                f"A {prefix} group coupon already uses that prefix."
            )

        kind, value = body.stored()
        group = _signup_offer(db)
        created = group is None
        if created:
            group = CouponGroup(
                restaurant_id=restaurant_id,
                kind="SIGNUP",
                code_prefix=prefix,
                uses=None,
            )
            db.add(group)

        group.code_prefix = prefix
        group.description = body.description
        group.discount_kind = kind
        group.discount_value = value
        group.max_discount_minor = body.max_discount_minor
        group.min_subtotal_minor = body.min_subtotal_minor
        group.starts_at = body.starts_at
        group.ends_at = body.ends_at
        group.valid_days = body.valid_days
        group.is_active = body.is_active
        db.flush()

        group_service.set_scope(db, group, body.applies_to)
        db.flush()

        headings = _headings(db)
        with system_session() as platform:
            _audit(
                platform, admin,
                "SUPER_ADMIN_CREATE_SIGNUP_OFFER" if created
                else "SUPER_ADMIN_UPDATE_SIGNUP_OFFER",
                {
                    "restaurant_id": str(restaurant_id),
                    "group_id": str(group.id),
                    "code_prefix": prefix,
                    "discount_kind": kind,
                    "discount_value": value,
                    "valid_days": body.valid_days,
                },
            )
        log.info("signup offer %s on restaurant %s", prefix, restaurant_id)
        return _signup_out(db, group, headings)


@router.delete("/restaurants/{restaurant_id}/signup-offer", status_code=204)
def retire_signup_offer(
    restaurant_id: UUID, admin: User = Depends(require_platform_admin)
):
    """End the welcome offer.

    Codes already issued stop working with it, the same way retiring a
    numbered group ends its codes: the offer is over. The rows stay, so the
    orders that used them still read.
    """
    with tenant_session(restaurant_id) as db:
        _restaurant(db, restaurant_id)
        group = _signup_offer(db)
        if group is None:
            raise errors.ApiError(
                404, "SIGNUP_OFFER_NOT_FOUND", "There is no welcome offer here."
            )
        group_service.retire(db, group)
        db.flush()

        with system_session() as platform:
            _audit(
                platform, admin, "SUPER_ADMIN_RETIRE_SIGNUP_OFFER",
                {"restaurant_id": str(restaurant_id), "group_id": str(group.id)},
            )
