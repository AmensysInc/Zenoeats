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

WHAT IS NOT HERE is any way to grant or adjust a customer's stamps. The
ledger is the truth and it is written by orders completing and refunds
reversing them (services/loyalty.py); a button here would be a second way to
move the same number, and the two would disagree the first time somebody
used it. An operator who wants to change what a customer earned changes
their orders. That is a deliberate absence, not a missing screen.
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
from app.models import (
    Item, ItemType, LoyaltyProgram, LoyaltyProgramItem, Offer, PurchaseType, User,
)
from app.services import images

# The audit writer the rest of the admin surface uses, imported rather than
# copied for the reason coupons.py gives: two places writing
# platform_audit_logs is two places to get its shape wrong.
from app.api.v1.admin import _audit
from app.db.session import system_session, tenant_session

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["rewards"])

# More programs than this is not a scheme a customer can hold in their head,
# and it bounds what one page has to render.
MAX_PROGRAMS = 20
MAX_OFFERS = 50


# ---------------------------------------------------------------- shapes


class ProgramIn(BaseModel):
    """A loyalty program as the form sends it.

    `choices` is the dishes the reward may be taken as -- at least one, or
    the program wins nothing. The qualifying fields are read only by
    MENU_ITEM and exactly one of them is set; the check constraint refuses
    the rest, and _qualifier below says so in words first.
    """

    name: str = Field(min_length=1, max_length=120)
    purchase_type: str = Field(default=PurchaseType.COMPLETED_ORDERS.value)
    required_count: int = Field(ge=1, le=1000)
    minimum_spend_minor: int = Field(default=0, ge=0)
    qualifying_item_id: UUID | None = None
    qualifying_item_type_id: UUID | None = None
    reward_expires_days: int | None = Field(default=None, ge=1, le=3650)
    is_active: bool = True
    sort_order: int = 0
    choices: list[UUID] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _tidy(cls, value: str) -> str:
        cleaned = " ".join(value.split()).strip()
        if not cleaned:
            raise ValueError("A program needs a name.")
        return cleaned

    @field_validator("purchase_type")
    @classmethod
    def _known(cls, value: str) -> str:
        if value not in {t.value for t in PurchaseType}:
            raise ValueError("That is not a kind of qualifying purchase.")
        return value


