"""Coupons, as the restaurant's own.

Promotions used to be the platform's alone: the manage portal had no coupon
screen and the restaurant API had no endpoint behind one, so a restaurant
wanting to run "20% off Tuesdays" filed a support request. That is the wrong
shape for the people whose margin it is.

WHAT IS SHARED AND WHAT IS NOT. Everything about *what a coupon is* --
pricing, the window, limits, minting a group's codes, the welcome offer, who
redeemed what -- lives in services/ and in api/v1/coupons.py and is called
from here rather than written again. What this file holds is the sequence of
calls plus a staff session and a restaurant audit line, which is the part
that genuinely differs. The platform router keeps its own screens: support
still needs to see and fix a restaurant's promotion.

WHO MAY. The same gate the delivery rings use -- ADMIN or IT_SUPPORT at this
restaurant. A discount is money, so it sits with the people who already set
the tax rate and the delivery fees rather than with everyone who can open the
kitchen board.

WHY NOT the platform's cross-tenant session: a coupon is a tenant business
mutation, so every endpoint here runs on the staff session under FORCE ROW
LEVEL SECURITY, which is what makes a query that forgot its restaurant find
nothing rather than somebody else's promotion.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import StaffDb, current_restaurant_staff
from app.api.v1.coupons import (
    MAX_COUPONS, MAX_REDEMPTIONS,
    CouponGroupIn, CouponGroupListOut, CouponGroupOut, CouponGroupPatch,
    CouponIn, CouponListOut, CouponOut, CouponPatch,
    RedemptionListOut, RedemptionOut,
    SignupOfferIn, SignupOfferOut, SignupOfferStateOut,
    _check_headings, _code_usage, _find, _find_group, _group_out, _group_scope,
    _headings, _out, _restaurant, _scopes, _signup_out, _usage,
    apply_patch, new_coupon,
)
from app.api.v1.restaurant import SETTINGS
from app.core import errors
from app.db.base import utcnow
from app.models import (
    Coupon, CouponGroup, Order, Restaurant, RestaurantUser, User, UserKind,
)
from app.services import coupon_groups as group_service
from app.services import coupons as coupon_service
from app.services import restaurant_profile, sms

log = logging.getLogger(__name__)
router = APIRouter(prefix="/restaurant", tags=["restaurant-coupons"])


def _audit(db, membership: RestaurantUser, restaurant_id, action: str, scope: dict) -> None:
    """Record what the restaurant did, naming the staff member.

    A discount is money, and "who set this up" is a question asked
    afterwards. The platform log records what the platform does; this
    records what the restaurant does.
    """
    restaurant_profile.audit(
        db, membership.user_id, action, {"restaurant_id": str(restaurant_id), **scope}
    )
    db.flush()


# ------------------------------------------------------------------ coupons ---


@router.get("/coupons", response_model=CouponListOut)
def list_coupons(
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(SETTINGS),
):
    """Every live coupon here, newest first, with what each has been used for,
    and the menu headings the form needs.

    Not audited: a read of the restaurant's own configuration, exposing
    nothing a customer typed, and a line per page view would bury the writes
    that matter.
    """
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
        restaurant_name=restaurant.name,
        currency=restaurant.currency,
        item_types=headings,
        coupons=[
            _out(coupon, *used.get(coupon.id, (0, 0)), scopes.get(coupon.id, []))
            for coupon in rows
        ],
    )


@router.post("/coupons", response_model=CouponOut, status_code=201)
def create_coupon(
    body: CouponIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(SETTINGS),
):
    """Create a coupon. It works from the moment it is saved, unless it
    carries a start date or was saved switched off."""
    _check_headings(db, body.applies_to)
    coupon = new_coupon(db, restaurant.id, body)

    headings = _headings(db)
    scope = _scopes(db, [coupon.id], headings).get(coupon.id, [])
    _audit(
        db, membership, restaurant.id, "RESTAURANT_CREATE_COUPON",
        {
            "coupon_id": str(coupon.id),
            "code": coupon.code,
            "discount_kind": coupon.discount_kind,
            "discount_value": coupon.discount_value,
            "applies_to": [heading.name for heading in scope],
        },
    )
    log.info("coupon %s created by restaurant %s", coupon.code, restaurant.id)
    return _out(coupon, 0, 0, scope)


@router.patch("/coupons/{coupon_id}", response_model=CouponOut)
def update_coupon(
    coupon_id: UUID,
    body: CouponPatch,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(SETTINGS),
):
    """Edit a coupon, except its code.

    The code is what customers were given, printed on something or read out
    over the phone, so a new code is a new coupon and the old one is retired.
    """
    sent = body.model_dump(exclude_unset=True)
    if not sent:
        raise errors.validation_error("No changes to save.")

    coupon = _find(db, coupon_id)
    if sent.get("applies_to"):
        _check_headings(db, sent["applies_to"])
    apply_patch(db, restaurant.id, coupon, sent)

    headings = _headings(db)
    scope = _scopes(db, [coupon.id], headings).get(coupon.id, [])
    _audit(
        db, membership, restaurant.id, "RESTAURANT_UPDATE_COUPON",
        {"coupon_id": str(coupon.id), "code": coupon.code, "changed": sorted(sent)},
    )
    return _out(coupon, *_usage(db, [coupon.id]).get(coupon.id, (0, 0)), scope)


@router.delete("/coupons/{coupon_id}", status_code=204)
def retire_coupon(
    coupon_id: UUID,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(SETTINGS),
):
    """Take a coupon off this screen and free its code to be used again.

    A soft delete: orders point at it and keep their own snapshot of the code
    and the amount, so a receipt from last month still reads the same.
    """
    coupon = _find(db, coupon_id)
    coupon.deleted_at = utcnow()
    coupon.is_active = False
    db.flush()
    _audit(
        db, membership, restaurant.id, "RESTAURANT_RETIRE_COUPON",
        {"coupon_id": str(coupon.id), "code": coupon.code},
    )
    log.info("coupon %s retired by restaurant %s", coupon.code, restaurant.id)


# ------------------------------------------------------------ group coupons ---


@router.get("/coupon-groups", response_model=CouponGroupListOut)
def list_groups(
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(SETTINGS),
):
    """Every live group here, with each code and who spent it."""
    headings = _headings(db)
    groups = db.execute(
        select(CouponGroup)
        .where(CouponGroup.deleted_at.is_(None), CouponGroup.kind == "NUMBERED")
        .order_by(CouponGroup.created_at.desc(), CouponGroup.id)
        .limit(MAX_COUPONS)
    ).scalars().all()
    return CouponGroupListOut(
        restaurant_name=restaurant.name,
        currency=restaurant.currency,
        item_types=headings,
        groups=[
            _group_out(db, group, _group_scope(db, group, headings))
            for group in groups
        ],
    )


@router.post("/coupon-groups", response_model=CouponGroupOut, status_code=201)
def create_group(
    body: CouponGroupIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(SETTINGS),
):
    """Create a group and mint its codes, in one transaction.

    A group that existed without its codes would be an offer nobody could be
    given, and the half-minted state is the one worth ruling out entirely.
    """
    prefix = group_service.validate_prefix(body.code_prefix)

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
        restaurant_id=restaurant.id,
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
    _audit(
        db, membership, restaurant.id, "RESTAURANT_CREATE_COUPON_GROUP",
        {"group_id": str(group.id), "code_prefix": prefix, "uses": body.uses},
    )
    log.info(
        "coupon group %s minted %d codes by restaurant %s",
        prefix, body.uses, restaurant.id,
    )
    return _group_out(db, group, scope)


@router.patch("/coupon-groups/{group_id}", response_model=CouponGroupOut)
def update_group(
    group_id: UUID,
    body: CouponGroupPatch,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(SETTINGS),
):
    """Pause, resume, or re-describe a group.

    Switching it carries to every code it minted: a paused group whose codes
    still worked would be a pause that paused nothing.
    """
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise errors.validation_error("No changes to save.")

    group = _find_group(db, group_id)
    if "description" in changes:
        group.description = (changes["description"] or "").strip() or None
    if "is_active" in changes:
        group_service.set_active(db, group, bool(changes["is_active"]))
    db.flush()

    headings = _headings(db)
    _audit(
        db, membership, restaurant.id, "RESTAURANT_UPDATE_COUPON_GROUP",
        {"group_id": str(group.id), "code_prefix": group.code_prefix,
         "changed": sorted(changes)},
    )
    return _group_out(db, group, _group_scope(db, group, headings))


@router.delete("/coupon-groups/{group_id}", status_code=204)
def retire_group(
    group_id: UUID,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(SETTINGS),
):
    """Retire a group and every code it minted.

    Soft, like retiring a coupon: the orders that used these codes keep their
    own snapshot, so last month's receipt still reads the same.
    """
    group = _find_group(db, group_id)
    group_service.retire(db, group)
    db.flush()
    _audit(
        db, membership, restaurant.id, "RESTAURANT_RETIRE_COUPON_GROUP",
        {"group_id": str(group.id), "code_prefix": group.code_prefix},
    )
    log.info("coupon group %s retired by restaurant %s", group.code_prefix, restaurant.id)


# ------------------------------------------------------------ welcome offer ---


def _signup_offer(db) -> CouponGroup | None:
    return db.execute(
        select(CouponGroup).where(
            CouponGroup.kind == "SIGNUP", CouponGroup.deleted_at.is_(None)
        )
    ).scalar_one_or_none()


@router.get("/signup-offer", response_model=SignupOfferStateOut)
def read_signup_offer(
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(SETTINGS),
):
    """The welcome offer, and whether this deployment can actually run it.

    `sms_configured` is what stops somebody switching on an offer whose only
    path to being claimed cannot send.
    """
    headings = _headings(db)
    group = _signup_offer(db)
    return SignupOfferStateOut(
        restaurant_name=restaurant.name,
        currency=restaurant.currency,
        item_types=headings,
        sms_configured=sms.configured(),
        offer=None if group is None else _signup_out(db, group, headings),
    )


@router.put("/signup-offer", response_model=SignupOfferOut)
def set_signup_offer(
    body: SignupOfferIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(SETTINGS),
):
    """Create the welcome offer, or change it.

    Codes already issued keep the terms they were given on: a customer was
    promised those, and an edit decides what the next one gets.
    """
    prefix = group_service.validate_prefix(body.code_prefix)
    _check_headings(db, body.applies_to)

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
            restaurant_id=restaurant.id, kind="SIGNUP", code_prefix=prefix, uses=None
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

    _audit(
        db, membership, restaurant.id,
        "RESTAURANT_CREATE_SIGNUP_OFFER" if created else "RESTAURANT_UPDATE_SIGNUP_OFFER",
        {"group_id": str(group.id), "code_prefix": prefix, "valid_days": body.valid_days},
    )
    log.info("signup offer %s on restaurant %s", prefix, restaurant.id)
    return _signup_out(db, group, _headings(db))


@router.delete("/signup-offer", status_code=204)
def retire_signup_offer(
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(SETTINGS),
):
    """End the welcome offer. Codes already issued stop working with it."""
    group = _signup_offer(db)
    if group is None:
        raise errors.ApiError(
            404, "SIGNUP_OFFER_NOT_FOUND", "There is no welcome offer here."
        )
    group_service.retire(db, group)
    db.flush()
    _audit(
        db, membership, restaurant.id, "RESTAURANT_RETIRE_SIGNUP_OFFER",
        {"group_id": str(group.id)},
    )


# ----------------------------------------------------------- who used what ----


@router.get("/coupon-redemptions", response_model=RedemptionListOut)
def list_redemptions(
    code: str | None = None,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(SETTINGS),
):
    """Every order here that used a code, newest first.

    `code` narrows it to one, which is how a group's MORE7 or one customer's
    welcome code is traced. Cancelled and expired orders are left out, for
    the reason redemptions are counted that way everywhere else: an order
    never paid for is not a promotion anybody received.
    """
    where = [
        Order.coupon_id.is_not(None),
        Order.status.notin_(coupon_service.SPENT_STATUSES),
    ]
    if code:
        where.append(Order.coupon_code == coupon_service.normalise(code))

    totals = db.execute(
        select(func.count(), func.coalesce(func.sum(Order.coupon_discount_minor), 0))
        .select_from(Order).where(*where)
    ).one()

    rows = db.execute(
        select(Order, User)
        .outerjoin(User, User.id == Order.customer_user_id)
        .where(*where)
        .order_by(Order.created_at.desc())
        .limit(MAX_REDEMPTIONS)
    ).all()

    return RedemptionListOut(
        restaurant_name=restaurant.name,
        currency=restaurant.currency,
        total_redemptions=totals[0],
        total_discounted_minor=totals[1],
        redemptions=[
            RedemptionOut(
                order_id=order.id,
                order_number=order.order_number,
                code=order.coupon_code or "",
                customer_name=None if user is None else user.full_name,
                customer_email=None if user is None else user.email,
                customer_phone=None if user is None else user.phone,
                is_guest=user is None or user.kind == UserKind.GUEST.value,
                discount_minor=order.coupon_discount_minor,
                order_total_minor=order.total_minor,
                status=order.status,
                created_at=order.created_at,
            )
            for order, user in rows
        ],
    )
