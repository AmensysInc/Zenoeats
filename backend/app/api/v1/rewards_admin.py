"""Rewards and offers, created by the platform rather than by the restaurant.

The same decision coupons took, for the same reason (api/v1/coupons.py says
it at length): what a promotion costs is the platform's business as much as
the kitchen's, so these are mounted on the admin router behind
require_platform_admin, on admin.<root domain>, and the restaurant's own
portal has no screen for either.

ON THE SESSION, which is the thing to get right in here: a platform admin is
a member of no restaurant, and the cross-tenant system surface is still the
wrong tool. Each endpoint takes the restaurant_id from the URL, opens a
tenant session on it, and does the work under FORCE ROW LEVEL SECURITY, so a
query that forgot its restaurant_id returns nothing rather than another
restaurant's program. The audit line is the one genuinely platform-owned
write, so it goes on a nested system session.

WHAT IS NOT HERE is the points. A balance is the count of a customer's
completed orders (services/rewards.py), so there is nothing to administer: no
granting, no adjusting, no zeroing. An operator who wants to change what a
customer has earned changes their orders, which is the only honest way to say
it. That is a deliberate absence, not a missing screen.
"""

import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import require_platform_admin
from app.core import errors
from app.models import Item, Offer, Reward, User
from app.services import images

# The audit writer the rest of the admin surface uses, imported rather than
# copied for the reason coupons.py gives: two places writing
# platform_audit_logs is two places to get its shape wrong.
from app.api.v1.admin import _audit
from app.db.session import system_session, tenant_session

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["rewards"])

# A ladder longer than this is not a ladder a customer reads, and it bounds
# what one page has to render.
MAX_REWARDS = 100
MAX_OFFERS = 50


# ---------------------------------------------------------------- shapes


class RewardIn(BaseModel):
    """One dish, free, from a threshold."""

    item_id: UUID
    points_required: int = Field(ge=1, le=10_000)
    sort_order: int = 0
    is_active: bool = True


class RewardPatch(BaseModel):
    """A partial update. Only what is sent changes."""

    item_id: UUID | None = None
    points_required: int | None = Field(default=None, ge=1, le=10_000)
    sort_order: int | None = None
    is_active: bool | None = None


class RewardAdminOut(BaseModel):
    id: UUID
    item_id: UUID
    item_name: str
    points_required: int
    sort_order: int
    is_active: bool
    # Whether the dish itself can be served. A reward on a sold-out dish is
    # worth flagging in the admin list: it stays on the ladder and cannot be
    # claimed, which is a thing an operator should see rather than discover.
    item_is_available: bool


class MenuDishOut(BaseModel):
    """A dish the form can choose from."""

    id: UUID
    name: str
    base_price_minor: int


class RewardListOut(BaseModel):
    restaurant_name: str
    currency: str
    # Every live dish, for the "which dish" field.
    items: list[MenuDishOut]
    rewards: list[RewardAdminOut]


class OfferIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    body: str | None = Field(default=None, max_length=500)
    image_path: str | None = Field(default=None, max_length=500)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    is_active: bool = True
    sort_order: int = 0

    @field_validator("title", "body")
    @classmethod
    def _tidy(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split()).strip()
        return cleaned or None

    @model_validator(mode="after")
    def _window(self):
        # Refused here for the message; the check constraint is what
        # guarantees it.
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("The offer has to end after it starts.")
        return self


class OfferPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    body: str | None = Field(default=None, max_length=500)
    image_path: str | None = Field(default=None, max_length=500)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    is_active: bool | None = None
    sort_order: int | None = None


class OfferAdminOut(BaseModel):
    id: UUID
    title: str
    body: str | None
    image_path: str | None
    image_url: str | None
    starts_at: datetime | None
    ends_at: datetime | None
    is_active: bool
    sort_order: int
    # Whether a customer would see it at this moment. The window plus the
    # switch, worked out in one place (Offer.is_live) so the admin list and
    # the storefront cannot disagree about what "running" means.
    is_live: bool