class ProgramPatch(BaseModel):
    """A partial update. Only what is sent changes; `choices` replaces the
    whole list when it is sent at all."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    required_count: int | None = Field(default=None, ge=1, le=1000)
    minimum_spend_minor: int | None = Field(default=None, ge=0)
    reward_expires_days: int | None = Field(default=None, ge=1, le=3650)
    is_active: bool | None = None
    sort_order: int | None = None
    choices: list[UUID] | None = None


class ProgramChoiceOut(BaseModel):
    item_id: UUID
    item_name: str
    is_available: bool


class ProgramOut(BaseModel):
    id: UUID
    name: str
    purchase_type: str
    required_count: int
    minimum_spend_minor: int
    qualifying_item_id: UUID | None
    qualifying_item_type_id: UUID | None
    # What the rule reads as in words, so the list does not make an operator
    # reassemble it from five fields.
    summary: str
    reward_expires_days: int | None
    is_active: bool
    sort_order: int
    choices: list[ProgramChoiceOut]


class MenuDishOut(BaseModel):
    """A dish the form can choose from."""

    id: UUID
    name: str
    base_price_minor: int


class MenuHeadingOut(BaseModel):
    id: UUID
    name: str


class ProgramListOut(BaseModel):
    restaurant_name: str
    currency: str
    items: list[MenuDishOut]
    headings: list[MenuHeadingOut]
    programs: list[ProgramOut]


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
    """A live dish of this restaurant, or a refusal."""
    item = db.execute(
        select(Item).where(Item.id == item_id, Item.deleted_at.is_(None))
    ).scalar_one_or_none()
    if item is None:
        raise errors.validation_error("That dish is not on this restaurant's menu.")
    return item


def _program(db: Session, program_id: UUID) -> LoyaltyProgram:
    program = db.execute(
        select(LoyaltyProgram).where(
            LoyaltyProgram.id == program_id, LoyaltyProgram.deleted_at.is_(None)
        )
    ).scalar_one_or_none()
    if program is None:
        raise errors.ApiError(404, "PROGRAM_NOT_FOUND", "No such loyalty program.")
    return program


def _offer(db: Session, offer_id: UUID) -> Offer:
    offer = db.execute(
        select(Offer).where(Offer.id == offer_id, Offer.deleted_at.is_(None))
    ).scalar_one_or_none()
    if offer is None:
        raise errors.ApiError(404, "OFFER_NOT_FOUND", "No such offer.")
    return offer


def _qualifier(db: Session, body: ProgramIn) -> tuple[UUID | None, UUID | None]:
    """What a MENU_ITEM program counts, refused in words before the check
    constraint refuses it in Postgres.

    Exactly one of a dish or a heading. "Nine burgers" means one of those two
    and a restaurant means different things by it on different weeks, so it
    has to say which rather than have the engine guess.
    """
    if body.purchase_type != PurchaseType.MENU_ITEM.value:
        if body.qualifying_item_id or body.qualifying_item_type_id:
            raise errors.validation_error(
                "A program counting orders does not also name a dish to count."
            )
        return None, None

    named = [bool(body.qualifying_item_id), bool(body.qualifying_item_type_id)]
    if sum(named) != 1:
        raise errors.validation_error(
            "Counting purchases needs either one dish or one menu heading, not both."
        )
    if body.qualifying_item_id:
        return _dish(db, body.qualifying_item_id).id, None

    heading = db.execute(
        select(ItemType).where(
            ItemType.id == body.qualifying_item_type_id, ItemType.deleted_at.is_(None)
        )
    ).scalar_one_or_none()
    if heading is None:
        raise errors.validation_error("That heading is not on this restaurant's menu.")
    return None, heading.id


def _set_choices(db: Session, restaurant_id: UUID, program: LoyaltyProgram, items: list[UUID]):
    """Replace the dishes this program's reward may be taken as.

    At least one, because a program that wins nothing is a card nobody would
    fill. Replaced as a set rather than patched one at a time: the form
    sends what the list should be, and a diff here would be a second way to
    express the same thing.
    """
    wanted = list(dict.fromkeys(items))
    if not wanted:
        raise errors.validation_error(
            "Choose at least one dish the reward can be taken as."
        )
    for existing in list(program.choices):
        program.choices.remove(existing)
    db.flush()
    for order, item_id in enumerate(wanted):
        dish = _dish(db, item_id)
        db.add(
            LoyaltyProgramItem(
                restaurant_id=restaurant_id,
                program_id=program.id,
                item_id=dish.id,
                sort_order=order,
            )
        )
    db.flush()


def _summary(db: Session, program: LoyaltyProgram) -> str:
    """The rule in a sentence, built server-side so the admin list and any
    later digest read it the same way."""
    if program.purchase_type == PurchaseType.COMPLETED_ORDERS.value:
        spend = (
            f" of at least {program.minimum_spend_minor / 100:.2f}"
            if program.minimum_spend_minor
            else ""
        )
        earning = f"{program.required_count} completed orders{spend}"
    elif program.qualifying_item_id:
        dish = db.get(Item, program.qualifying_item_id)
        earning = f"{program.required_count} \u00d7 {dish.name if dish else 'a dish'}"
    else:
        heading = db.get(ItemType, program.qualifying_item_type_id)
        earning = f"{program.required_count} items from {heading.name if heading else 'a heading'}"

    names = [c.item_name for c in _choices_out(db, program)]
    reward = " or ".join(names) if names else "nothing yet"
    lapses = (
        f", expiring {program.reward_expires_days} days after it is won"
        if program.reward_expires_days
        else ""
    )
    return f"After {earning}, the customer may have {reward} free{lapses}."


def _choices_out(db: Session, program: LoyaltyProgram) -> list[ProgramChoiceOut]:
    rows = db.execute(
        select(LoyaltyProgramItem, Item)
        .join(Item, Item.id == LoyaltyProgramItem.item_id)
        .where(LoyaltyProgramItem.program_id == program.id, Item.deleted_at.is_(None))
        .order_by(LoyaltyProgramItem.sort_order, LoyaltyProgramItem.id)
    ).all()
    return [
        ProgramChoiceOut(item_id=i.id, item_name=i.name, is_available=i.is_available)
        for _link, i in rows
    ]


def _program_out(db: Session, program: LoyaltyProgram) -> ProgramOut:
    return ProgramOut(
        id=program.id,
        name=program.name,
        purchase_type=program.purchase_type,
        required_count=program.required_count,
        minimum_spend_minor=program.minimum_spend_minor,
        qualifying_item_id=program.qualifying_item_id,
        qualifying_item_type_id=program.qualifying_item_type_id,
        summary=_summary(db, program),
        reward_expires_days=program.reward_expires_days,
        is_active=program.is_active,
        sort_order=program.sort_order,
        choices=_choices_out(db, program),
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


# --------------------------------------------------------------- programs


@router.get("/restaurants/{restaurant_id}/rewards", response_model=ProgramListOut)
def list_programs(restaurant_id: UUID, admin: User = Depends(require_platform_admin)):
    """One restaurant's loyalty programs, with the menu the form needs.

    Not audited: a read of the platform's own configuration that exposes
    nothing a customer typed and no money that moved.
    """
    with tenant_session(restaurant_id) as db:
        restaurant = _restaurant(db, restaurant_id)
        programs = db.execute(
            select(LoyaltyProgram)
            .where(LoyaltyProgram.deleted_at.is_(None))
            .order_by(LoyaltyProgram.sort_order, LoyaltyProgram.created_at, LoyaltyProgram.id)
            .limit(MAX_PROGRAMS)
        ).scalars().all()
        dishes = db.execute(
            select(Item).where(Item.deleted_at.is_(None)).order_by(Item.name, Item.id)
        ).scalars().all()
        headings = db.execute(
            select(ItemType)
            .where(ItemType.deleted_at.is_(None))
            .order_by(ItemType.sort_order, ItemType.id)
        ).scalars().all()
        return ProgramListOut(
            restaurant_name=restaurant["name"],
            currency=restaurant["currency"],
            items=[
                MenuDishOut(id=d.id, name=d.name, base_price_minor=d.base_price_minor)
                for d in dishes
            ],
            headings=[MenuHeadingOut(id=h.id, name=h.name) for h in headings],
            programs=[_program_out(db, p) for p in programs],
        )


@router.post(
    "/restaurants/{restaurant_id}/rewards", response_model=ProgramOut, status_code=201
)
def create_program(
    restaurant_id: UUID, body: ProgramIn, admin: User = Depends(require_platform_admin)
):
    """Start a loyalty program. It runs from the moment it is saved, unless
    it was saved switched off."""
    with tenant_session(restaurant_id) as db:
        _restaurant(db, restaurant_id)
        live = db.execute(
            select(func.count())
            .select_from(LoyaltyProgram)
            .where(LoyaltyProgram.deleted_at.is_(None))
        ).scalar_one()
        if live >= MAX_PROGRAMS:
            raise errors.validation_error(
                f"That is {MAX_PROGRAMS} programs. Retire one before adding another."
            )
        item_id, type_id = _qualifier(db, body)

        program = LoyaltyProgram(
            restaurant_id=restaurant_id,
            name=body.name,
            purchase_type=body.purchase_type,
            required_count=body.required_count,
            minimum_spend_minor=(
                body.minimum_spend_minor
                if body.purchase_type == PurchaseType.COMPLETED_ORDERS.value
                else 0
            ),
            qualifying_item_id=item_id,
            qualifying_item_type_id=type_id,
            reward_expires_days=body.reward_expires_days,
            is_active=body.is_active,
            sort_order=body.sort_order,
        )
        db.add(program)
        db.flush()
        _set_choices(db, restaurant_id, program, body.choices)

        with system_session() as platform:
            _audit(
                platform, admin, "SUPER_ADMIN_CREATE_LOYALTY_PROGRAM",
                {
                    "restaurant_id": str(restaurant_id),
                    "program_id": str(program.id),
                    "name": program.name,
                    "purchase_type": program.purchase_type,
                    "required_count": program.required_count,
                },
            )
        log.info("loyalty program %r created on restaurant %s", program.name, restaurant_id)
        return _program_out(db, program)


@router.patch(
    "/restaurants/{restaurant_id}/rewards/{program_id}", response_model=ProgramOut
)
def update_program(
    restaurant_id: UUID,
    program_id: UUID,
    body: ProgramPatch,
    admin: User = Depends(require_platform_admin),
):
    """Change a program.

    What a program counts cannot change: the cards already filling against
    it were earned under the old rule, and rewriting the rule under them
    would silently restate what every customer has done. Retire it and start
    another instead.
    """
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise errors.validation_error("No changes to save.")
    choices = changes.pop("choices", None)

    with tenant_session(restaurant_id) as db:
        _restaurant(db, restaurant_id)
        program = _program(db, program_id)
        for field, value in changes.items():
            setattr(program, field, value)
        if choices is not None:
            _set_choices(db, restaurant_id, program, choices)
        db.flush()

        with system_session() as platform:
            _audit(
                platform, admin, "SUPER_ADMIN_UPDATE_LOYALTY_PROGRAM",
                {
                    "restaurant_id": str(restaurant_id),
                    "program_id": str(program.id),
                    **{k: str(v) for k, v in changes.items()},
                },
            )
        return _program_out(db, program)


@router.delete("/restaurants/{restaurant_id}/rewards/{program_id}", status_code=204)
def delete_program(
    restaurant_id: UUID, program_id: UUID, admin: User = Depends(require_platform_admin)
):
    """Retire a program.

    Soft, and the ledger is untouched: stamps already earned stay earned and
    rewards already won stay spendable. Ending a promotion is not the same
    as taking back what it gave, and a customer holding a free burger should
    still get it.
    """
    with tenant_session(restaurant_id) as db:
        _restaurant(db, restaurant_id)
        program = _program(db, program_id)
        program.deleted_at = func.now()
        program.is_active = False
        db.flush()

        with system_session() as platform:
            _audit(
                platform, admin, "SUPER_ADMIN_DELETE_LOYALTY_PROGRAM",
                {"restaurant_id": str(restaurant_id), "program_id": str(program_id)},
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