class OfferListOut(BaseModel):
    restaurant_name: str
    offers: list[OfferAdminOut]


# ---------------------------------------------------------------- helpers


def _restaurant(db: Session, restaurant_id: UUID):
    """The restaurant this program belongs to, or 404.

    Read inside the tenant session, so row-level security is what proves the
    row is the one the URL named.
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


def _dish(db: Session, item_id: UUID) -> Item:
    """A dish of this restaurant, or 404. Live only: a reward on a deleted
    dish is a reward nobody can be given."""
    item = db.execute(
        select(Item).where(Item.id == item_id, Item.deleted_at.is_(None))
    ).scalar_one_or_none()
    if item is None:
        raise errors.validation_error("That dish is not on this restaurant's menu.")
    return item


def _reward(db: Session, reward_id: UUID) -> Reward:
    reward = db.execute(
        select(Reward).where(Reward.id == reward_id, Reward.deleted_at.is_(None))
    ).scalar_one_or_none()
    if reward is None:
        raise errors.ApiError(404, "REWARD_NOT_FOUND", "No such reward.")
    return reward


def _offer(db: Session, offer_id: UUID) -> Offer:
    offer = db.execute(
        select(Offer).where(Offer.id == offer_id, Offer.deleted_at.is_(None))
    ).scalar_one_or_none()
    if offer is None:
        raise errors.ApiError(404, "OFFER_NOT_FOUND", "No such offer.")
    return offer


def _reward_out(reward: Reward, item: Item) -> RewardAdminOut:
    return RewardAdminOut(
        id=reward.id,
        item_id=item.id,
        item_name=item.name,
        points_required=reward.points_required,
        sort_order=reward.sort_order,
        is_active=reward.is_active,
        item_is_available=item.is_available,
    )


def _offer_out(offer: Offer, now: datetime) -> OfferAdminOut:
    return OfferAdminOut(
        id=offer.id,
        title=offer.title,
        body=offer.body,
        image_path=offer.image_path,
        image_url=images.image_url(offer.image_path),
        starts_at=offer.starts_at,
        ends_at=offer.ends_at,
        is_active=offer.is_active,
        sort_order=offer.sort_order,
        is_live=offer.is_live(now),
    )


def _clash(db: Session, restaurant_id: UUID, item_id: UUID, points: int, skip: UUID | None):
    """The same dish twice at one threshold is a mistake, not two rewards.

    Checked for the message; the partial unique index is the guarantee, and
    it covers live rows only so a retired reward may be created again.
    """
    query = select(Reward.id).where(
        Reward.item_id == item_id,
        Reward.points_required == points,
        Reward.deleted_at.is_(None),
    )
    if skip is not None:
        query = query.where(Reward.id != skip)
    if db.execute(query).scalar_one_or_none() is not None:
        raise errors.validation_error(
            "That dish is already a reward at that number of points."
        )


# ---------------------------------------------------------------- rewards


@router.get("/restaurants/{restaurant_id}/rewards", response_model=RewardListOut)
def list_rewards(restaurant_id: UUID, admin: User = Depends(require_platform_admin)):
    """One restaurant's reward ladder, lowest threshold first, with the menu
    the form needs.

    Not audited: a read of the platform's own configuration that exposes
    nothing a customer typed and no money that moved.
    """
    with tenant_session(restaurant_id) as db:
        restaurant = _restaurant(db, restaurant_id)
        rows = db.execute(
            select(Reward, Item)
            .join(Item, Item.id == Reward.item_id)
            .where(Reward.deleted_at.is_(None))
            .order_by(Reward.points_required, Reward.sort_order, Reward.created_at, Reward.id)
            .limit(MAX_REWARDS)
        ).all()
        dishes = db.execute(
            select(Item).where(Item.deleted_at.is_(None)).order_by(Item.name, Item.id)
        ).scalars().all()
        return RewardListOut(
            restaurant_name=restaurant["name"],
            currency=restaurant["currency"],
            items=[
                MenuDishOut(id=d.id, name=d.name, base_price_minor=d.base_price_minor)
                for d in dishes
            ],
            rewards=[_reward_out(reward, item) for reward, item in rows],
        )


@router.post(
    "/restaurants/{restaurant_id}/rewards",
    response_model=RewardAdminOut,
    status_code=201,
)
def create_reward(
    restaurant_id: UUID, body: RewardIn, admin: User = Depends(require_platform_admin)
):
    """Put a dish on the ladder at a threshold."""
    with tenant_session(restaurant_id) as db:
        _restaurant(db, restaurant_id)
        live = db.execute(
            select(func.count()).select_from(Reward).where(Reward.deleted_at.is_(None))
        ).scalar_one()
        if live >= MAX_REWARDS:
            raise errors.validation_error(
                f"That is {MAX_REWARDS} rewards. Retire one before adding another."
            )
        item = _dish(db, body.item_id)
        _clash(db, restaurant_id, body.item_id, body.points_required, None)

        reward = Reward(
            restaurant_id=restaurant_id,
            item_id=item.id,
            points_required=body.points_required,
            sort_order=body.sort_order,
            is_active=body.is_active,
        )
        db.add(reward)
        db.flush()

        with system_session() as platform:
            _audit(
                platform, admin, "SUPER_ADMIN_CREATE_REWARD",
                {
                    "restaurant_id": str(restaurant_id),
                    "reward_id": str(reward.id),
                    "item": item.name,
                    "points_required": reward.points_required,
                },
            )
        log.info(
            "reward %s at %s points created on restaurant %s",
            item.name, reward.points_required, restaurant_id,
        )
        return _reward_out(reward, item)


@router.patch(
    "/restaurants/{restaurant_id}/rewards/{reward_id}", response_model=RewardAdminOut
)
def update_reward(
    restaurant_id: UUID,
    reward_id: UUID,
    body: RewardPatch,
    admin: User = Depends(require_platform_admin),
):
    """Change a reward: its dish, its threshold, its order, or switch it off."""
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise errors.validation_error("No changes to save.")

    with tenant_session(restaurant_id) as db:
        _restaurant(db, restaurant_id)
        reward = _reward(db, reward_id)
        item = _dish(db, changes.get("item_id") or reward.item_id)
        _clash(
            db, restaurant_id, item.id,
            changes.get("points_required") or reward.points_required,
            reward.id,
        )
        for field, value in changes.items():
            setattr(reward, field, value)
        db.flush()

        with system_session() as platform:
            _audit(
                platform, admin, "SUPER_ADMIN_UPDATE_REWARD",
                {
                    "restaurant_id": str(restaurant_id),
                    "reward_id": str(reward.id),
                    **{k: str(v) for k, v in changes.items()},
                },
            )
        return _reward_out(reward, item)


@router.delete("/restaurants/{restaurant_id}/rewards/{reward_id}", status_code=204)
def delete_reward(
    restaurant_id: UUID, reward_id: UUID, admin: User = Depends(require_platform_admin)
):
    """Take a reward off the ladder.

    A soft delete, like a coupon's: orders that were given this dish free
    keep their own line and their own figures, and the row stays so an
    operator asking what the ladder used to offer gets an answer.
    """
    with tenant_session(restaurant_id) as db:
        _restaurant(db, restaurant_id)
        reward = _reward(db, reward_id)
        reward.deleted_at = func.now()
        db.flush()

        with system_session() as platform:
            _audit(
                platform, admin, "SUPER_ADMIN_DELETE_REWARD",
                {"restaurant_id": str(restaurant_id), "reward_id": str(reward_id)},
            )
    return None


# ----------------------------------------------------------------- offers


@router.get("/restaurants/{restaurant_id}/offers", response_model=OfferListOut)
def list_offers(restaurant_id: UUID, admin: User = Depends(require_platform_admin)):
    """One restaurant's offers, in its own order, each saying whether a
    customer can see it now."""
    now = datetime.now(tz=None).astimezone()
    with tenant_session(restaurant_id) as db:
        restaurant = _restaurant(db, restaurant_id)
        rows = db.execute(
            select(Offer)
            .where(Offer.deleted_at.is_(None))
            .order_by(Offer.sort_order, Offer.created_at, Offer.id)
            .limit(MAX_OFFERS)
        ).scalars().all()
        return OfferListOut(
            restaurant_name=restaurant["name"],
            offers=[_offer_out(offer, now) for offer in rows],
        )


@router.post(
    "/restaurants/{restaurant_id}/offers",
    response_model=OfferAdminOut,
    status_code=201,
)
def create_offer(
    restaurant_id: UUID, body: OfferIn, admin: User = Depends(require_platform_admin)
):
    """Write an offer. It shows from the moment it is saved, unless it carries
    a start date or was saved switched off."""
    now = datetime.now(tz=None).astimezone()
    with tenant_session(restaurant_id) as db:
        _restaurant(db, restaurant_id)
        live = db.execute(
            select(func.count()).select_from(Offer).where(Offer.deleted_at.is_(None))
        ).scalar_one()
        if live >= MAX_OFFERS:
            raise errors.validation_error(
                f"That is {MAX_OFFERS} offers. Retire one before adding another."
            )
        offer = Offer(
            restaurant_id=restaurant_id,
            title=body.title,
            body=body.body,
            image_path=body.image_path,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
            is_active=body.is_active,
            sort_order=body.sort_order,
        )
        db.add(offer)
        db.flush()

        with system_session() as platform:
            _audit(
                platform, admin, "SUPER_ADMIN_CREATE_OFFER",
                {
                    "restaurant_id": str(restaurant_id),
                    "offer_id": str(offer.id),
                    "title": offer.title,
                },
            )
        log.info("offer %r created on restaurant %s", offer.title, restaurant_id)
        return _offer_out(offer, now)


@router.patch(
    "/restaurants/{restaurant_id}/offers/{offer_id}", response_model=OfferAdminOut
)
def update_offer(
    restaurant_id: UUID,
    offer_id: UUID,
    body: OfferPatch,
    admin: User = Depends(require_platform_admin),
):
    """Change an offer's words, picture, window or switch."""
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise errors.validation_error("No changes to save.")
    now = datetime.now(tz=None).astimezone()

    with tenant_session(restaurant_id) as db:
        _restaurant(db, restaurant_id)
        offer = _offer(db, offer_id)
        for field, value in changes.items():
            setattr(offer, field, value)
        # Read back off the row rather than the patch: a window is only valid
        # as a pair, and one end may have come from the patch and the other
        # from what was already there.
        if offer.starts_at and offer.ends_at and offer.ends_at <= offer.starts_at:
            raise errors.validation_error("The offer has to end after it starts.")
        db.flush()

        with system_session() as platform:
            _audit(
                platform, admin, "SUPER_ADMIN_UPDATE_OFFER",
                {
                    "restaurant_id": str(restaurant_id),
                    "offer_id": str(offer.id),
                    **{k: str(v) for k, v in changes.items()},
                },
            )
        return _offer_out(offer, now)


@router.delete("/restaurants/{restaurant_id}/offers/{offer_id}", status_code=204)
def delete_offer(
    restaurant_id: UUID, offer_id: UUID, admin: User = Depends(require_platform_admin)
):
    """Retire an offer. Soft, so what was advertised stays answerable."""
    with tenant_session(restaurant_id) as db:
        _restaurant(db, restaurant_id)
        offer = _offer(db, offer_id)
        offer.deleted_at = func.now()
        db.flush()

        with system_session() as platform:
            _audit(
                platform, admin, "SUPER_ADMIN_DELETE_OFFER",
                {"restaurant_id": str(restaurant_id), "offer_id": str(offer_id)},
            )
    return None
