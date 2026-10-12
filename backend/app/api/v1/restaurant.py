"""Restaurant staff API: menu management and the live order board.

Every endpoint runs under the tenant-scoped session. Even if the role check
were removed, RLS would return zero rows for another restaurant's data.
"""

import logging
from datetime import date, datetime, time, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, selectinload

from app.api.deps import (
    StaffDb, TenantContext, current_restaurant_staff, current_staff_user,
    current_staff_user_ready, require_staff, resolve_tenant_staff,
)
from app.config import settings
from app.core import crypto, errors, staff_auth
from app.core.logsafe import email_for_log
from app.core import ratelimit
from app.core.ratelimit import per_ip, per_staff_user
from app.db.base import utcnow
from app.db.session import system_session, tenant_session
from app.models import (
    Combo, ComboSlot, ComboSlotItem, DeliveryZone, DiscountKind, Item,
    ItemIncludedOption,
    ItemModifierGroup, ItemType, Meal, MealItem, ModifierGroup,
    FulfillmentType, ModifierGroupItemType, ModifierOption, Order, OrderEvent,
    OrderEventAction, OrderItem, OrderStatus, Payment, PaymentStatus, Restaurant,
    RestaurantUser, SelectionType, StaffRole, StaffStatus,
    User, UserKind,
)
from app.schemas.api import (
    ChangePasswordIn, MenuOut, StaffInviteOut, StaffLoginIn, StaffMeOut,
    StaffPasswordResetOut, known_timezone,
)
from app.services import geocoding, images, restaurant_profile, stripe_service, tracking, storefront
from app.services import email as email_service
from app.services import order_emails, staff_emails
from app.schemas.storefront import ThemePatch, CategoryPatch, BannersIn, CollectionsIn, MapPatch, ShortcutsIn
from app.services.images import ImageKind
from app.services.menu import load_item_types, load_menu
from app.services.orders import transition

log = logging.getLogger(__name__)
router = APIRouter(prefix="/restaurant", tags=["restaurant"])


@router.post(
    "/login",
    response_model=StaffMeOut,
    # Unauthenticated and credential-bearing. Tight, and keyed by address
    # because there is no session yet to key on.
    dependencies=[Depends(per_ip("staff_login", limit=10, window_seconds=300))],
)
def staff_login(
    body: StaffLoginIn,
    response: Response,
    tenant: TenantContext = Depends(resolve_tenant_staff),
):
    """Sign in restaurant staff.

    Authentication and authorization are separate steps on purpose. The
    password proves who you are; membership of *this* restaurant, read from
    restaurant_users under RLS, decides whether you may be here. Staff of
    another restaurant therefore get the same refusal as a wrong password,
    and learn nothing about which subdomain they do belong to.
    """
    email = body.email.strip().lower()
    # Before anything is looked up, so a spent account cannot keep guessing.
    if ratelimit.sign_in_blocked("staff_login", email):
        log.warning("staff sign-in throttled for %s", email_for_log(email))
        raise ratelimit.sign_in_throttled()

    with system_session() as session:
        user = session.execute(
            select(User).where(
                User.email == email,
                User.kind == UserKind.STAFF.value,
                User.password_hash.isnot(None),
            )
        ).scalar_one_or_none()
        if user is None:
            staff_auth.dummy_verify(body.password)
            # Logged and counted like a wrong password: a list of addresses
            # tried one after another is the pattern worth seeing, and the
            # budget must not tell a real account from an invented one.
            log.warning("failed staff sign-in for unknown %s", email_for_log(email))
            ratelimit.sign_in_failed("staff_login", email)
            raise errors.ApiError(401, "INVALID_CREDENTIALS", "Email or password is incorrect.")
        digest = user.password_hash
        user_id = user.id
        full_name = user.full_name
        active = user.is_active and user.deleted_at is None
        must_change = user.must_change_password

    if not staff_auth.verify_password(digest, body.password):
        log.warning("failed staff sign-in for %s", email_for_log(email))
        ratelimit.sign_in_failed("staff_login", email)
        raise errors.ApiError(401, "INVALID_CREDENTIALS", "Email or password is incorrect.")
    if not active:
        raise errors.ApiError(403, "ACCOUNT_INACTIVE", "This account is not active.")

    with tenant_session(tenant.restaurant_id) as session:
        # INVITED as well as ACTIVE: accepting an invitation happens inside the
        # portal, so the invitee has to be able to sign in to reach it. Every
        # other staff endpoint still requires ACTIVE (require_staff), so an
        # invited session can see the invitation and nothing else.
        membership = _membership(session, user_id)
        if membership is None:
            log.warning("staff %s has no membership at %s", email_for_log(email), tenant.slug)
            raise errors.ApiError(401, "INVALID_CREDENTIALS", "Email or password is incorrect.")
        role_code = membership.role_code
        membership_status = membership.status

        # Read in the same tenant session as the membership, so RLS is what
        # proves this is the caller's restaurant. Sign-in returns the same
        # shape as /me deliberately: the portal paints its header from this
        # response, and leaving the name out meant a blank header until the
        # first /me landed.
        restaurant = session.get(Restaurant, tenant.restaurant_id)
        if restaurant is None:
            raise errors.tenant_scope_denied()
        restaurant_name = restaurant.name
        storefront_enabled = restaurant.storefront_customization_enabled

    response.set_cookie(
        key=staff_auth.SESSION_COOKIE,
        value=staff_auth.issue_session(user_id),
        max_age=settings.STAFF_SESSION_TTL_MINUTES * 60,
        httponly=True,
        samesite="lax",
        secure=settings.ENV == "production",
        path="/",
    )
    return StaffMeOut(
        user_id=user_id, email=email, full_name=full_name,
        role_code=role_code, must_change_password=must_change,
        restaurant_name=restaurant_name, membership_status=membership_status,
        storefront_customization_enabled=storefront_enabled,
    )


def _membership(session: Session, user_id) -> RestaurantUser | None:
    """This person's live membership of the tenant the session is scoped to.
    Read under RLS, so another restaurant's row cannot come back."""
    return session.execute(
        select(RestaurantUser).where(
            RestaurantUser.user_id == user_id,
            RestaurantUser.status.in_([StaffStatus.ACTIVE.value, StaffStatus.INVITED.value]),
        )
    ).scalar_one_or_none()


@router.post("/logout", status_code=204)
def staff_logout(response: Response):
    response.delete_cookie(
        key=staff_auth.SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.ENV == "production",
    )


@router.post("/logout-everywhere", status_code=204)
def staff_logout_everywhere(
    response: Response,
    user: User = Depends(current_staff_user),
):
    """End every session this account holds, on every device.

    Ordinary sign-out stays with the device it is pressed on, on purpose: a
    restaurant often shares one login across the kitchen tablets, and one
    person leaving must not sign the tablet on the pass out mid-service. But
    that left no answer to a lost phone, or to a session left open on someone
    else's device -- short of changing the password. This is that answer
    without the password change: every session issued before now is refused,
    by the same sessions_valid_after check a password change uses.

    Needs only a session, not a role, so an account still holding a temporary
    password can use it too.
    """
    with system_session() as session:
        row = session.get(User, user.id)
        if row is not None:
            row.sessions_valid_after = utcnow()
    log.info("staff %s signed out of every device", email_for_log(user.email))
    response.delete_cookie(
        key=staff_auth.SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.ENV == "production",
    )


@router.get("/me", response_model=StaffMeOut)
def staff_me(
    user: User = Depends(current_staff_user),
    tenant: TenantContext = Depends(resolve_tenant_staff),
):
    """Who the caller is, and whether they still owe a password change.

    Uses current_staff_user rather than the ready variant so the frontend can
    ask this while holding a temporary password and route to the change form.
    """
    with tenant_session(tenant.restaurant_id) as session:
        membership = _membership(session, user.id)
        if membership is None:
            raise errors.tenant_scope_denied()
        role_code = membership.role_code
        membership_status = membership.status

        # Read inside the same tenant session, so RLS is what proves this is
        # the restaurant the caller is scoped to rather than any lookup by id.
        restaurant = session.get(Restaurant, tenant.restaurant_id)
        if restaurant is None:
            raise errors.tenant_scope_denied()
        restaurant_name = restaurant.name
        storefront_enabled = restaurant.storefront_customization_enabled

    return StaffMeOut(
        user_id=user.id, email=user.email, full_name=user.full_name,
        role_code=role_code, must_change_password=user.must_change_password,
        restaurant_name=restaurant_name, membership_status=membership_status,
        storefront_customization_enabled=storefront_enabled,
    )


@router.post(
    "/change-password",
    status_code=204,
    # Checks the current password, so it is a password oracle for whoever
    # holds a session. Rare for a person; limited for a script.
    dependencies=[Depends(per_staff_user("change_password", limit=10, window_seconds=900))],
)
def change_password(
    body: ChangePasswordIn,
    response: Response,
    user: User = Depends(current_staff_user),
):
    """Set a new password.

    Requires the current one even though the session already proves identity:
    it is what stops an unattended signed-in tablet being turned into a
    permanent account takeover.

    This is not a reset. Forgotten passwords are reissued by the super admin
    until there is an email provider to send a reset link through.
    """
    with system_session() as session:
        row = session.get(User, user.id)
        if row is None or row.password_hash is None:
            raise errors.ApiError(401, "UNAUTHENTICATED", "Sign in to continue.")
        if not staff_auth.verify_password(row.password_hash, body.current_password):
            raise errors.ApiError(401, "INVALID_CREDENTIALS", "Current password is incorrect.")
        if body.new_password == body.current_password:
            raise errors.validation_error("Choose a password you have not used here before.")

        row.password_hash = staff_auth.hash_password(body.new_password)
        row.must_change_password = False
        # Every session issued under the old password ends here, on every
        # device -- not just this one's cookie.
        row.sessions_valid_after = utcnow()

    # sessions_valid_after already ended every session on the server; clearing
    # this cookie too sends the browser straight to sign in with the new one.
    response.delete_cookie(
        key=staff_auth.SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.ENV == "production",
    )


# Who may call what. Every endpoint below depends on exactly one of these,
# apart from the few sign-in endpoints that come before a role can exist, and
# tests/test_role_coverage.py fails if a new endpoint forgets. The portal
# mirrors these lists in web/src/features/restaurant/nav.ts.
#
# ANY_STAFF is every role, the floor included. It used to be called KITCHEN,
# which read as "kitchen staff only" to anyone adding an endpoint.
MANAGE = require_staff(StaffRole.ADMIN, StaffRole.MANAGER)
# Deliberately not DRIVER: a driver has no business on the board, in the
# menu, in stock or in reports. Everything they may do is under DELIVERY.
ANY_STAFF = require_staff(
    StaffRole.ADMIN, StaffRole.MANAGER, StaffRole.KITCHEN, StaffRole.CASHIER
)
STAFF_ADMIN = require_staff(StaffRole.ADMIN)
# The delivery surface. A driver sees only the orders assigned to them, which
# the endpoints enforce; a manager sees every delivery and may act for a driver
# who is on the road with their hands full.
DELIVERY = require_staff(StaffRole.ADMIN, StaffRole.MANAGER, StaffRole.DRIVER)
# Every role, drivers included. Not a permission so much as the absence of
# one: these are about your own name and your own login, which a cook and a
# driver have exactly as much claim to as an owner.
OWN_ACCOUNT = require_staff()

# IT support. The four lists below are the whole of what the role may do, and
# they are split by what an entry costs rather than by which screen it is on:
# reading the restaurant to work out what is wrong is free, and changing how
# it is configured is recoverable, so support has both. Changing what is sold,
# acting on a live order, reading takings and touching the team are none of
# those, so support has none of them -- the endpoints for those keep MANAGE,
# ANY_STAFF and STAFF_ADMIN untouched, which is what makes this role additive
# rather than a re-cut of everyone else's.
#
# FLOOR_VIEW and MENU_VIEW exist as the read halves of ANY_STAFF and MANAGE.
# The write halves stay on the originals, so the pairing is what keeps the
# role read-only there: adding an endpoint to the wrong one of each pair is
# exactly what tests/test_role_coverage.py refuses.
FLOOR_VIEW = require_staff(
    StaffRole.ADMIN, StaffRole.MANAGER, StaffRole.KITCHEN, StaffRole.CASHIER,
    StaffRole.IT_SUPPORT,
)
MENU_VIEW = require_staff(StaffRole.ADMIN, StaffRole.MANAGER, StaffRole.IT_SUPPORT)
# The storefront's presentation, and the image uploads it needs. An upload on
# its own attaches nothing -- saving a row with the key is what puts a picture
# anywhere -- so this does not become a way into the menu.
STOREFRONT = require_staff(StaffRole.ADMIN, StaffRole.MANAGER, StaffRole.IT_SUPPORT)
# The restaurant's own record and where it delivers. Admin's, and support's
# because a wrong timezone, a mistyped address or a delivery ring that never
# matches is what a restaurant calls support about.
SETTINGS = require_staff(StaffRole.ADMIN, StaffRole.IT_SUPPORT)


# ------------------------------------------------------------ own account ---


class OwnAccountIn(BaseModel):
    """Your own display name. Optional, and clearable: some people would
    rather a ticket showed nothing but their email."""

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, max_length=160)


class ChangeEmailIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    # The session already proves who you are. This proves you are still here,
    # which is a different question on a tablet left signed in behind a
    # counter -- the same reason change-password asks for it.
    current_password: str = Field(min_length=1, max_length=256)


@router.patch("/me", response_model=StaffMeOut)
def update_own_account(
    body: OwnAccountIn,
    user: User = Depends(current_staff_user_ready),
    tenant: TenantContext = Depends(resolve_tenant_staff),
    membership: RestaurantUser = Depends(OWN_ACCOUNT),
):
    """Change your own display name.

    No password asked for: a name is what colleagues see beside an order, not
    a credential, and getting it wrong costs nothing that cannot be typed
    again.
    """
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise errors.validation_error("No changes to save.")

    full_name = (changes["full_name"] or "").strip() or None

    with system_session() as session:
        row = session.get(User, user.id)
        if row is None:
            raise errors.ApiError(401, "UNAUTHENTICATED", "Sign in to continue.")
        row.full_name = full_name
        email = row.email
        must_change = row.must_change_password

    with tenant_session(tenant.restaurant_id) as session:
        restaurant = session.get(Restaurant, tenant.restaurant_id)
        if restaurant is None:
            raise errors.tenant_scope_denied()
        restaurant_name = restaurant.name
        storefront_enabled = restaurant.storefront_customization_enabled

    return StaffMeOut(
        user_id=user.id, email=email, full_name=full_name,
        role_code=membership.role_code, must_change_password=must_change,
        restaurant_name=restaurant_name, membership_status=membership.status,
    )


@router.post(
    "/change-email",
    status_code=204,
    # Also checks the current password; see change-password.
    dependencies=[Depends(per_staff_user("change_email", limit=10, window_seconds=900))],
)
def change_email(
    body: ChangeEmailIn,
    user: User = Depends(current_staff_user_ready),
    _=Depends(OWN_ACCOUNT),
):
    """Change the address you sign in with.

    Refused when another staff login already holds it, and that refusal is
    load-bearing rather than tidiness: sign-in looks an account up by address
    alone and expects exactly one. Two rows sharing an address make both
    accounts unreachable, so without this check typing a colleague's address
    into your own settings would lock them out.

    Customer accounts are a separate population and are not consulted. An
    address that orders lunch here can also work here, exactly as it can at
    invitation time.

    The session survives. You have just proved the password, and changing
    which address it belongs to does not make the person holding it someone
    else.
    """
    email = body.email.strip().lower()
    if "@" not in email or len(email) < 3:
        raise errors.validation_error("Enter an email address you can sign in with.")

    with system_session() as session:
        row = session.get(User, user.id)
        if row is None or row.password_hash is None:
            raise errors.ApiError(401, "UNAUTHENTICATED", "Sign in to continue.")
        if not staff_auth.verify_password(row.password_hash, body.current_password):
            raise errors.ApiError(401, "INVALID_CREDENTIALS", "Current password is incorrect.")

        previous = row.email
        if email == previous:
            return

        taken = session.execute(
            select(User.id).where(
                User.email == email,
                User.kind == UserKind.STAFF.value,
                User.id != user.id,
            ).limit(1)
        ).scalar_one_or_none()
        if taken is not None:
            # Deliberately the same answer whether that login is a colleague
            # here or someone at a restaurant this caller cannot see: which
            # addresses have staff accounts elsewhere is not theirs to learn.
            raise errors.ApiError(
                409, "EMAIL_IN_USE", "That address already has a staff login."
            )

        row.email = email
        restaurant_profile.audit(
            session, user.id, "STAFF_CHANGE_EMAIL",
            {"user_id": str(user.id), "from": email_for_log(previous),
             "to": email_for_log(email)},
        )
    log.info("staff %s changed sign-in address to %s",
             email_for_log(previous), email_for_log(email))


# ------------------------------------------------------ restaurant profile ---
#
# A restaurant editing its own record. The platform can edit the same row from
# the Super Admin portal, and both go through services/restaurant_profile.py
# so the two cannot drift: the tax rules are the same rules whoever is typing.
#
# Admin only. A manager runs the service; changing the trading name, the
# address sales tax is sourced at, or the tax rate itself is a different kind
# of decision, and it is the one the owner is accountable for.


class RestaurantProfileIn(BaseModel):
    """What a restaurant may change about itself.

    Every field is optional and only the ones actually sent are applied, so
    clearing the tagline by sending null stays distinguishable from leaving it
    alone, and two admins editing different fields do not overwrite one
    another.

    Deliberately absent, and still the platform's to change:
      slug      -- the public address, in QR codes on tables and in customers'
                   bookmarks. Moving is a migration, not a text edit.
      status    -- activate and suspend own that transition and its readiness
                   gate, which a plain field write would bypass.
      currency  -- what every existing order and payment is denominated in.
    """

    # Reject unknown fields rather than ignoring them, so sending `slug` is a
    # refusal rather than a silent no-op that reads like a client bug.
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    tagline: str | None = Field(default=None, max_length=200)
    # Shown to customers on the order page and in every order email.
    phone: str | None = Field(default=None, max_length=32)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    _timezone = field_validator("timezone")(known_timezone)
    accepting_orders: bool | None = None
    tax_mode: Literal["FLAT", "STRIPE_TAX"] | None = None
    tax_rate_bps: int | None = Field(default=None, ge=0, le=3000)
    tax_code: str | None = Field(default=None, pattern=r"^txcd_\d{8}$")
    address_line1: str | None = Field(default=None, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    address_city: str | None = Field(default=None, max_length=100)
    address_state: str | None = Field(default=None, max_length=100)
    address_postal_code: str | None = Field(default=None, max_length=20)
    address_country: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    # The brand customers see in the header and on the sign-in pages. Keys
    # come from POST /restaurant/images?kind=branding; null takes one off.
    logo_path: str | None = Field(default=None, max_length=500)
    brand_name_image_path: str | None = Field(default=None, max_length=500)
    brand_name_font: Literal[restaurant_profile.BRAND_NAME_FONTS] | None = None

    @field_validator("brand_name_font")
    @classmethod
    def _font_cannot_be_cleared(cls, value):
        # "default" is the way back to the platform's font; null is not a font.
        if value is None:
            raise ValueError("Choose a font.")
        return value


class RestaurantProfileOut(BaseModel):
    """The profile, plus the few read-only facts the screen needs to explain
    itself -- why Stripe Tax is refused, and what the platform still owns."""

    slug: str
    status: str
    currency: str
    name: str
    tagline: str | None
    phone: str | None
    timezone: str
    accepting_orders: bool
    tax_mode: str
    tax_rate_bps: int
    tax_code: str
    address_line1: str | None
    address_line2: str | None
    address_city: str | None
    address_state: str | None
    address_postal_code: str | None
    address_country: str | None
    logo_path: str | None
    logo_url: str | None
    brand_name_image_path: str | None
    brand_name_image_url: str | None
    brand_name_font: str
    stripe_connected: bool
    charges_enabled: bool


def _profile_out(db: Session, restaurant: Restaurant) -> RestaurantProfileOut:
    account = db.execute(
        text(
            "SELECT stripe_account_id, charges_enabled FROM restaurant_payment_accounts "
            "WHERE restaurant_id = :rid"
        ),
        {"rid": str(restaurant.id)},
    ).mappings().one_or_none()
    return RestaurantProfileOut(
        slug=restaurant.slug, status=restaurant.status, currency=restaurant.currency,
        name=restaurant.name, tagline=restaurant.tagline, phone=restaurant.phone,
        timezone=restaurant.timezone, accepting_orders=restaurant.accepting_orders,
        tax_mode=restaurant.tax_mode, tax_rate_bps=restaurant.tax_rate_bps,
        tax_code=restaurant.tax_code,
        address_line1=restaurant.address_line1, address_line2=restaurant.address_line2,
        address_city=restaurant.address_city, address_state=restaurant.address_state,
        address_postal_code=restaurant.address_postal_code,
        address_country=restaurant.address_country,
        logo_path=restaurant.logo_path, logo_url=images.image_url(restaurant.logo_path),
        brand_name_image_path=restaurant.brand_name_image_path,
        brand_name_image_url=images.image_url(restaurant.brand_name_image_path),
        brand_name_font=restaurant.brand_name_font,
        stripe_connected=bool(account and account["stripe_account_id"]),
        charges_enabled=bool(account and account["charges_enabled"]),
    )


@router.get("/profile", response_model=RestaurantProfileOut)
def read_profile(
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(SETTINGS),
):
    """This restaurant's own record.

    Read through the tenant session, so RLS is what proves this is the
    caller's restaurant rather than any id they could have supplied.
    """
    return _profile_out(db, restaurant)


@router.patch("/profile", response_model=RestaurantProfileOut)
def update_profile(
    body: RestaurantProfileIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(SETTINGS),
):
    """Change it.

    The Stripe Tax gate runs before anything is written and on the *result* of
    the edit, not on the edit: a restaurant clearing its own city is refused
    exactly as one switching Stripe Tax on without an address is. Otherwise
    the first order after a quiet typo is the one that discovers it.
    """
    changes = restaurant_profile.normalize(body.model_dump(exclude_unset=True))
    if not changes:
        raise errors.validation_error("No changes to save.")

    account_id = db.execute(
        text("SELECT stripe_account_id FROM restaurant_payment_accounts WHERE restaurant_id = :rid"),
        {"rid": str(restaurant.id)},
    ).scalar_one_or_none()
    restaurant_profile.guard_stripe_tax(restaurant, changes, account_id)

    # Brand images must be this restaurant's own uploads. The ones they
    # replace are released once nothing else points at them.
    replaced = []
    for field in ("logo_path", "brand_name_image_path"):
        if field in changes:
            changes[field] = images.accept(changes[field], restaurant.id, ImageKind.BRANDING)
            if changes[field] != getattr(restaurant, field):
                replaced.append(getattr(restaurant, field))

    restaurant_profile.apply_changes(restaurant, changes)

    restaurant_profile.audit(
        db, membership.user_id, "RESTAURANT_UPDATE_PROFILE",
        {"restaurant_id": str(restaurant.id), "fields": sorted(changes)},
    )
    db.flush()
    for key in replaced:
        images.release(db, key)
    return _profile_out(db, restaurant)


# ------------------------------------------------------------- delivery ---
#
# Where a restaurant delivers and what it charges. Admin only, alongside the
# rest of the profile: it decides what customers are charged.
#
# Rings are described by their outer edge alone. A restaurant types "3 miles,
# $4" rather than "0 to 3 miles, $4", because the inner edge is never a free
# choice -- a gap between rings would be an address that can be neither
# charged for nor refused. Beyond the last ring is no delivery, not free
# delivery.


MAX_ZONES = 8


class ZoneIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_miles: float = Field(gt=0, le=100)
    fee_minor: int = Field(ge=0, le=100_000)


class ZonesIn(BaseModel):
    """The whole set at once, rather than a row at a time.

    Rings are only meaningful against each other -- they have to be in order,
    with no two sharing an edge -- so validating one in isolation cannot say
    whether the result makes sense. Sending the set makes every save a state
    the restaurant actually chose.
    """

    model_config = ConfigDict(extra="forbid")

    zones: list[ZoneIn] = Field(max_length=MAX_ZONES)


class DeliverySettingsIn(BaseModel):
    """Only what was sent is applied, so switching delivery on and answering
    the tax question are separate decisions that do not overwrite each other."""

    model_config = ConfigDict(extra="forbid")

    delivery_enabled: bool | None = None
    delivery_fee_taxable: bool | None = None


class ZoneOut(BaseModel):
    id: UUID
    max_miles: float
    fee_minor: int


class DeliverySettingsOut(BaseModel):
    delivery_enabled: bool
    # Whether a customer would actually be offered delivery right now. The
    # switch alone does not decide it: an address that has not been placed, or
    # no rings to charge for, means there is nothing to quote.
    delivery_available: bool
    # Why not, in the restaurant's words. Empty when it is available.
    blockers: list[str]
    latitude: float | None
    longitude: float | None
    # The address the coordinates were found from, and the address as it reads
    # now. Different means the restaurant moved and has not been placed again.
    geocoded_address: str | None
    pickup_address: str
    origin_is_current: bool
    # False when no API key is configured, which the screen has to say rather
    # than leaving someone pressing a button that cannot work.
    geocoding_configured: bool
    currency: str
    # Whether the fee is taxed. Only meaningful under a flat rate: a Stripe Tax
    # restaurant hands Stripe the amount and Stripe decides per jurisdiction,
    # so the screen shows that instead of a switch nothing reads.
    delivery_fee_taxable: bool
    tax_mode: str
    zones: list[ZoneOut]


def _zones(db: Session, restaurant_id) -> list[DeliveryZone]:
    return list(
        db.execute(
            select(DeliveryZone).where(DeliveryZone.restaurant_id == restaurant_id)
            .order_by(DeliveryZone.max_miles)
        ).scalars()
    )


def _delivery_out(db: Session, restaurant: Restaurant) -> DeliverySettingsOut:
    zones = _zones(db, restaurant.id)
    origin_current = restaurant.delivery_origin_is_current

    blockers = []
    if not restaurant.pickup_address_line:
        blockers.append("Add the restaurant's address first.")
    elif not origin_current:
        blockers.append("Place the restaurant on the map to measure distances from.")
    if not zones:
        blockers.append("Add at least one delivery ring.")
    if not restaurant.delivery_enabled:
        blockers.append("Delivery is switched off.")

    return DeliverySettingsOut(
        delivery_enabled=restaurant.delivery_enabled,
        delivery_available=not blockers,
        blockers=blockers,
        latitude=restaurant.latitude,
        longitude=restaurant.longitude,
        geocoded_address=restaurant.geocoded_address,
        pickup_address=restaurant.pickup_address_line,
        origin_is_current=origin_current,
        geocoding_configured=geocoding.configured(),
        currency=restaurant.currency,
        delivery_fee_taxable=restaurant.delivery_fee_taxable,
        tax_mode=restaurant.tax_mode,
        zones=[
            ZoneOut(id=z.id, max_miles=z.max_miles, fee_minor=z.fee_minor) for z in zones
        ],
    )


@router.get("/delivery", response_model=DeliverySettingsOut)
def read_delivery(
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(SETTINGS),
):
    """What this restaurant delivers, and why it might not be delivering."""
    return _delivery_out(db, restaurant)


@router.post("/delivery/locate", response_model=DeliverySettingsOut)
def locate_restaurant(
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(SETTINGS),
):
    """Find the restaurant's own coordinates from its pickup address.

    Deliberately an action someone takes rather than something that happens on
    every address save: it costs a paid lookup, and an address halfway through
    being typed is not an address to spend one on.
    """
    address = restaurant.pickup_address_line
    if not address:
        raise errors.validation_error("Add the restaurant's address first.")

    try:
        point = geocoding.geocode(address)
    except geocoding.GeocodingUnavailable:
        # Not str(exc): that text is written for whoever runs the platform --
        # an unenabled API, a key restricted the wrong way, billing switched
        # off -- and none of it is a restaurant's to fix or to understand. The
        # real reason is already in the log, where the operator will look.
        raise errors.ApiError(
            503, "GEOCODING_UNAVAILABLE",
            "Address lookup is not working at the moment. This one is ours to "
            "fix rather than yours -- tell us if it keeps happening. You can "
            "still set up your rings in the meantime.",
        ) from None
    if point is None:
        raise errors.ApiError(
            422, "ADDRESS_NOT_FOUND",
            "We could not find that address on the map. Check it and try again.",
        )

    restaurant.latitude = point.latitude
    restaurant.longitude = point.longitude
    # Stored as it read at the moment it was placed, so a later edit to the
    # address shows up as an origin that needs placing again.
    restaurant.geocoded_address = address
    restaurant_profile.audit(
        db, membership.user_id, "RESTAURANT_LOCATED",
        {"restaurant_id": str(restaurant.id)},
    )
    db.flush()
    return _delivery_out(db, restaurant)


@router.patch("/delivery", response_model=DeliverySettingsOut)
def update_delivery(
    body: DeliverySettingsIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(SETTINGS),
):
    """Switch delivery on or off, and say whether the fee is taxed.

    Switching it on is refused unless there is something to quote with: a
    restaurant that believes it is delivering and is not is worse off than one
    told why it cannot yet.
    """
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise errors.validation_error("No changes to save.")

    if changes.get("delivery_enabled"):
        if not restaurant.delivery_origin_is_current:
            raise errors.ApiError(
                409, "DELIVERY_NOT_READY",
                "Place the restaurant on the map before switching delivery on.",
            )
        if not _zones(db, restaurant.id):
            raise errors.ApiError(
                409, "DELIVERY_NOT_READY",
                "Add at least one delivery ring before switching delivery on.",
            )

    for field, value in changes.items():
        setattr(restaurant, field, value)
    restaurant_profile.audit(
        db, membership.user_id, "RESTAURANT_DELIVERY_SWITCH",
        {"restaurant_id": str(restaurant.id), **{k: v for k, v in changes.items()}},
    )
    db.flush()
    return _delivery_out(db, restaurant)


@router.put("/delivery/zones", response_model=DeliverySettingsOut)
def set_zones(
    body: ZonesIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(SETTINGS),
):
    """Replace the whole set of rings."""
    edges = [round(zone.max_miles, 2) for zone in body.zones]
    if len(set(edges)) != len(edges):
        raise errors.validation_error("Two rings cannot end at the same distance.")
    if not body.zones and restaurant.delivery_enabled:
        raise errors.ApiError(
            409, "DELIVERY_NOT_READY",
            "Switch delivery off before removing every ring.",
        )

    for existing in _zones(db, restaurant.id):
        db.delete(existing)
    # Flushed before the new rows go in, so replacing a set that reuses an
    # edge does not trip the one-ring-per-edge constraint on its way through.
    db.flush()
    for zone, edge in zip(body.zones, edges):
        db.add(DeliveryZone(
            restaurant_id=restaurant.id, max_miles=edge, fee_minor=zone.fee_minor,
        ))

    restaurant_profile.audit(
        db, membership.user_id, "RESTAURANT_DELIVERY_ZONES",
        {"restaurant_id": str(restaurant.id), "rings": len(body.zones)},
    )
    db.flush()
    return _delivery_out(db, restaurant)


class MealIn(BaseModel):
    name: str = Field(max_length=120)
    sort_order: int = 0
    # Optional, and only ever together. See MealUpdateIn for what a pair means.
    starts_at: time | None = None
    ends_at: time | None = None


class MealUpdateIn(BaseModel):
    """A partial update: rename the period, or set the hours it is served.

    Every field is optional and only the ones actually sent are applied, the
    same rule the rest of this builder keeps -- so clearing the hours is
    sending both as null, and leaving them out means "don't touch them".
    """

    name: str | None = Field(default=None, min_length=1, max_length=120)
    starts_at: time | None = None
    ends_at: time | None = None


def _resolve_hours(
    sent: dict, current: tuple[time | None, time | None]
) -> tuple[time | None, time | None]:
    """The hours a meal ends up with, refusing the pairs that mean nothing.

    Applied as a pair even though they arrive as two fields, because the rule
    is about the pair: a period that opens at seven and never closes tells a
    customer less than one that says nothing at all. Sending only one of them
    is therefore read against what is already stored, and refused if that
    leaves a half-open range -- which is also what the database would say, but
    later and in words about a constraint.

    Equal ends are refused here rather than by a check constraint so the
    message can name the two readings it could not choose between. Every
    other pair is allowed, including an end before the start: that is late
    night, and it is the period most likely to want hours at all.
    """
    starts = sent.get("starts_at", current[0])
    ends = sent.get("ends_at", current[1])

    if (starts is None) != (ends is None):
        raise errors.validation_error(
            "A meal period needs both a start and an end time, or neither."
        )
    if starts is not None and starts == ends:
        raise errors.validation_error(
            "The start and end times are the same. Set an end later than the "
            "start, or clear both to leave the hours unsaid."
        )
    return starts, ends


class ItemTypeIn(BaseModel):
    """A new type, in the restaurant's own words.

    parent_id makes it a subcategory: Burgers under Food. Left out, which is
    the ordinary case, it is a heading of its own.
    """

    name: str = Field(min_length=1, max_length=60)
    parent_id: UUID | None = None
    sort_order: int | None = None


class ItemTypeUpdateIn(BaseModel):
    """A partial update: rename a type, move it up, or file it under another.

    parent_id follows the same rule as every other field here -- only what is
    actually sent is applied -- which is what makes `"parent_id": null` mean
    "promote this back to a heading of its own" and leaving it out mean
    "don't touch it".
    """

    name: str | None = Field(default=None, min_length=1, max_length=60)
    parent_id: UUID | None = None
    sort_order: int | None = None


class ItemUpdateIn(BaseModel):
    """A partial update. Every field is optional, and only the ones actually
    sent are applied -- so clearing a description is `"description": null`,
    and leaving it out means "don't touch it".

    meal_ids and modifier_group_ids are whole lists rather than add/remove
    operations: sending one replaces the set. The builder always knows the
    complete set it wants, and a replace cannot leave the item attached to a
    period the manager just unticked because two requests crossed.
    """

    name: str | None = Field(default=None, min_length=1, max_length=180)
    description: str | None = None
    # Null takes the figure off again, so it has to be told apart from the
    # field being left out -- like description and image_path below.
    calories: int | None = Field(default=None, ge=0, le=20000)
    base_price_minor: int | None = Field(default=None, ge=0)
    tax_exempt: bool | None = None
    item_type_id: UUID | None = None
    meal_ids: list[UUID] | None = None
    modifier_group_ids: list[UUID] | None = None
    included_option_ids: list[UUID] | None = None
    # A key returned by POST /images, or null to take the picture off.
    image_path: str | None = Field(default=None, max_length=200)


class ItemIn(BaseModel):
    """A new item, optionally served from the moment it is created.

    meal_ids may be empty. An item that belongs to no period yet is still a
    real item -- it sits in the library until someone puts it on a menu,
    which is how a whole season can be typed up before it goes on sale.
    """

    name: str = Field(max_length=180)
    item_type_id: UUID
    description: str | None = None
    # kcal, as the restaurant states it. Null is "not stated"; a combo adds
    # up what its chosen items state.
    calories: int | None = Field(default=None, ge=0, le=20000)
    base_price_minor: int = Field(ge=0)
    # Left out of the tax on every order it is part of.
    tax_exempt: bool = False
    sort_order: int = 0
    modifier_group_ids: list[UUID] = Field(default_factory=list)
    meal_ids: list[UUID] = Field(default_factory=list)
    # What the item comes with: chosen for the customer, and charged at
    # nothing. Every one has to belong to a group in modifier_group_ids.
    included_option_ids: list[UUID] = Field(default_factory=list)
    # A key returned by POST /images. Uploaded first and attached here, so a
    # picture is chosen while the item is still being described.
    image_path: str | None = Field(default=None, max_length=200)


class MealItemsIn(BaseModel):
    """Items to start serving in a period, pulled from the library."""

    item_ids: list[UUID] = Field(default_factory=list)


class ComboSlotIn(BaseModel):
    """One required choice in a combo, and what may fill it.

    A slot with no items is not sent. The builder creates a slot by ticking
    items into a type, so a type nobody ticked is a type the combo does not
    include -- there is no separate step that makes an empty one.
    """

    item_type_id: UUID
    item_ids: list[UUID] = Field(min_length=1)


class ComboIn(BaseModel):
    """A new combo.

    The discount is a kind and a value read according to it: basis points for
    PERCENT, minor units for AMOUNT, ignored for NONE. Validated in
    _combo_discount below rather than here, because the bound on the value
    depends on the kind and a field cannot see its sibling.
    """

    meal_id: UUID
    name: str = Field(min_length=1, max_length=180)
    description: str | None = None
    discount_kind: DiscountKind = DiscountKind.NONE
    discount_value: int = Field(default=0, ge=0)
    sort_order: int = 0
    # A key returned by POST /images?kind=items: the deal's own photograph.
    image_path: str | None = Field(default=None, max_length=500)
    slots: list[ComboSlotIn] = Field(default_factory=list)


class ComboUpdateIn(BaseModel):
    """A partial update. Sending `slots` replaces every slot and choice.

    Replaced rather than merged for the same reason an item's meal periods
    are: the builder always knows the whole combo it means, and a merge
    cannot express taking the last drink out of a slot.
    """

    name: str | None = Field(default=None, min_length=1, max_length=180)
    description: str | None = None
    discount_kind: DiscountKind | None = None
    discount_value: int | None = Field(default=None, ge=0)
    is_available: bool | None = None
    image_path: str | None = Field(default=None, max_length=500)
    slots: list[ComboSlotIn] | None = None


class OptionIn(BaseModel):
    """One choice in a group.

    Nothing here says "comes as standard". That is a property of the item
    that offers the group, not of the option, because a burger and a salad
    built from the same Veggies group come with different things.
    """

    name: str = Field(max_length=180)
    price_delta_minor: int = 0
    # What this choice adds to the item's calories. Negative takes them off;
    # null states no change, which is counted as none.
    calories_delta: int | None = Field(default=None, ge=-20000, le=20000)
    sort_order: int = 0
    # A key returned by POST /images?kind=options.
    image_path: str | None = Field(default=None, max_length=200)


class ModifierGroupUpdateIn(BaseModel):
    """A partial update, like ItemUpdateIn: only the fields sent are applied.

    applies_to_type_ids is a whole list when sent, and an empty one is a real
    value meaning "offer this everywhere" -- which is why it has to be told
    apart from the field being absent, and why this cannot read the attribute
    directly."""

    name: str | None = Field(default=None, min_length=1, max_length=180)
    applies_to_type_ids: list[UUID] | None = None
    # The rules. Any of them may be sent alone; the group they add up to, with
    # what is stored for the rest, is checked as a whole.
    selection_type: SelectionType | None = None
    is_required: bool | None = None
    min_select: int | None = Field(default=None, ge=0)
    max_select: int | None = Field(default=None, ge=1)


RULE_FIELDS = ("selection_type", "is_required", "min_select", "max_select")


class ModifierOptionUpdateIn(BaseModel):
    """A partial update, like ItemUpdateIn. price_delta_minor carries no lower
    bound: "no cheese -0.50" is a legitimate decrement, and it is the one
    documented exception to the non-negative money rule."""

    name: str | None = Field(default=None, min_length=1, max_length=180)
    price_delta_minor: int | None = None
    # Null here really is "no change stated", so it is applied rather than
    # refused -- unlike price_delta_minor, which every option must have.
    calories_delta: int | None = Field(default=None, ge=-20000, le=20000)
    # A key returned by POST /images?kind=options, or null to take it off.
    image_path: str | None = Field(default=None, max_length=200)


class ModifierGroupIn(BaseModel):
    name: str = Field(max_length=180)
    selection_type: SelectionType = SelectionType.MULTI
    is_required: bool = False
    min_select: int = Field(default=0, ge=0)
    max_select: int = Field(default=1, ge=1)
    # Which item kinds the builder offers this group for. Empty means all of
    # them. A list rather than one type because a Size group belongs on
    # drinks and sides alike, and duplicating it per type would mean two
    # libraries to keep in step.
    applies_to_type_ids: list[UUID] = Field(default_factory=list)
    options: list[OptionIn] = Field(default_factory=list)


@router.post(
    "/images",
    status_code=201,
    # Generous for a manager photographing a whole menu in one sitting, and
    # still a wall against a script filling the disk. Counted per person, so
    # one busy manager does not lock out another.
    dependencies=[Depends(per_staff_user("image_upload", limit=60, window_seconds=600))],
)
def upload_image(
    kind: ImageKind,
    file: UploadFile,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    _=Depends(STOREFRONT),
):
    """Store a photo for an item or a modifier option, and say where it is.

    Upload first, attach second. This returns a key and nothing points at it
    yet; saving an item or option with that key is what puts the picture on
    the menu. So choosing a photo on a form that is then cancelled changes
    nothing a customer sees, which is the same promise every other edit in
    this builder keeps.

    The photo is decoded, re-encoded as WebP and stripped of metadata before
    it is written, and the key is generated rather than taken from the file's
    name. See services/images for why each of those matters.

    `kind` is a query parameter rather than a form field, so a malformed
    request is refused before the file is read.
    """
    # One byte over the limit is enough to know it is over, without reading
    # the rest of an arbitrarily large body into memory.
    data = file.file.read(images.MAX_UPLOAD_BYTES + 1)
    # Banners and category tiles only exist on a customized storefront. The
    # logo and name lettering do not: they are set in Settings and shown on
    # every restaurant's header, so they upload whatever the switch says.
    if kind in (ImageKind.BANNERS, ImageKind.CATEGORIES):
        storefront.require_enabled(restaurant)
    picture = images.process(data, kind)

    key = images.new_key(restaurant.id, kind)
    images.storage().save(key, picture)
    log.info("stored %s image %s (%d bytes)", kind.value, key, len(picture))
    return {"image_path": key, "image_url": images.image_url(key)}


@router.get("/menu", response_model=MenuOut)
def staff_menu(
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MENU_VIEW),
):
    """The menu as the builder needs to see it.

    Deliberately not the public /menu. That one resolves only an ACTIVE
    restaurant, so a draft could never be set up, and it drops meal periods
    that serve nothing -- which is every period the moment it is created.
    Reading it here made each addition look as though it had not been saved.
    """
    return load_menu(db, include_empty=True)


@router.post("/meals", status_code=201)
def create_meal(
    body: MealIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    name = _required_name(body.name, "A meal period needs a name.")
    starts, ends = _resolve_hours(
        body.model_dump(exclude_unset=True), (None, None)
    )
    meal = Meal(
        restaurant_id=restaurant.id,
        name=name,
        sort_order=body.sort_order,
        starts_at=starts,
        ends_at=ends,
    )
    db.add(meal)
    db.flush()
    return _meal_out(meal)


@router.patch("/meals/{meal_id}")
def update_meal(
    meal_id: UUID,
    body: MealUpdateIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    """Correct a meal's name, or say the hours it is served.

    Both are safe in a way that deleting is not: nothing outside the menu
    reads either. Orders snapshot the item name they charged for, no order
    records the meal at all, and nothing anywhere is gated on the hours.
    """
    meal = db.get(Meal, meal_id)
    if meal is None or meal.deleted_at is not None:
        raise errors.validation_error("No such meal.")

    sent = body.model_dump(exclude_unset=True)

    if "name" in sent:
        # Trimmed here rather than in the schema, so a name of nothing but
        # spaces is refused instead of stored as an empty heading.
        name = (sent["name"] or "").strip()
        if not name:
            raise errors.validation_error("A meal needs a name.")
        meal.name = name

    meal.starts_at, meal.ends_at = _resolve_hours(
        sent, (meal.starts_at, meal.ends_at)
    )
    return _meal_out(meal)


def _meal_out(meal: Meal) -> dict:
    """What both write endpoints answer with.

    Times go out as HH:MM. The seconds a `time` carries are always zero here
    -- nothing sets them -- and sending them would have the builder echo back
    something nobody typed.
    """
    return {
        "id": str(meal.id),
        "name": meal.name,
        "starts_at": None if meal.starts_at is None else meal.starts_at.strftime("%H:%M"),
        "ends_at": None if meal.ends_at is None else meal.ends_at.strftime("%H:%M"),
    }


@router.delete("/meals/{meal_id}")
def delete_meal(
    meal_id: UUID,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    """Remove a meal period. The items it served are kept.

    This is the change that came with items owning themselves. A period used
    to own everything filed under it, so deleting Breakfast deleted the
    coffee -- including the coffee that Lunch was also selling. Now the
    period is a list of what it serves, and dropping the list drops only the
    listing.

    Soft delete on the meal itself, not DELETE, so a period can be restored
    by hand if it goes in error. The links are deleted outright: nothing
    outside the menu reads them.
    """
    meal = db.get(Meal, meal_id)
    if meal is None or meal.deleted_at is not None:
        raise errors.validation_error("No such meal period.")

    for link in db.execute(
        select(MealItem).where(MealItem.meal_id == meal.id)
    ).scalars().all():
        db.delete(link)

    meal.deleted_at = utcnow()
    return {"id": str(meal.id), "deleted": True}


@router.post("/meals/{meal_id}/items", status_code=201)
def add_meal_items(
    meal_id: UUID,
    body: MealItemsIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    """Start serving existing items during this period.

    Adding something already on the list is not an error. The builder sends
    what the manager ticked, and a double-click or a stale screen should not
    read as a failure when the end state is exactly what was asked for.
    """
    meal = db.get(Meal, meal_id)
    if meal is None or meal.deleted_at is not None:
        raise errors.validation_error("No such meal period.")

    served = {
        link.item_id
        for link in db.execute(
            select(MealItem).where(MealItem.meal_id == meal.id)
        ).scalars().all()
    }

    added = 0
    for item_id in dict.fromkeys(body.item_ids):
        item = db.get(Item, item_id)
        if item is None or item.deleted_at is not None:
            raise errors.validation_error("No such item.")
        if item_id in served:
            continue
        db.add(
            MealItem(
                restaurant_id=restaurant.id, meal_id=meal.id, item_id=item_id,
            )
        )
        added += 1

    db.flush()
    return {"meal_id": str(meal.id), "added": added}


@router.delete("/meals/{meal_id}/items/{item_id}")
def remove_meal_item(
    meal_id: UUID,
    item_id: UUID,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    """Stop serving one item during this period.

    The item itself is untouched, along with every other period serving it.
    Taking coffee off Breakfast is a menu decision; deleting the coffee is a
    different one, and it lives on the item.
    """
    link = db.execute(
        select(MealItem).where(MealItem.meal_id == meal_id, MealItem.item_id == item_id)
    ).scalars().first()
    if link is None:
        raise errors.validation_error("That item is not on this meal period.")

    db.delete(link)
    return {"meal_id": str(meal_id), "item_id": str(item_id), "removed": True}


def _required_name(value: str | None, message: str) -> str:
    """A name with its surrounding spaces trimmed, refused if nothing is left.

    The update endpoints always did this; the create endpoints stored " " as a
    name, which reads on a menu as an item or heading with no name at all.
    """
    name = (value or "").strip()
    if not name:
        raise errors.validation_error(message)
    return name


def _check_group_rules(
    name: str,
    selection_type: SelectionType | str,
    is_required: bool,
    min_select: int,
    max_select: int,
    *,
    option_count: int,
) -> None:
    """Refuse a modifier group no customer could complete, or one that says
    something checkout does not do.

    These mirror services/pricing._validate_modifiers, which is what a customer
    actually meets: a pick-one group takes exactly one choice, a minimum only
    binds a required group, and every choice is one tick of one option.
    """
    kind = selection_type.value if isinstance(selection_type, SelectionType) else selection_type
    if option_count < 1:
        raise errors.validation_error(
            f"{name} needs at least one option. A group with none has nothing to offer."
        )
    if kind == SelectionType.SINGLE.value and (max_select != 1 or min_select > 1):
        raise errors.validation_error(
            f"{name} is pick-one, so a customer chooses exactly one: its maximum is 1."
        )
    if max_select < min_select:
        raise errors.validation_error(
            f"{name} has a maximum of {max_select}, below its minimum of {min_select}."
        )
    if is_required and min_select < 1:
        raise errors.validation_error(
            f"{name} is required, so a customer has to choose at least one."
        )
    if not is_required and min_select > 0:
        # Checkout only holds a minimum against a required group, so an
        # optional one with a minimum would promise a rule nobody enforces.
        raise errors.validation_error(
            f"{name} is optional, so it cannot have a minimum. Make it required instead."
        )
    if min_select > option_count:
        raise errors.validation_error(
            f"{name} asks customers to choose {min_select} but has only "
            f"{option_count} {'option' if option_count == 1 else 'options'}."
        )


def _set_group_types(
    db: Session, restaurant: Restaurant, group: ModifierGroup, type_ids
) -> None:
    """Make the group offered for exactly these item types.

    Deduplicated, because the same type twice means nothing to the filter and
    would read back as a repeated chip in the builder. An empty list is a real
    value: no rows means the group is offered for every type.

    Top-level types only. A group named against Burgers would have to be
    named again against Nuggets, and again against every subcategory added
    afterwards -- exactly the duplication subcategories exist to avoid. An
    item is matched by its root, so a burger is offered whatever Food is.
    """
    wanted = list(dict.fromkeys(type_ids))
    for type_id in wanted:
        item_type = _live_type(db, type_id)
        if item_type.parent_id is not None:
            raise errors.validation_error(
                f"{item_type.name} is a subcategory. Offer the group for the "
                "heading above it and every item inside it gets it."
            )

    existing = {
        link.item_type_id: link
        for link in db.execute(
            select(ModifierGroupItemType).where(
                ModifierGroupItemType.group_id == group.id
            )
        ).scalars().all()
    }

    for type_id, link in existing.items():
        if type_id not in wanted:
            db.delete(link)

    for type_id in wanted:
        if type_id not in existing:
            db.add(
                ModifierGroupItemType(
                    restaurant_id=restaurant.id, group_id=group.id, item_type_id=type_id
                )
            )
    db.flush()


# ---------------------------------------------------------------- combos ---
#
# A combo is one item from each of several kinds, sold together for less. It
# belongs to one meal period and may only offer items that period serves --
# checked here rather than trusted, because a combo built from another
# period's menu would offer food that is not on sale when it is.


def _combo_discount(kind: DiscountKind, value: int) -> tuple[str, int]:
    """Check a discount against the kind that decides how to read it."""
    if kind == DiscountKind.PERCENT:
        # 10000 basis points is 100%. Past that a restaurant pays customers.
        if not 0 <= value <= 10000:
            raise errors.validation_error(
                "A percentage discount has to be between 0 and 100."
            )
    elif kind == DiscountKind.AMOUNT:
        if value < 0:
            raise errors.validation_error("A discount cannot be negative.")
    else:
        # NONE carries no value. Storing whatever was typed before the kind
        # was changed would resurrect it if the kind changed back.
        value = 0
    return kind.value, value


def _set_combo_slots(
    db: Session, restaurant: Restaurant, combo: Combo, slots: list[ComboSlotIn]
) -> None:
    """Rebuild a combo's slots and their choices.

    Every item is checked twice over: that it exists, and that the period
    this combo belongs to actually serves it. The second is the one that
    matters -- an item can be taken off breakfast while a breakfast combo
    still lists it, and the combo would then offer something the period does
    not have.

    The type is checked too. A drink in the food slot would sort under the
    wrong heading and, worse, let a combo demand two drinks and no food while
    still reading as a meal.
    """
    if not slots:
        raise errors.validation_error(
            "A combo needs at least one choice. Tick the items it includes."
        )

    served = {
        link.item_id
        for link in db.execute(
            select(MealItem).where(MealItem.meal_id == combo.meal_id)
        ).scalars().all()
    }

    for existing in list(combo.slots):
        combo.slots.remove(existing)
    db.flush()

    seen_types: set = set()
    for order, slot_in in enumerate(slots):
        item_type = _live_type(db, slot_in.item_type_id)
        # A slot asks for a food, and burgers and nuggets are both foods.
        # Letting it ask for Burgers would turn one meal deal into several,
        # each offering a narrower choice than the deal it replaced, the
        # moment a restaurant subdivided its menu.
        if item_type.parent_id is not None:
            raise errors.validation_error(
                f"{item_type.name} is a subcategory. A combo asks for a "
                "top-level type, and every item inside it can fill the slot."
            )
        if item_type.id in seen_types:
            raise errors.validation_error(
                "A combo can only ask for one of each type."
            )
        seen_types.add(item_type.id)

        slot = ComboSlot(
            restaurant_id=restaurant.id, combo_id=combo.id,
            item_type_id=item_type.id, sort_order=order,
        )
        db.add(slot)
        db.flush()

        for index, item_id in enumerate(dict.fromkeys(slot_in.item_ids)):
            item = db.get(Item, item_id)
            if item is None or item.deleted_at is not None:
                raise errors.validation_error("No such item.")
            if item.id not in served:
                raise errors.validation_error(
                    f"{item.name} is not on this meal period, so it cannot be "
                    "part of a combo on it."
                )
            # By root, so a burger filed under Food > Burgers still counts
            # as a food. This is the whole reason subcategories are a
            # display idea rather than a structural one.
            if _root_type_id(db, item.item_type_id) != item_type.id:
                raise errors.validation_error(
                    f"{item.name} is not filed under {item_type.name}."
                )
            db.add(
                ComboSlotItem(
                    restaurant_id=restaurant.id, slot_id=slot.id,
                    item_id=item.id, sort_order=index,
                )
            )
    db.flush()


def _combo_out(combo: Combo) -> dict:
    """A combo as the builder edits it: ids to bind to, not a priced menu."""
    return {
        "id": str(combo.id),
        "meal_id": str(combo.meal_id),
        "name": combo.name,
        "description": combo.description,
        "discount_kind": combo.discount_kind,
        "discount_value": combo.discount_value,
        "is_available": combo.is_available,
        "image_path": combo.image_path,
        "image_url": images.image_url(combo.image_path),
        "slots": [
            {
                "id": str(slot.id),
                "item_type_id": str(slot.item_type_id),
                "item_ids": [str(choice.item_id) for choice in slot.choices],
            }
            for slot in combo.slots
        ],
    }


@router.get("/combos")
def list_combos(
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MENU_VIEW),
):
    """Every combo, whichever period it belongs to."""
    combos = db.execute(
        select(Combo)
        .where(Combo.deleted_at.is_(None))
        .order_by(Combo.sort_order, Combo.created_at, Combo.id)
        .options(selectinload(Combo.slots).selectinload(ComboSlot.choices))
    ).scalars().all()
    return [_combo_out(combo) for combo in combos]


@router.post("/combos", status_code=201)
def create_combo(
    body: ComboIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    meal = db.get(Meal, body.meal_id)
    if meal is None or meal.deleted_at is not None:
        raise errors.validation_error("That meal period does not exist.")

    kind, value = _combo_discount(body.discount_kind, body.discount_value)
    name = body.name.strip()
    if not name:
        raise errors.validation_error("A combo needs a name.")

    combo = Combo(
        restaurant_id=restaurant.id, meal_id=meal.id, name=name,
        description=(body.description or "").strip() or None,
        discount_kind=kind, discount_value=value, sort_order=body.sort_order,
        image_path=images.accept(body.image_path, restaurant.id, ImageKind.ITEMS),
    )
    db.add(combo)
    db.flush()

    _set_combo_slots(db, restaurant, combo, body.slots)
    return {"id": str(combo.id), "name": combo.name}


@router.patch("/combos/{combo_id}")
def update_combo(
    combo_id: UUID,
    body: ComboUpdateIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    """Edit a combo.

    The meal period is not editable. Every choice in every slot is an item
    that period serves, so moving the combo would invalidate all of them at
    once -- that is a new combo, and building it as one is clearer than a
    rule about which choices survive.
    """
    combo = db.get(Combo, combo_id)
    if combo is None or combo.deleted_at is not None:
        raise errors.validation_error("No such combo.")

    sent = body.model_dump(exclude_unset=True)

    if "name" in sent:
        name = (sent["name"] or "").strip()
        if not name:
            raise errors.validation_error("A combo needs a name.")
        combo.name = name

    if "description" in sent:
        description = (sent["description"] or "").strip()
        combo.description = description or None

    if sent.get("is_available") is not None:
        combo.is_available = sent["is_available"]

    # Read together: the value means nothing without the kind, so changing
    # one without the other has to fall back on what is stored.
    if sent.get("discount_kind") is not None or sent.get("discount_value") is not None:
        kind = DiscountKind(sent.get("discount_kind") or combo.discount_kind)
        value = sent.get("discount_value")
        if value is None:
            value = combo.discount_value
        combo.discount_kind, combo.discount_value = _combo_discount(kind, value)

    # Null is how the photograph is taken off, so the field being absent and
    # the field being null have to stay different. The file it replaces goes
    # after the commit, and only if nothing else still shows it.
    if "image_path" in sent:
        previous = combo.image_path
        combo.image_path = images.accept(sent["image_path"], restaurant.id, ImageKind.ITEMS)
        if previous != combo.image_path:
            images.release(db, previous)

    if sent.get("slots") is not None:
        _set_combo_slots(
            db, restaurant, combo, [ComboSlotIn(**slot) for slot in sent["slots"]]
        )

    db.flush()
    return _combo_out(combo)


@router.delete("/combos/{combo_id}")
def delete_combo(
    combo_id: UUID,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    """Take a combo off the menu. The items it offered are untouched.

    Soft delete: order_items points at combos so a past receipt can still say
    which deal it was, and removing the row would either fail on that key or
    rewrite history. The slots stay with it -- they are meaningless without
    the combo and nothing reads them once it is hidden.
    """
    combo = db.get(Combo, combo_id)
    if combo is None or combo.deleted_at is not None:
        raise errors.validation_error("No such combo.")

    combo.deleted_at = utcnow()
    return {"id": str(combo.id), "deleted": True}


@router.post("/modifier-groups", status_code=201)
def create_modifier_group(
    body: ModifierGroupIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    """Reusable across items. Define "Ice level" once, attach it to every
    beverage.

    The builder checks all of this before it sends anything, but the API is
    what stores the group, and a group whose rules no customer can meet makes
    every item offering it impossible to order.
    """
    name = _required_name(body.name, "A modifier group needs a name.")
    option_names = [
        _required_name(option.name, "Every option needs a name.") for option in body.options
    ]
    _check_group_rules(
        name, body.selection_type, body.is_required, body.min_select, body.max_select,
        option_count=len(option_names),
    )

    group = ModifierGroup(
        restaurant_id=restaurant.id, name=name,
        selection_type=body.selection_type.value, is_required=body.is_required,
        min_select=body.min_select, max_select=body.max_select,
    )
    db.add(group)
    db.flush()
    _set_group_types(db, restaurant, group, body.applies_to_type_ids)

    for option, option_name in zip(body.options, option_names):
        db.add(
            ModifierOption(
                restaurant_id=restaurant.id, group_id=group.id, name=option_name,
                price_delta_minor=option.price_delta_minor,
                calories_delta=option.calories_delta,
                sort_order=option.sort_order,
                image_path=images.accept(option.image_path, restaurant.id, ImageKind.OPTIONS),
            )
        )
    db.flush()
    return {"id": str(group.id), "name": group.name}


@router.get("/modifier-groups")
def list_modifier_groups(
    item_type_id: UUID | None = None,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MENU_VIEW),
):
    """The reusable library. Filtered by item type so adding a drink surfaces
    Ice level rather than Veggies.

    A group with no types is offered for every type, so it has to pass this
    filter too -- no rows is the "everything" case, not an omission.

    A subcategory is answered with its parent's groups. The builder asks with
    whatever type the item actually carries, which for a burger is Burgers;
    groups are named against Food. Resolving here rather than asking the
    caller to is what keeps "which groups apply" one question with one
    answer.
    """
    query = select(ModifierGroup).where(ModifierGroup.deleted_at.is_(None))
    if item_type_id:
        wanted = (
            select(ModifierGroupItemType.group_id)
            .where(ModifierGroupItemType.item_type_id == _root_type_id(db, item_type_id))
        )
        unrestricted = ~select(ModifierGroupItemType.id).where(
            ModifierGroupItemType.group_id == ModifierGroup.id
        ).exists()
        query = query.where(ModifierGroup.id.in_(wanted) | unrestricted)

    groups = db.execute(
        query.options(
            selectinload(ModifierGroup.options),
            selectinload(ModifierGroup.type_links),
        )
    ).scalars().all()
    return [
        {
            "id": str(g.id), "name": g.name, "selection_type": g.selection_type,
            "is_required": g.is_required, "min_select": g.min_select,
            "max_select": g.max_select,
            "applies_to_type_ids": [str(link.item_type_id) for link in g.type_links],
            "options": [
                {
                    "id": str(o.id), "name": o.name,
                    "price_delta_minor": o.price_delta_minor,
                    "calories_delta": o.calories_delta,
                    # The key is what an edit sends back unchanged; the URL is
                    # what the thumbnail shows.
                    "image_path": o.image_path,
                    "image_url": images.image_url(o.image_path),
                }
                for o in g.options if o.deleted_at is None
            ],
        }
        for g in groups
    ]


@router.patch("/modifier-groups/{group_id}")
def update_modifier_group(
    group_id: UUID,
    body: ModifierGroupUpdateIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    """Correct a group's name, which item kinds it is offered for, or its rules.

    All of it reaches every item that opted into the group, which is the point
    of a library. Orders already placed keep the group name they were shown,
    because order_item_modifiers snapshots it, and were priced under the rules
    of the moment.

    The rules -- pick one or several, required or not, how many -- used to be
    fixed at creation, so correcting "up to 2" to "up to 3" meant deleting the
    group and attaching a new one to every item. They are checked the way a
    new group is, against the options the group has now, and refused where an
    item already comes with more of the group's options than the new maximum
    allows: its default choice would be one checkout refuses.

    Narrowing the types does not detach the group from items that already
    carry it. The types are a filter on what the builder offers, not a rule
    about what an item may hold: an existing choice was made deliberately,
    and dropping it silently on an unrelated edit would lose that work.
    """
    group = db.get(ModifierGroup, group_id)
    if group is None or group.deleted_at is not None:
        raise errors.validation_error("No such modifier group.")

    sent = body.model_dump(exclude_unset=True)

    if "name" in sent:
        name = (sent["name"] or "").strip()
        if not name:
            raise errors.validation_error("A modifier group needs a name.")
        group.name = name

    if sent.get("applies_to_type_ids") is not None:
        _set_group_types(db, restaurant, group, sent["applies_to_type_ids"])

    if any(sent.get(field) is not None for field in RULE_FIELDS):
        _change_group_rules(db, group, sent)

    return {
        "id": str(group.id),
        "name": group.name,
        "applies_to_type_ids": [str(link.item_type_id) for link in group.type_links],
        "selection_type": group.selection_type,
        "is_required": group.is_required,
        "min_select": group.min_select,
        "max_select": group.max_select,
    }


def _change_group_rules(db: Session, group: ModifierGroup, sent: dict) -> None:
    """Apply new rules to a group, if the group they make is one a customer can
    still complete on every item that offers it."""
    def pick(field):
        return sent[field] if sent.get(field) is not None else getattr(group, field)

    selection_type = pick("selection_type")
    selection_type = getattr(selection_type, "value", selection_type)
    is_required, min_select, max_select = (
        pick("is_required"), pick("min_select"), pick("max_select")
    )

    live_options = db.execute(
        select(ModifierOption).where(
            ModifierOption.group_id == group.id, ModifierOption.deleted_at.is_(None)
        )
    ).scalars().all()
    _check_group_rules(
        group.name, selection_type, is_required, min_select, max_select,
        option_count=len(live_options),
    )

    # An included option counts towards the maximum at checkout, because the
    # customer is choosing it. So an item that comes with three toppings from
    # a group now allowing two would open with a choice checkout refuses.
    over = db.execute(
        select(Item.name, func.count(ItemIncludedOption.option_id))
        .join(Item, Item.id == ItemIncludedOption.item_id)
        .join(ModifierOption, ModifierOption.id == ItemIncludedOption.option_id)
        .where(
            ModifierOption.group_id == group.id,
            ModifierOption.deleted_at.is_(None),
            Item.deleted_at.is_(None),
        )
        .group_by(Item.id, Item.name)
        .having(func.count(ItemIncludedOption.option_id) > max_select)
        .order_by(Item.name)
    ).all()
    if over:
        name, count = over[0]
        others = f" and {len(over) - 1} more" if len(over) > 1 else ""
        raise errors.validation_error(
            f"{name}{others} comes with {count} options from {group.name}, more than "
            f"a maximum of {max_select} allows. Change what it comes with first."
        )

    group.selection_type = selection_type
    group.is_required = is_required
    group.min_select = min_select
    group.max_select = max_select


@router.delete("/modifier-groups/{group_id}")
def delete_modifier_group(
    group_id: UUID,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    """Remove a group and its options.

    The item_modifier_groups links are left alone. They point at a row that
    still exists, and both the menu reader and pricing skip a deleted group,
    so the effect is that every item quietly stops offering it. Deleting the
    links as well would destroy which items had opted in, for no gain.
    """
    group = db.get(ModifierGroup, group_id)
    if group is None or group.deleted_at is not None:
        raise errors.validation_error("No such modifier group.")

    now = utcnow()
    options = db.execute(
        select(ModifierOption).where(
            ModifierOption.group_id == group.id, ModifierOption.deleted_at.is_(None)
        )
    ).scalars().all()
    for option in options:
        option.deleted_at = now
    group.deleted_at = now
    return {"id": str(group.id), "deleted": True}


@router.post("/modifier-groups/{group_id}/options", status_code=201)
def create_modifier_option(
    group_id: UUID,
    body: OptionIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    """Add one option to an existing group.

    sort_order is assigned here rather than taken from the caller, so a new
    option lands at the end of the list the operator is looking at instead of
    tying with everything else on zero.
    """
    group = db.get(ModifierGroup, group_id)
    if group is None or group.deleted_at is not None:
        raise errors.validation_error("No such modifier group.")

    name = body.name.strip()
    if not name:
        raise errors.validation_error("An option needs a name.")

    highest = db.execute(
        select(func.max(ModifierOption.sort_order)).where(
            ModifierOption.group_id == group.id, ModifierOption.deleted_at.is_(None)
        )
    ).scalar()

    option = ModifierOption(
        restaurant_id=restaurant.id, group_id=group.id, name=name,
        price_delta_minor=body.price_delta_minor,
        calories_delta=body.calories_delta,
        sort_order=(highest + 1) if highest is not None else 0,
        image_path=images.accept(body.image_path, restaurant.id, ImageKind.OPTIONS),
    )
    db.add(option)
    db.flush()
    return {"id": str(option.id), "name": option.name}


@router.patch("/modifier-options/{option_id}")
def update_modifier_option(
    option_id: UUID,
    body: ModifierOptionUpdateIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    """Edit an option's name, its price change, or its picture."""
    option = db.get(ModifierOption, option_id)
    if option is None or option.deleted_at is not None:
        raise errors.validation_error("No such option.")

    # Only what the caller actually sent, so editing a price cannot blank a
    # name that was simply left out of the request.
    sent = body.model_dump(exclude_unset=True)

    if "name" in sent:
        name = (sent["name"] or "").strip()
        if not name:
            raise errors.validation_error("An option needs a name.")
        option.name = name

    if "price_delta_minor" in sent:
        if sent["price_delta_minor"] is None:
            raise errors.validation_error("A price change cannot be blank. Use 0 for none.")
        option.price_delta_minor = sent["price_delta_minor"]

    # Null is a real value here: it takes a stated change back off.
    if "calories_delta" in sent:
        option.calories_delta = sent["calories_delta"]

    if "image_path" in sent:
        previous = option.image_path
        option.image_path = images.accept(sent["image_path"], restaurant.id, ImageKind.OPTIONS)
        if previous != option.image_path:
            images.release(db, previous)

    return {
        "id": str(option.id),
        "name": option.name,
        "price_delta_minor": option.price_delta_minor,
        "calories_delta": option.calories_delta,
    }


@router.delete("/modifier-options/{option_id}")
def delete_modifier_option(
    option_id: UUID,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    """Take one option off a group.

    Soft, like everything else on the menu: order_item_modifiers may reference
    it, and those lines have to keep resolving.
    """
    option = db.get(ModifierOption, option_id)
    if option is None or option.deleted_at is not None:
        raise errors.validation_error("No such option.")

    # What the group is left with has to still be a group a customer can
    # complete: a required "choose 2" with one option left, or a group with
    # none at all, would make every item offering it unorderable.
    group = db.get(ModifierGroup, option.group_id)
    if group is not None and group.deleted_at is None:
        remaining = sum(
            1
            for sibling in db.execute(
                select(ModifierOption).where(
                    ModifierOption.group_id == group.id,
                    ModifierOption.deleted_at.is_(None),
                )
            ).scalars().all()
            if sibling.id != option.id
        )
        if remaining == 0:
            raise errors.validation_error(
                f"{option.name} is the last option in {group.name}. Delete the group "
                "instead, or add another option first."
            )
        if group.is_required and remaining < group.min_select:
            raise errors.validation_error(
                f"{group.name} asks customers to choose {group.min_select}, so it needs "
                f"at least {group.min_select} options. Add another before deleting this one."
            )

    option.deleted_at = utcnow()
    return {"id": str(option.id), "deleted": True}


# ----------------------------------------------------------- item types ---
#
# What sort of thing an item is, in the restaurant's own words. Four fixed
# words used to live in the schema; a tiffin house had to file tiffins,
# thalis and chaat under "Food" and read someone else's vocabulary back on
# its own menu.


def _live_type(db: Session, type_id: UUID) -> ItemType:
    item_type = db.get(ItemType, type_id)
    if item_type is None or item_type.deleted_at is not None:
        raise errors.validation_error("No such item type.")
    return item_type


def _root_type_id(db: Session, type_id: UUID) -> UUID:
    """The top-level type an item of this type belongs to.

    Itself, unless it is a subcategory, in which case its parent. Everything
    structural -- combo slots, the modifier-group filter -- goes through here
    so that subdividing a menu stays a change to how it reads and not to how
    it works. Depth is capped at two, so this is one hop and never a loop.
    """
    item_type = db.get(ItemType, type_id)
    if item_type is None:
        return type_id
    return item_type.parent_id or item_type.id


def _live_parent(db: Session, parent_id: UUID, *, child: ItemType | None = None) -> ItemType:
    """The type a subcategory is being filed under, if it may hold one.

    Two levels and no more, so the parent has to be top-level itself. The
    database says the same thing -- see the composite key in migration 0009
    -- but a constraint violation reaches the builder as a 500, and this is a
    sentence the person naming the subcategory can act on.
    """
    parent = _live_type(db, parent_id)
    if child is not None and parent.id == child.id:
        raise errors.validation_error("A type cannot be filed under itself.")
    if parent.parent_id is not None:
        raise errors.validation_error(
            f"{parent.name} is already a subcategory. Menus go two levels "
            "deep: a heading, and the groups inside it."
        )
    return parent


def _children_of(db: Session, type_id: UUID) -> list[ItemType]:
    """The live subcategories filed under a type, in their own order."""
    return list(
        db.execute(
            select(ItemType)
            .where(ItemType.parent_id == type_id, ItemType.deleted_at.is_(None))
            .order_by(ItemType.sort_order, ItemType.created_at, ItemType.id)
        ).scalars().all()
    )


def _type_name_taken(db: Session, name: str, *, excluding: UUID | None = None) -> bool:
    """Case-insensitively, among live types.

    "Drinks" and "drinks" are the same heading to a customer. The database
    holds the same rule as a unique index; this exists so the answer is a
    sentence about a duplicate name rather than a constraint violation.
    """
    query = select(ItemType).where(
        func.lower(ItemType.name) == name.lower(), ItemType.deleted_at.is_(None)
    )
    if excluding is not None:
        query = query.where(ItemType.id != excluding)
    return db.execute(query).scalars().first() is not None


@router.get("/item-types")
def list_item_types(
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MENU_VIEW),
):
    """The restaurant's own types, in the order its menu reads.

    Flat, and in reading order: each heading is followed by its own
    subcategories. `parent_id` is what tells them apart, so the builder can
    indent without a second request.

    `items` counts what would be orphaned by deleting one, so the builder can
    say so before asking. It is the direct count, never a rolled-up one: it
    is the number the deletion rule reads, and a parent showing its
    children's items would say a type is in use when nothing is filed on it.
    """
    types = load_item_types(db)
    counts = dict(
        db.execute(
            select(Item.item_type_id, func.count(Item.id))
            .where(Item.deleted_at.is_(None))
            .group_by(Item.item_type_id)
        ).all()
    )
    return [
        {
            "id": str(t.id),
            "name": t.name,
            "parent_id": str(t.parent_id) if t.parent_id else None,
            "sort_order": t.sort_order,
            "items": counts.get(t.id, 0),
        }
        for t in types
    ]


@router.post("/item-types", status_code=201)
def create_item_type(
    body: ItemTypeIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    """Add a type, or a subcategory inside one.

    Names are unique across the whole restaurant rather than within a parent.
    The item form offers one flat list, so two entries both reading "Regular"
    under different headings would be a choice nobody can make correctly.
    """
    name = body.name.strip()
    if not name:
        raise errors.validation_error("An item type needs a name.")
    if _type_name_taken(db, name):
        raise errors.validation_error(f"There is already a type called {name}.")

    parent = _live_parent(db, body.parent_id) if body.parent_id else None

    # Added at the end unless told otherwise: a new type is not usually meant
    # to jump to the top of the menu. Among its own siblings, though -- a
    # subcategory goes last under its parent, not last on the whole menu,
    # because sort_order is read within the level it sits on.
    sort_order = body.sort_order
    if sort_order is None:
        highest = db.execute(
            select(func.max(ItemType.sort_order)).where(
                ItemType.deleted_at.is_(None),
                ItemType.parent_id == (parent.id if parent else None),
            )
        ).scalar()
        sort_order = (highest or 0) + 1

    item_type = ItemType(
        restaurant_id=restaurant.id, name=name,
        parent_id=parent.id if parent else None, sort_order=sort_order,
    )
    db.add(item_type)
    db.flush()
    return {
        "id": str(item_type.id),
        "name": item_type.name,
        "parent_id": str(parent.id) if parent else None,
        "sort_order": sort_order,
    }


@router.patch("/item-types/{type_id}")
def update_item_type(
    type_id: UUID,
    body: ItemTypeUpdateIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    """Rename a type, move it up or down, or file it under another one.

    Renaming reaches every item of that type at once, which is the point of
    the type being a row: correcting "Drinks" to "Beverages" is one edit, not
    one per item. Nothing else changes -- an item does not move, and a combo
    slot asking for this type still asks for it.

    Refiling is the one that can be refused. A heading that combos or
    modifier groups are built on cannot become a subcategory while they are,
    because both of those read top-level types only; and a heading with
    subcategories of its own cannot be filed under a third, because menus go
    two levels deep. Both are refused with what is in the way rather than
    silently undone, so the answer is "remove that first", not "it didn't
    save and I don't know why".
    """
    item_type = _live_type(db, type_id)
    sent = body.model_dump(exclude_unset=True)

    if "name" in sent:
        name = (sent["name"] or "").strip()
        if not name:
            raise errors.validation_error("An item type needs a name.")
        if _type_name_taken(db, name, excluding=item_type.id):
            raise errors.validation_error(f"There is already a type called {name}.")
        item_type.name = name

    if "parent_id" in sent:
        _refile(db, item_type, sent["parent_id"])

    if sent.get("sort_order") is not None:
        item_type.sort_order = sent["sort_order"]

    return {
        "id": str(item_type.id),
        "name": item_type.name,
        "parent_id": str(item_type.parent_id) if item_type.parent_id else None,
        "sort_order": item_type.sort_order,
    }


def _refile(db: Session, item_type: ItemType, parent_id: UUID | None) -> None:
    """Move a type under another one, or back out to the top level.

    Promoting -- parent_id null -- is always allowed: a subcategory becoming
    a heading of its own breaks nothing, because nothing structural was
    pointing at it while it was a subcategory.

    Demoting is the direction with rules, and all three are about something
    that already reads this type as top-level.
    """
    if parent_id is None:
        item_type.parent_id = None
        return

    if item_type.parent_id == parent_id:
        return  # already there; nothing to check and nothing to do

    children = _children_of(db, item_type.id)
    if children:
        names = ", ".join(child.name for child in children)
        raise errors.validation_error(
            f"{item_type.name} has subcategories of its own ({names}), and a "
            "menu goes two levels deep. Move those out first."
        )

    # Live combos only. A deleted combo keeps its slots -- nothing reads them
    # once it is hidden -- and counting those made a type that had ever been in
    # a combo impossible to file under anything, with a message pointing at
    # combos nobody could see or edit.
    combos = db.execute(
        select(Combo.name)
        .join(ComboSlot, ComboSlot.combo_id == Combo.id)
        .where(ComboSlot.item_type_id == item_type.id, Combo.deleted_at.is_(None))
        .distinct()
        .order_by(Combo.name)
    ).scalars().all()
    if combos:
        raise errors.validation_error(
            f"{item_type.name} is a choice in {len(combos)} "
            f"{'combo' if len(combos) == 1 else 'combos'} ({', '.join(combos)}). Combos "
            "are built from top-level types, so take it out of those first."
        )

    links = db.execute(
        select(func.count(ModifierGroupItemType.id)).where(
            ModifierGroupItemType.item_type_id == item_type.id
        )
    ).scalar_one()
    if links:
        raise errors.validation_error(
            f"{links} modifier {'group is' if links == 1 else 'groups are'} "
            f"offered for {item_type.name}. Those are set on top-level types, "
            "so change them first."
        )

    item_type.parent_id = _live_parent(db, parent_id, child=item_type).id


@router.delete("/item-types/{type_id}")
def delete_item_type(
    type_id: UUID,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    """Remove a type nothing is using.

    A type still on items is refused rather than cascading. Deleting it would
    take real menu items with it, or leave them under a heading that no
    longer exists, and neither is something to do on one click. The count is
    in the message so the answer is actionable: move those items to another
    type first.

    A heading with subcategories under it is refused for the same reason: the
    subcategories hold the items, and removing the heading would leave them
    with nothing to appear under. The message names them, so the answer is to
    empty and remove those first, or to promote them to headings of their own.

    Soft delete, because a combo slot and a modifier group can point at a
    type, and those rows should not be left dangling.
    """
    item_type = _live_type(db, type_id)

    children = _children_of(db, item_type.id)
    if children:
        names = ", ".join(child.name for child in children)
        raise errors.validation_error(
            f"{item_type.name} still has {names} under it. Move those out or "
            "delete them first."
        )

    in_use = db.execute(
        select(func.count(Item.id)).where(
            Item.item_type_id == item_type.id, Item.deleted_at.is_(None)
        )
    ).scalar_one()
    if in_use:
        raise errors.validation_error(
            f"{in_use} {'item is' if in_use == 1 else 'items are'} still typed as "
            f"{item_type.name}. Move them to another type first."
        )

    # A slot asking for a type nobody can fill is a combo nobody can order, so
    # those slots go with it. The combos themselves stay: the builder shows
    # them as needing a choice put back.
    for slot in db.execute(
        select(ComboSlot).where(ComboSlot.item_type_id == item_type.id)
    ).scalars().all():
        db.delete(slot)

    for link in db.execute(
        select(ModifierGroupItemType).where(
            ModifierGroupItemType.item_type_id == item_type.id
        )
    ).scalars().all():
        db.delete(link)

    item_type.deleted_at = utcnow()
    return {"id": str(item_type.id), "deleted": True}


def _set_modifier_links(db: Session, restaurant: Restaurant, item: Item, group_ids) -> None:
    """Make the item offer exactly these modifier groups, in this order.

    Links that survive are kept rather than deleted and recreated, so a group
    an item already had keeps its row. Only the sort_order is restated, which
    is what carries the order the builder chose.
    """
    wanted = list(dict.fromkeys(group_ids))
    for group_id in wanted:
        group = db.get(ModifierGroup, group_id)
        if group is None or group.deleted_at is not None:
            raise errors.validation_error("Unknown modifier group.")

    existing = {
        link.group_id: link
        for link in db.execute(
            select(ItemModifierGroup).where(ItemModifierGroup.item_id == item.id)
        ).scalars().all()
    }

    for group_id, link in existing.items():
        if group_id not in wanted:
            db.delete(link)

    for index, group_id in enumerate(wanted):
        link = existing.get(group_id)
        if link is None:
            db.add(
                ItemModifierGroup(
                    restaurant_id=restaurant.id, item_id=item.id,
                    group_id=group_id, sort_order=index,
                )
            )
        else:
            link.sort_order = index

    # These sessions are autoflush=False, so a pending insert is invisible to
    # the next SELECT until something flushes it. _set_included_options reads
    # these rows back to decide which options an item may come with, and
    # without this it read an empty set and refused every inclusion on a new
    # item -- "Lettuce belongs to a group this item does not offer", about the
    # group ticked seconds earlier in the same request.
    db.flush()


def _set_included_options(
    db: Session, restaurant: Restaurant, item: Item, option_ids
) -> None:
    """Make the item come with exactly these options.

    Each one has to belong to a group the item actually offers. Anything else
    could never be ordered -- pricing only accepts options from the item's own
    groups -- so an inclusion outside them would be a silent lie about what
    the item comes with rather than a harmless extra row.

    Called after the groups are set, for that reason: an item being given the
    Veggies group and its lettuce in one request has to see the group first.
    """
    wanted = list(dict.fromkeys(option_ids))
    if not wanted:
        for link in db.execute(
            select(ItemIncludedOption).where(ItemIncludedOption.item_id == item.id)
        ).scalars().all():
            db.delete(link)
        return

    offered = {
        link.group_id
        for link in db.execute(
            select(ItemModifierGroup).where(ItemModifierGroup.item_id == item.id)
        ).scalars().all()
    }

    for option_id in wanted:
        option = db.get(ModifierOption, option_id)
        if option is None or option.deleted_at is not None:
            raise errors.validation_error("No such modifier option.")
        if option.group_id not in offered:
            raise errors.validation_error(
                f"{option.name} belongs to a group {item.name} does not offer, "
                "so it cannot be included with it."
            )

    existing = {
        link.option_id: link
        for link in db.execute(
            select(ItemIncludedOption).where(ItemIncludedOption.item_id == item.id)
        ).scalars().all()
    }

    for option_id, link in existing.items():
        if option_id not in wanted:
            db.delete(link)

    for option_id in wanted:
        if option_id not in existing:
            db.add(
                ItemIncludedOption(
                    restaurant_id=restaurant.id, item_id=item.id, option_id=option_id
                )
            )
    db.flush()


def _set_meal_links(db: Session, restaurant: Restaurant, item: Item, meal_ids) -> None:
    """Make exactly these meal periods serve the item.

    A link that already exists is left alone rather than replaced, so its
    created_at survives -- that timestamp is the tiebreaker deciding where
    the item sits in the period, and recreating it would send the item to the
    bottom of the list on every unrelated edit.
    """
    wanted = list(dict.fromkeys(meal_ids))
    for meal_id in wanted:
        meal = db.get(Meal, meal_id)
        if meal is None or meal.deleted_at is not None:
            raise errors.validation_error("That meal period does not exist.")

    existing = {
        link.meal_id: link
        for link in db.execute(
            select(MealItem).where(MealItem.item_id == item.id)
        ).scalars().all()
    }

    for meal_id, link in existing.items():
        if meal_id not in wanted:
            db.delete(link)

    for meal_id in wanted:
        if meal_id not in existing:
            db.add(
                MealItem(restaurant_id=restaurant.id, meal_id=meal_id, item_id=item.id)
            )


@router.get("/items")
def list_items(
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MENU_VIEW),
):
    """The item library: everything the restaurant sells, served or not.

    /menu answers the same items arranged by meal period, which is the menu.
    This is the list they are defined in, so an item can be written once and
    put on breakfast and lunch without being typed twice.
    """
    items = db.execute(
        select(Item)
        .where(Item.deleted_at.is_(None))
        # The same tie the rest of the menu uses. Editing an item must not
        # move it in this list.
        .order_by(Item.sort_order, Item.created_at, Item.id)
        .options(
            selectinload(Item.modifier_links).joinedload(ItemModifierGroup.group),
            selectinload(Item.meal_links),
            selectinload(Item.included_links),
        )
    ).scalars().all()

    return [
        {
            "id": str(item.id),
            "name": item.name,
            "item_type_id": str(item.item_type_id),
            "description": item.description,
            "calories": item.calories,
            "base_price_minor": item.base_price_minor,
            "currency": item.currency,
            "is_available": item.is_available,
            "tax_exempt": item.tax_exempt,
            # The key is what an edit sends back unchanged; the URL is what the
            # thumbnail shows.
            "image_path": item.image_path,
            "image_url": images.image_url(item.image_path),
            "meal_ids": [str(link.meal_id) for link in item.meal_links],
            "modifier_groups": [
                {"id": str(link.group.id), "name": link.group.name}
                for link in item.modifier_links
                if link.group.deleted_at is None
            ],
            "included_option_ids": [str(link.option_id) for link in item.included_links],
        }
        for item in items
    ]


@router.post("/items", status_code=201)
def create_item(
    body: ItemIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    name = _required_name(body.name, "An item needs a name.")
    item_type = _live_type(db, body.item_type_id)
    item = Item(
        restaurant_id=restaurant.id, name=name, item_type_id=item_type.id,
        description=(body.description or "").strip() or None,
        calories=body.calories,
        base_price_minor=body.base_price_minor, tax_exempt=body.tax_exempt,
        currency=restaurant.currency, sort_order=body.sort_order,
        image_path=images.accept(body.image_path, restaurant.id, ImageKind.ITEMS),
    )
    db.add(item)
    db.flush()

    _set_modifier_links(db, restaurant, item, body.modifier_group_ids)
    # After the groups: an inclusion is only valid against a group the item
    # already offers.
    _set_included_options(db, restaurant, item, body.included_option_ids)
    _set_meal_links(db, restaurant, item, body.meal_ids)
    db.flush()
    return {"id": str(item.id), "name": item.name, "item_type_id": str(item.item_type_id)}


@router.patch("/items/{item_id}")
def update_item(
    item_id: UUID,
    body: ItemUpdateIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    """Edit an item: what it is called, what it costs, what type it is,
    which periods serve it, which groups it offers and what it comes with.

    Changing a price changes what the next order is charged, and nothing
    before it. Order lines store the price they were charged at, so a
    correction today cannot rewrite what someone already paid. The same edit
    now reaches every meal period at once, because there is one item rather
    than a copy per period.

    Availability is not settable here. It is the kitchen's sold-out toggle,
    open to the whole floor, while this is menu editing and restricted to
    managers -- so the two stay separate endpoints with separate roles.
    """
    item = db.get(Item, item_id)
    if item is None or item.deleted_at is not None:
        raise errors.validation_error("No such item.")

    # Only the fields the caller actually sent. Reading the attributes
    # directly cannot tell "set this to null" apart from "left it out", and
    # would blank a description on every price edit.
    sent = body.model_dump(exclude_unset=True)

    if "name" in sent:
        # Trimmed here rather than in the schema, so a name of nothing but
        # spaces is refused instead of stored as a blank row.
        name = (sent["name"] or "").strip()
        if not name:
            raise errors.validation_error("An item needs a name.")
        item.name = name

    if "description" in sent:
        description = (sent["description"] or "").strip()
        item.description = description or None

    if "calories" in sent:
        item.calories = sent["calories"]

    if "base_price_minor" in sent:
        item.base_price_minor = sent["base_price_minor"]

    # Like a price, it changes what the next order is charged and nothing
    # before it: an order keeps the tax it was charged.
    if sent.get("tax_exempt") is not None:
        item.tax_exempt = sent["tax_exempt"]

    if sent.get("item_type_id") is not None:
        # Changing the type moves the item to another heading and changes
        # which groups the builder offers it. The groups it already carries
        # are left alone: they were chosen deliberately, and dropping them
        # silently would lose work on what is often a correction.
        item.item_type_id = _live_type(db, sent["item_type_id"]).id

    if sent.get("modifier_group_ids") is not None:
        _set_modifier_links(db, restaurant, item, sent["modifier_group_ids"])

    # After the groups in the same request, for the same reason as create. A
    # request that narrows the groups and drops the inclusions that went with
    # them has to apply them in that order or the second half is refused.
    if sent.get("included_option_ids") is not None:
        _set_included_options(db, restaurant, item, sent["included_option_ids"])

    if sent.get("meal_ids") is not None:
        _set_meal_links(db, restaurant, item, sent["meal_ids"])

    # "image_path" in sent, not a truthiness test: null is how a picture is
    # taken off, and has to be told apart from the field being left out. The
    # file it replaces goes only after the commit, and only if nothing else
    # still shows it.
    if "image_path" in sent:
        previous = item.image_path
        item.image_path = images.accept(sent["image_path"], restaurant.id, ImageKind.ITEMS)
        if previous != item.image_path:
            images.release(db, previous)

    return {
        "id": str(item.id),
        "name": item.name,
        "item_type_id": str(item.item_type_id),
        "description": item.description,
        "calories": item.calories,
        "base_price_minor": item.base_price_minor,
        "tax_exempt": item.tax_exempt,
    }


@router.delete("/items/{item_id}")
def delete_item(
    item_id: UUID,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    """Take an item off the menu for good, in every period at once.

    Distinct from the sold-out toggle next door, which the whole kitchen can
    flip and which the item comes back from, and distinct from removing it
    from one meal period. This one is for menu editing, so it is MANAGE, and
    there is no way back through the portal.

    Soft delete: order_items carries a foreign key to menu_items, so removing
    the row would either fail on that key or take paid orders with it. The
    meal links go outright, because a link to a deleted item is a listing
    nobody can order from.

    A cart already holding this item is not a problem: pricing refuses a
    deleted item at checkout rather than charging for something that is no
    longer sold.
    """
    item = db.get(Item, item_id)
    if item is None or item.deleted_at is not None:
        raise errors.validation_error("No such item.")

    for link in db.execute(
        select(MealItem).where(MealItem.item_id == item.id)
    ).scalars().all():
        db.delete(link)

    item.deleted_at = utcnow()
    return {"id": str(item.id), "deleted": True}


@router.get("/stock")
def stock(
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(FLOOR_VIEW),
):
    """What is in stock, for the people who run out of it.

    The sold-out toggle has always been open to the whole floor, but the only
    screen with one read /items, which is menu editing and managers only -- so
    the kitchen staff the toggle exists for could not reach it. This is the
    same list cut down to what flipping it needs: no prices, no modifier
    groups, nothing a kitchen login has no business reading.

    In menu order, headings first, so the list reads the way the menu does.
    `type` is named with its heading when it is a subcategory, since this
    screen shows it away from the heading it sits under.
    """
    types = load_item_types(db)
    position = {t.id: index for index, t in enumerate(types)}
    by_id = {t.id: t for t in types}

    def label(type_id) -> str:
        item_type = by_id.get(type_id)
        if item_type is None:
            return "No type"
        parent = by_id.get(item_type.parent_id) if item_type.parent_id else None
        return f"{parent.name} / {item_type.name}" if parent else item_type.name

    items = db.execute(
        select(Item)
        .where(Item.deleted_at.is_(None))
        .order_by(Item.sort_order, Item.created_at, Item.id)
    ).scalars().all()
    # A stable sort on the type's place in the menu keeps the item order above
    # within each type. Items of a type since deleted go last.
    items = sorted(items, key=lambda i: position.get(i.item_type_id, len(types)))

    return [
        {
            "id": str(item.id),
            "name": item.name,
            "type": label(item.item_type_id),
            "is_available": item.is_available,
        }
        for item in items
    ]


@router.patch("/items/{item_id}/availability")
def set_item_availability(
    item_id: UUID,
    is_available: bool,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(ANY_STAFF),
):
    """Manual sold-out toggle. Overrides everything else."""
    item = db.get(Item, item_id)
    if item is None or item.deleted_at is not None:
        raise errors.validation_error("No such item.")
    item.is_available = is_available
    return {"id": str(item.id), "is_available": is_available}


@router.get("/orders")
def order_board(
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(FLOOR_VIEW),
):
    """The live board. Polled every few seconds by the kitchen screen.

    PENDING_PAYMENT orders are deliberately excluded: nobody has paid, and
    showing them to the kitchen would start food on an unconfirmed order.

    Each ticket carries its payment status. A refund from the restaurant's
    Stripe Dashboard changes the payment and never the order (rule 26), so
    without this a refunded order stayed on the board looking like any other,
    and the kitchen made food nobody was paying for. pin_locked says the
    counter cannot hand it over any more, so the screen can offer the manager
    override instead of a PIN box that only answers "locked".
    """
    active = [
        OrderStatus.AUTO_ACCEPTED.value,
        OrderStatus.PREPARING.value,
        OrderStatus.READY_FOR_PICKUP.value,
        # A delivery is still the kitchen's order until the driver has it, and
        # the counter still wants to see where it got to afterwards.
        OrderStatus.READY_FOR_DELIVERY.value,
        OrderStatus.OUT_FOR_DELIVERY.value,
    ]
    orders = db.execute(
        select(Order)
        .where(Order.status.in_(active))
        .order_by(Order.created_at)
        .options(selectinload(Order.items).selectinload(OrderItem.modifiers))
    ).scalars().all()

    payment_status = dict(
        db.execute(
            select(Payment.order_id, Payment.status).where(
                Payment.order_id.in_([o.id for o in orders])
            )
        ).all()
    ) if orders else {}
    driver_names = _driver_names(orders)

    return [
        {
            "order_id": str(o.id),
            "order_number": o.order_number,
            "status": o.status,
            "total_minor": o.total_minor,
            "currency": o.currency,
            "created_at": o.created_at.isoformat(),
            # When the kitchen's clock starts. created_at is when checkout
            # began, which can be minutes before the payment that put the
            # ticket on the board; timing from it made every order look late.
            "paid_at": o.paid_at.isoformat() if o.paid_at else None,
            "customer_note": o.customer_note,
            "payment_status": payment_status.get(o.id),
            "pin_locked": o.pickup_pin_failed_attempts >= PIN_ATTEMPTS,
            # Delivery, and who is running it. Null on a pickup order, which
            # is every order until a manager assigns a driver.
            "fulfillment_type": o.fulfillment_type,
            "delivery_address": o.delivery_address,
            # Non-zero when the customer chose and paid for delivery at
            # checkout, which is what stops it being turned back into a
            # collection from the board.
            "delivery_fee_minor": o.delivery_fee_minor,
            "driver": driver_names.get(o.driver_user_id),
            # Who to call and whose name to call out. Null on orders from
            # before checkout asked for them.
            "contact_name": o.contact_name,
            "contact_phone": o.contact_phone,
            # combo_name and combo_group ride along so the screen can draw a
            # meal deal as one block. Without them a combo reads as three
            # unrelated items and gets plated as three separate orders.
            "items": [
                {
                    "name": i.name_snapshot,
                    "combo_name": i.combo_name_snapshot,
                    "combo_group": i.combo_group,
                    "quantity": i.quantity,
                    "note": i.item_note,
                    "modifiers": [
                        f"{m.group_name_snapshot}: {m.option_name_snapshot}"
                        for m in i.modifiers
                    ],
                }
                for i in o.items
            ],
        }
        for o in orders
    ]


HISTORY_LIMIT = 200


@router.get("/orders/history")
def order_history(
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(FLOOR_VIEW),
):
    """Today's orders that have left the board, newest first.

    Once handed over or cancelled, an order vanished from every screen the
    counter has, so "I ordered twenty minutes ago, where is it?" had no answer
    short of the Stripe Dashboard. This is today -- the restaurant's today, in
    its timezone -- by the day each order was paid.

    Each carries the last thing staff did to it, from order_events: who handed
    it over or cancelled it, and for an override or a cancellation, the reason
    they gave -- which is what settles a disputed pickup.
    """
    zone = _zone(restaurant.timezone)
    today = datetime.now(zone).date()
    start = datetime.combine(today, time(0), tzinfo=zone)
    end = datetime.combine(today + timedelta(days=1), time(0), tzinfo=zone)

    orders = db.execute(
        select(Order)
        .where(
            Order.status.in_([OrderStatus.COMPLETED.value, OrderStatus.CANCELLED.value]),
            Order.paid_at >= start,
            Order.paid_at < end,
        )
        .order_by(func.coalesce(Order.completed_at, Order.updated_at).desc())
        .limit(HISTORY_LIMIT)
        .options(selectinload(Order.items).selectinload(OrderItem.modifiers))
    ).scalars().all()

    # The last event per order: what left it here, and who did it.
    last_event: dict = {}
    if orders:
        for event in db.execute(
            select(OrderEvent)
            .where(OrderEvent.order_id.in_([o.id for o in orders]))
            .order_by(OrderEvent.created_at)
        ).scalars().all():
            last_event[event.order_id] = event

    names: dict = {}
    actor_ids = {event.actor_user_id for event in last_event.values()}
    if actor_ids:
        with system_session() as sys_db:
            for user in sys_db.execute(select(User).where(User.id.in_(actor_ids))).scalars():
                names[user.id] = user.full_name or user.email

    payment_status = dict(
        db.execute(
            select(Payment.order_id, Payment.status).where(
                Payment.order_id.in_([o.id for o in orders])
            )
        ).all()
    ) if orders else {}

    out = []
    for o in orders:
        event = last_event.get(o.id)
        out.append({
            "order_id": str(o.id),
            "order_number": o.order_number,
            "status": o.status,
            "total_minor": o.total_minor,
            "currency": o.currency,
            "paid_at": o.paid_at.isoformat() if o.paid_at else None,
            "finished_at": (o.completed_at or o.updated_at).isoformat(),
            "payment_status": payment_status.get(o.id),
            "fulfillment_type": o.fulfillment_type,
            "delivery_address": o.delivery_address,
            "items": [
                {"name": i.name_snapshot, "quantity": i.quantity, "combo_name": i.combo_name_snapshot}
                for i in o.items
            ],
            # Null for an order finished before events were recorded.
            "last_action": None if event is None else {
                "action": event.action,
                "by": names.get(event.actor_user_id),
                "reason": event.reason,
            },
        })
    return {"date": today.isoformat(), "timezone": zone.key, "orders": out}


@router.post("/orders/{order_id}/ready")
def mark_ready(
    order_id: UUID,
    background: BackgroundTasks,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(ANY_STAFF),
):
    order = db.get(Order, order_id, with_for_update=True)
    if order is None:
        raise errors.order_not_found()
    # One button on the board, two destinations: a delivery is ready for its
    # driver, not for a counter, and it is the order that knows which it is.
    ready = (
        OrderStatus.READY_FOR_DELIVERY.value
        if order.fulfillment_type == FulfillmentType.DELIVERY.value
        else OrderStatus.READY_FOR_PICKUP.value
    )
    transition(order, ready)
    _record(db, order, membership, OrderEventAction.MARKED_READY)
    # A collection is told it can come. A delivery waits for its driver, and
    # hears when they set off.
    if ready == OrderStatus.READY_FOR_PICKUP.value:
        background.add_task(order_emails.queue, "order_ready", restaurant.id, order.id)
    return {"order_id": str(order.id), "status": order.status}


def _record(
    db: Session, order: Order, membership: RestaurantUser, action: OrderEventAction,
    reason: str | None = None,
) -> None:
    """Write down who did this to the order, in the same transaction as the
    change itself, so there is never a transition without its record."""
    db.add(
        OrderEvent(
            restaurant_id=order.restaurant_id, order_id=order.id,
            actor_user_id=membership.user_id, action=action.value, reason=reason,
        )
    )


class OrderReasonIn(BaseModel):
    """Why staff are doing something the ordinary flow would not allow.
    Required: an override or a cancellation with no reason is one nobody can
    review afterwards."""

    reason: str = Field(min_length=1, max_length=200)


def _reason(body: OrderReasonIn) -> str:
    reason = body.reason.strip()
    if len(reason) < 3:
        raise errors.validation_error("Say why, in a few words. It is kept with the order.")
    return reason


def _require_paid(db: Session, order: Order) -> Payment:
    payment = db.execute(select(Payment).where(Payment.order_id == order.id)).scalar_one_or_none()
    if payment is None or payment.succeeded_at is None:
        raise errors.payment_not_confirmed("This order has not been paid.")
    return payment


PIN_ATTEMPTS = 5

# Wrong PINs one staff member may enter across every order, per window. The
# per-order lock stops a PIN being guessed; this stops one account -- a stolen
# tablet, say -- spending five guesses on each of the day's orders. Only wrong
# PINs count, so a cashier handing over order after order is never slowed.
PIN_FAILURES_PER_STAFF = 20
PIN_FAILURE_WINDOW_SECONDS = 600


def _pin_locked() -> errors.ApiError:
    return errors.ApiError(
        423, "PIN_LOCKED", "Too many attempts. Hand it over without the PIN, with a reason."
    )


class CompleteOrderIn(BaseModel):
    # In the body, never the URL: a query string is written to the nginx and
    # uvicorn access logs, which put every customer's PIN on disk.
    pin: str = Field(min_length=1, max_length=12)


@router.post("/orders/{order_id}/complete")
def complete_order(
    order_id: UUID,
    body: CompleteOrderIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(ANY_STAFF),
):
    """Hand the food over. PIN verified server side, five attempts then lock.

    Failed attempts are counted on the order row so the lock survives a page
    refresh or a different staff device.
    """
    import hmac

    from app.core.crypto import decrypt_field

    failures = f"pickup_pin_failures:staff:{membership.user_id}"
    if ratelimit.failures_exhausted(failures, PIN_FAILURES_PER_STAFF, PIN_FAILURE_WINDOW_SECONDS):
        raise errors.ApiError(
            429, "RATE_LIMITED",
            "Too many wrong PINs from this account. Wait a few minutes and try again.",
        )

    # FOR UPDATE, so two wrong guesses sent at once are counted one after the
    # other rather than both reading the same count and writing back one more.
    order = db.get(Order, order_id, with_for_update=True)
    if order is None:
        raise errors.order_not_found()
    if order.pickup_pin_failed_attempts >= PIN_ATTEMPTS:
        raise _pin_locked()
    if order.pickup_pin_encrypted is None:
        raise errors.order_state_conflict("This order has no pickup PIN.")

    if not hmac.compare_digest(decrypt_field(order.pickup_pin_encrypted), body.pin.strip()):
        order.pickup_pin_failed_attempts += 1
        attempts = order.pickup_pin_failed_attempts
        # Committed here, before the refusal is raised. The request's session
        # rolls back on any exception, and it used to take this count with it:
        # the lock never engaged, and a PIN could be guessed without limit.
        db.commit()
        ratelimit.record_failure(failures, PIN_FAILURE_WINDOW_SECONDS)
        if attempts >= PIN_ATTEMPTS:
            raise _pin_locked()
        left = PIN_ATTEMPTS - attempts
        raise errors.ApiError(
            400, "PIN_INVALID",
            f"That PIN does not match. {left} {'attempt' if left == 1 else 'attempts'} left.",
        )

    _require_paid(db, order)

    transition(order, OrderStatus.COMPLETED.value)
    order.completed_at = utcnow()
    _record(db, order, membership, OrderEventAction.COMPLETED_WITH_PIN)
    return {"order_id": str(order.id), "status": order.status}


@router.post("/orders/{order_id}/override-complete")
def override_complete(
    order_id: UUID,
    body: OrderReasonIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(ANY_STAFF),
):
    """Hand an order over without the customer's PIN.

    For the customer whose phone died, and for the order five wrong PINs
    locked -- which before this could never leave the board. The whole floor
    may, because the cashier at the counter is who the customer is standing
    in front of. Always with a reason, and recorded against whoever did it:
    it is the one way food leaves the counter without proof the right person
    took it.

    Still only from READY_FOR_PICKUP and only once paid, the same as the PIN
    route. It skips the PIN, not the rest of the order's rules.
    """
    reason = _reason(body)
    order = db.get(Order, order_id, with_for_update=True)
    if order is None:
        raise errors.order_not_found()
    _require_paid(db, order)

    transition(order, OrderStatus.COMPLETED.value)
    order.completed_at = utcnow()
    _record(db, order, membership, OrderEventAction.COMPLETED_BY_OVERRIDE, reason)
    return {"order_id": str(order.id), "status": order.status}


class CancelOrderIn(OrderReasonIn):
    """Why, and whether the customer gets their money back.

    Refunding is the ordinary case and the portal ticks it, but not every
    cancellation is one: a no-show the restaurant cooked for is cancelled
    without a refund, and that has to stay expressible. The order keeps its
    refund afterwards either way -- see POST /orders/{id}/refund.
    """

    refund: bool = True


@router.post("/orders/{order_id}/cancel")
def cancel_order(
    order_id: UUID,
    body: CancelOrderIn,
    background: BackgroundTasks,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(ANY_STAFF),
):
    """Take a paid order off the board: a no-show, a refund, a mistake.

    The whole floor may, kitchen and cashier included, with the same refund
    choice a manager has: a refund only ever goes back to the card that paid,
    and the reason is kept against whoever did it. Refunding an order that is
    already cancelled stays with managers (POST /orders/{id}/refund).

    With `refund` the customer's money goes back on the same card, whole,
    including Zenoeats' fee. Without it nothing moves and the order keeps a
    refund to issue, which the board says out loud.

    The refund is asked of Stripe after the cancellation is written, never
    inside the transaction (rule 6). A refund that fails therefore leaves the
    order cancelled and the money where it was, which is the safe way round:
    whoever cancelled is told, every admin and manager is emailed, and a
    manager can try again from the board or from Stripe.

    An unpaid order is refused. It never reached the board, it expires by
    itself, and cancelling one while the customer is still on the card step
    would leave a payment landing on an order that no longer exists.
    """
    reason = _reason(body)
    order = db.get(Order, order_id, with_for_update=True)
    if order is None:
        raise errors.order_not_found()
    if order.status == OrderStatus.PENDING_PAYMENT.value:
        raise errors.order_state_conflict(
            "This order has not been paid. It expires on its own if payment never arrives."
        )
    payment = _require_paid(db, order)

    transition(order, OrderStatus.CANCELLED.value, reason="CANCELLED_BY_RESTAURANT")
    _record(db, order, membership, OrderEventAction.CANCELLED, reason)

    problem = None
    if body.refund and _refundable(payment):
        db.flush()
        problem = _refund(db, order, payment, membership, reason)

    # With the refund in it if one went through; without, if not.
    background.add_task(order_emails.queue, "order_cancelled", restaurant.id, order.id)
    if problem:
        # Whoever cancelled saw it here, and may be gone by the time anyone
        # thinks of it again. Every admin and manager hears.
        background.add_task(
            staff_emails.queue, "refund_failed", restaurant.id,
            order_id=order.id, problem=problem, at=utcnow().isoformat(),
        )
    return {
        "order_id": str(order.id),
        "status": order.status,
        "payment_status": payment.status,
        "refund_needed": _refundable(payment),
        # Null unless the refund was asked for and Stripe refused it. The
        # cancellation still stands; only the money did not move.
        "refund_problem": problem,
    }


def _refundable(payment: Payment) -> bool:
    """Whether there is still money to give back."""
    return payment.status not in (
        PaymentStatus.REFUNDED.value, PaymentStatus.REFUND_PENDING.value
    )


def _refund(db: Session, order: Order, payment: Payment, membership, reason: str) -> str | None:
    """Ask Stripe for the refund and write down what came back.

    Returns the problem to show the manager, or None when the refund is on
    its way. The refund webhook remains the authority on the amount: this
    records that one was accepted, and `charge.refunded` records what was
    actually returned, cumulatively, however many times it is delivered.
    """
    try:
        refund = stripe_service.refund_order(payment, reason)
    except errors.ApiError as refused:
        _record(db, order, membership, OrderEventAction.CANCELLED, f"Refund failed: {reason}")
        return refused.detail["message"]

    # succeeded is the ordinary answer for a card; pending happens on the
    # slower methods, and the webhook finishes the story for both.
    payment.status = (
        PaymentStatus.REFUNDED.value if refund.get("status") == "succeeded"
        else PaymentStatus.REFUND_PENDING.value
    )
    if refund.get("status") == "succeeded":
        payment.refunded_minor = min(int(refund.get("amount") or 0), payment.amount_minor)
    return None


@router.get("/orders/refunds-due")
def refunds_due(
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    """Cancelled orders whose money is still with the restaurant.

    A cancellation leaves the board, so without this a refund nobody issued
    has nowhere left to be seen: the manager who ticked the box, watched
    Stripe refuse it and moved on would never be reminded. Managers only --
    it is a list of money owed, and the counter has no use for it.

    A fortnight is the window. Older than that and the refund has either
    happened elsewhere or become a conversation rather than a button.
    """
    since = utcnow() - timedelta(days=14)
    rows = db.execute(
        select(Order, Payment)
        .join(Payment, Payment.order_id == Order.id)
        .where(
            Order.status == OrderStatus.CANCELLED.value,
            Order.created_at >= since,
            Payment.succeeded_at.is_not(None),
            Payment.status == PaymentStatus.PAID.value,
        )
        .order_by(Order.created_at.desc())
        .limit(50)
    ).all()
    return [
        {
            "order_id": str(order.id),
            "order_number": order.order_number,
            "status": order.status,
            "total_minor": order.total_minor,
            "currency": order.currency,
            "created_at": order.created_at.isoformat(),
            "payment_status": payment.status,
            "cancelled_reason": order.cancelled_reason,
        }
        for order, payment in rows
    ]


@router.post("/orders/{order_id}/refund")
def refund_order(
    order_id: UUID,
    body: OrderReasonIn,
    background: BackgroundTasks,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(MANAGE),
):
    """Give a cancelled order's money back, whole.

    For the cancellation that was made without one, and for the refund that
    Stripe refused the first time. Only a cancelled order: refunding an order
    the kitchen is still cooking would leave a customer owed food they have
    not paid for, and cancelling is the decision that comes first.
    """
    reason = _reason(body)
    order = db.get(Order, order_id, with_for_update=True)
    if order is None:
        raise errors.order_not_found()
    if order.status != OrderStatus.CANCELLED.value:
        raise errors.order_state_conflict("Cancel this order before refunding it.")
    payment = _require_paid(db, order)
    if not _refundable(payment):
        raise errors.order_state_conflict("This order has already been refunded.")

    db.flush()
    refunded_before = payment.refunded_minor
    problem = _refund(db, order, payment, membership, reason)
    if problem:
        raise errors.ApiError(502, "REFUND_FAILED", problem)
    background.add_task(
        order_emails.queue, "refund_issued", restaurant.id, order.id,
        refunded_before=refunded_before,
    )
    return {
        "order_id": str(order.id),
        "status": order.status,
        "payment_status": payment.status,
        "refund_needed": _refundable(payment),
    }


# ------------------------------------------------------------- delivery ----
#
# Two ways an order becomes a delivery. The customer chooses it at checkout,
# with their address and a fee priced from it. Or a restaurant agrees over the
# phone to run a collection out itself, and a manager gives the paid order to
# a driver with the address they were told. Either way a manager assigns the
# driver, and the driver's screen is that order and no more of the portal.


class AssignDriverIn(BaseModel):
    # The membership, not the person: it is what proves this driver works
    # here, and it is what the team list already gives the portal.
    membership_id: UUID
    delivery_address: str = Field(min_length=1, max_length=300)


def _driver_names(orders) -> dict:
    """The name to show for each order's driver, read in one go.

    users is a platform table -- there is no tenant policy on it -- so it is
    read with the system role, exactly as the team list does.
    """
    ids = {o.driver_user_id for o in orders if o.driver_user_id}
    if not ids:
        return {}
    with system_session() as sys_db:
        return {
            user.id: user.full_name or user.email
            for user in sys_db.execute(select(User).where(User.id.in_(ids))).scalars()
        }


def _delivery_order(db: Session, order_id: UUID, membership: RestaurantUser) -> Order:
    """The order a driver may act on: assigned to them, and still in play.

    A manager may act on any delivery -- a driver on the road calling it in --
    but a driver only on their own. RLS has already confined this to the
    restaurant; this is about which person inside it.
    """
    order = db.get(Order, order_id, with_for_update=True)
    if order is None:
        raise errors.order_not_found()
    if order.fulfillment_type != FulfillmentType.DELIVERY.value:
        raise errors.order_state_conflict("This order is a collection, not a delivery.")
    if (
        membership.role_code == StaffRole.DRIVER.value
        and order.driver_user_id != membership.user_id
    ):
        # Not "this is someone else's": a driver has no way to learn which
        # orders exist but are not theirs.
        raise errors.order_not_found()
    return order


@router.post("/orders/{order_id}/assign-driver")
def assign_driver(
    order_id: UUID,
    body: AssignDriverIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(MANAGE),
):
    """Give a paid order to one of the restaurant's drivers, with the address.

    For a phone order this is what turns a collection into a delivery. For a
    delivery the customer chose at checkout, the address arrives already
    filled in and this only names the driver. Managers only: who takes an
    order out is a staffing decision, not one the counter makes.

    Reassigning is the same call again -- a driver who called in sick has
    their orders handed on, and each assignment is recorded with who did it
    and who took it.
    """
    driver = db.get(RestaurantUser, body.membership_id)
    if (
        driver is None
        or driver.status != StaffStatus.ACTIVE.value
        or driver.role_code != StaffRole.DRIVER.value
    ):
        raise errors.validation_error("Choose a driver from this restaurant's team.")

    order = db.get(Order, order_id, with_for_update=True)
    if order is None:
        raise errors.order_not_found()
    if order.status in (
        OrderStatus.COMPLETED.value, OrderStatus.CANCELLED.value, OrderStatus.EXPIRED.value
    ):
        raise errors.order_state_conflict("This order is finished.")
    _require_paid(db, order)
    if order.status == OrderStatus.READY_FOR_PICKUP.value:
        # It was ready at the counter; a delivery is ready for its driver.
        transition(order, OrderStatus.READY_FOR_DELIVERY.value)

    order.fulfillment_type = FulfillmentType.DELIVERY.value
    order.driver_user_id = driver.user_id
    order.delivery_address = body.delivery_address.strip()

    name = _driver_names([order]).get(driver.user_id) or "a driver"
    _record(db, order, membership, OrderEventAction.ASSIGNED_DRIVER, f"to {name}")
    return {
        "order_id": str(order.id),
        "status": order.status,
        "driver": name,
        "delivery_address": order.delivery_address,
    }


@router.post("/orders/{order_id}/unassign-driver")
def unassign_driver(
    order_id: UUID,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(MANAGE),
):
    """Take a delivery back off its driver: it is a collection again.

    For the customer who rings back to say they will come in after all. The
    driver, the address and the delivery itself are undone, and an order that
    was waiting for a driver is waiting at the counter instead.

    Not once the driver has it. The food has left the building, and pretending
    otherwise would put a PIN prompt in front of a counter that has nothing to
    hand over. Cancel it, or let the driver finish and mark it delivered.
    """
    order = db.get(Order, order_id, with_for_update=True)
    if order is None:
        raise errors.order_not_found()
    if order.fulfillment_type != FulfillmentType.DELIVERY.value:
        raise errors.order_state_conflict("This order is already a collection.")
    if order.delivery_fee_minor:
        # The customer paid for this delivery. Turning it into a collection
        # would keep their fee for a journey nobody makes -- and the database
        # refuses a fee on a collection anyway.
        raise errors.order_state_conflict(
            "The customer paid for delivery. Change the driver, or cancel and refund it."
        )
    if order.status == OrderStatus.OUT_FOR_DELIVERY.value:
        raise errors.order_state_conflict(
            "The driver already has this order. Cancel it, or let them deliver it."
        )

    if order.status == OrderStatus.READY_FOR_DELIVERY.value:
        transition(order, OrderStatus.READY_FOR_PICKUP.value)

    order.fulfillment_type = FulfillmentType.PICKUP.value
    order.driver_user_id = None
    order.delivery_address = None
    _record(db, order, membership, OrderEventAction.UNASSIGNED_DRIVER)
    return {"order_id": str(order.id), "status": order.status, "fulfillment_type": order.fulfillment_type}


@router.get("/drivers")
def drivers(
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    """The drivers a manager may hand an order to.

    Names and membership ids, nothing else. The full team list is admins only,
    and a manager assigning a delivery does not need it -- only who is here to
    drive.
    """
    rows = db.execute(
        select(RestaurantUser).where(
            RestaurantUser.role_code == StaffRole.DRIVER.value,
            RestaurantUser.status == StaffStatus.ACTIVE.value,
        )
    ).scalars().all()

    names = {}
    if rows:
        with system_session() as sys_db:
            for user in sys_db.execute(
                select(User).where(User.id.in_([r.user_id for r in rows]))
            ).scalars():
                names[user.id] = user.full_name or user.email

    return sorted(
        ({"membership_id": str(r.id), "name": names.get(r.user_id, "unknown")} for r in rows),
        key=lambda d: d["name"].lower(),
    )


@router.get("/deliveries")
def deliveries(
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(DELIVERY),
):
    """The deliveries in play: a driver's own, or every one for a manager.

    Everything a driver needs to run the order and nothing else about the
    restaurant: what to take, where to, and what they have already picked up.
    """
    active = [
        OrderStatus.AUTO_ACCEPTED.value,
        OrderStatus.PREPARING.value,
        OrderStatus.READY_FOR_DELIVERY.value,
        OrderStatus.OUT_FOR_DELIVERY.value,
    ]
    query = (
        select(Order)
        .where(
            Order.fulfillment_type == FulfillmentType.DELIVERY.value,
            Order.status.in_(active),
        )
        .order_by(Order.created_at)
        .options(selectinload(Order.items).selectinload(OrderItem.modifiers))
    )
    if membership.role_code == StaffRole.DRIVER.value:
        query = query.where(Order.driver_user_id == membership.user_id)

    orders = db.execute(query).scalars().all()
    names = _driver_names(orders)
    return [
        {
            "order_id": str(o.id),
            "order_number": o.order_number,
            "status": o.status,
            "total_minor": o.total_minor,
            "currency": o.currency,
            "created_at": o.created_at.isoformat(),
            "paid_at": o.paid_at.isoformat() if o.paid_at else None,
            "delivery_address": o.delivery_address,
            "customer_note": o.customer_note,
            "contact_name": o.contact_name,
            "contact_phone": o.contact_phone,
            "driver": names.get(o.driver_user_id),
            "mine": o.driver_user_id == membership.user_id,
            "items": [
                {
                    "name": i.name_snapshot,
                    "combo_name": i.combo_name_snapshot,
                    "combo_group": i.combo_group,
                    "quantity": i.quantity,
                    "note": i.item_note,
                    "modifiers": [
                        f"{m.group_name_snapshot}: {m.option_name_snapshot}"
                        for m in i.modifiers
                    ],
                }
                for i in o.items
            ],
        }
        for o in orders
    ]


@router.post("/orders/{order_id}/picked-up")
def picked_up(
    order_id: UUID,
    background: BackgroundTasks,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(DELIVERY),
):
    """The driver has the food and is on the way."""
    order = _delivery_order(db, order_id, membership)
    transition(order, OrderStatus.OUT_FOR_DELIVERY.value)
    _record(db, order, membership, OrderEventAction.PICKED_UP)
    background.add_task(order_emails.queue, "order_on_the_way", restaurant.id, order.id)
    return {"order_id": str(order.id), "status": order.status}


@router.post("/orders/{order_id}/delivered")
def delivered(
    order_id: UUID,
    background: BackgroundTasks,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(DELIVERY),
):
    """Handed to the customer at their door, which finishes the order.

    No PIN: there is no counter and no screen to read it from. The driver
    saying so is what completes it, and order_events records who said it.
    """
    order = _delivery_order(db, order_id, membership)
    _require_paid(db, order)
    transition(order, OrderStatus.COMPLETED.value)
    order.completed_at = utcnow()
    _record(db, order, membership, OrderEventAction.DELIVERED)
    background.add_task(order_emails.queue, "order_delivered", restaurant.id, order.id)
    return {"order_id": str(order.id), "status": order.status}


class DriverLocationIn(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    # Degrees clockwise from north, when the phone knows it.
    heading: float | None = Field(default=None, ge=0, le=360)


@router.post(
    "/driver/location",
    # A phone sends one every five seconds or so. Twice that, with room for a
    # flaky connection flushing a few at once.
    dependencies=[Depends(per_staff_user("driver_location", limit=30))],
)
def driver_location(
    body: DriverLocationIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(DELIVERY),
):
    """Where the signed-in driver is right now, for their customers' maps.

    Accepted only while they have an order of their own on the road. A driver
    between deliveries, or a manager who is not carrying anything, is not
    tracked -- the position is refused, not quietly kept. One position covers
    every order they are carrying, since they are in one place.

    Kept for minutes in Redis, never in the database: see services/tracking.
    """
    carrying = db.execute(
        select(func.count()).select_from(Order).where(
            Order.driver_user_id == membership.user_id,
            Order.status == OrderStatus.OUT_FOR_DELIVERY.value,
        )
    ).scalar_one()
    if not carrying:
        raise errors.ApiError(
            409, "NOT_ON_A_DELIVERY", "Your location is only shared while you have an order on the road."
        )
    stored = tracking.record_location(
        restaurant.id, membership.user_id, body.latitude, body.longitude, body.heading
    )
    return {"sharing": stored, "orders": carrying}


# ---------------------------------------------------------------- staff ----

class StaffInviteIn(BaseModel):
    email: str = Field(max_length=320)
    role_code: StaffRole


@router.get("/staff")
def list_staff(
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(STAFF_ADMIN),
):
    rows = db.execute(
        select(RestaurantUser).where(RestaurantUser.revoked_at.is_(None))
    ).scalars().all()

    user_ids = [r.user_id for r in rows]
    emails = {}
    if user_ids:
        with system_session() as sys_db:
            for u in sys_db.execute(select(User).where(User.id.in_(user_ids))).scalars():
                emails[u.id] = (u.email, u.full_name)

    return [
        {
            "id": str(r.id),
            "email": emails.get(r.user_id, ("unknown", None))[0],
            "full_name": emails.get(r.user_id, (None, None))[1],
            "role_code": r.role_code,
            "status": r.status,
            "invited_at": r.invited_at.isoformat() if r.invited_at else None,
            "accepted_at": r.accepted_at.isoformat() if r.accepted_at else None,
            # What became of the last invitation email; null while queued.
            "invitation_email_status": r.invitation_email_status,
            "invitation_email_at": (
                r.invitation_email_at.isoformat() if r.invitation_email_at else None
            ),
            "invitation_email_problem": r.invitation_email_problem,
            # The caller's own row, which the portal does not offer to remove.
            "is_you": r.user_id == membership.user_id,
        }
        for r in rows
    ]


@router.post(
    "/staff",
    status_code=201,
    response_model=StaffInviteOut,
    # Each one sends an email from our domain; a hijacked session must not be
    # able to turn that into a spam cannon or burn the sending reputation.
    dependencies=[Depends(per_staff_user("staff_invite", limit=30, window_seconds=3600))],
)
def invite_staff(
    body: StaffInviteIn,
    background: BackgroundTasks,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(STAFF_ADMIN),
):
    """Invite someone to this restaurant's team.

    Rule 27: this never grants access. The row sits at INVITED until the
    invitee signs in to this restaurant's portal and accepts.

    Staff credentials are issued, not self-registered, so an address with no
    staff login gets one here: a temporary password, returned once for the
    admin to pass on, which must be replaced at first sign-in.

    An address that already has a staff login is not given a password here --
    with one exception. Reissuing an unused temporary password used to hand
    another restaurant's brand-new owner to whichever restaurant typed their
    address first, and a restaurant cannot see that account's memberships
    elsewhere (RLS keeps them from it). Such a person signs in with the
    password they have; a lost one is reset by the super admin.

    The exception is re-inviting someone this restaurant already invited and
    who never signed in: they get a fresh temporary password, because the one
    they were first given is gone with the email that never reached them.
    Only when the login is on no other team -- see
    _reissue_temporary_password_if_safe.

    Customer accounts are a separate population and are never looked at: an
    email that orders lunch here is not thereby a candidate for the kitchen.
    """
    email = body.email.strip().lower()
    if "@" not in email or len(email) < 3:
        raise errors.validation_error("Enter the person's email address.")

    with system_session() as sys_db:
        found = sys_db.execute(
            select(User.id)
            .where(User.email == email, User.kind == UserKind.STAFF.value)
            # A real login before a leftover invite placeholder, if both exist.
            .order_by(User.password_hash.is_(None))
            .limit(1)
        ).scalar_one_or_none()

    # Checked before any account is created, so a refusal leaves nothing behind.
    existing = (
        db.execute(
            select(RestaurantUser).where(RestaurantUser.user_id == found)
        ).scalar_one_or_none()
        if found else None
    )
    if existing and existing.status == StaffStatus.ACTIVE.value:
        raise errors.ApiError(409, "ALREADY_STAFF", "That person is already on the team.")

    # Someone this restaurant already invited, still pending. Checked before
    # the row below is touched: the reissue rule counts committed live
    # memberships, and this one is -- as INVITED -- until this request ends.
    reinviting_pending = bool(existing and existing.status == StaffStatus.INVITED.value)

    temp_password = None
    if found is None:
        temp_password = staff_auth.generate_temp_password()
        with system_session() as sys_db:
            user = User(
                kind=UserKind.STAFF.value,
                email=email,
                password_hash=staff_auth.hash_password(temp_password),
                must_change_password=True,
                is_active=True,
            )
            sys_db.add(user)
            sys_db.flush()
            target_user_id = user.id
    else:
        target_user_id = found
    if existing:
        if reinviting_pending:
            # Invited before and never signed in: whatever password they were
            # first given is lost with the email that should have carried it,
            # so without a new one this re-invitation is a dead end.
            temp_password = _reissue_temporary_password_if_safe(existing.user_id)
        existing.role_code = body.role_code.value
        existing.status = StaffStatus.INVITED.value
        existing.invited_at = utcnow()
        existing.invited_by_user_id = membership.user_id
        existing.revoked_at = None
        existing.accepted_at = None
        _mark_invitation_email_queued(existing)
        db.flush()
        background.add_task(_queue_staff_invitation, restaurant.id, existing.id, temp_password)
        return StaffInviteOut(
            id=existing.id, email=email, status=existing.status,
            temporary_password=temp_password, email_configured=email_service.configured(),
        )

    invite = RestaurantUser(
        restaurant_id=restaurant.id,
        user_id=target_user_id,
        role_code=body.role_code.value,
        status=StaffStatus.INVITED.value,
        invited_by_user_id=membership.user_id,
        invited_at=utcnow(),
    )
    db.add(invite)
    db.flush()
    background.add_task(_queue_staff_invitation, restaurant.id, invite.id, temp_password)
    return StaffInviteOut(
        id=invite.id, email=email, status=invite.status, temporary_password=temp_password,
        email_configured=email_service.configured(),
    )


def _queue_staff_invitation(restaurant_id, membership_id, temporary_password=None) -> None:
    """Hand the invitation email to the worker, after the response is sent --
    by which point the membership row has committed and the task can read it.

    The temporary password, when this invitation issued one, goes with it so
    the email can carry it. It exists in plain text only here: the database
    keeps its argon2 hash, so the worker cannot look it up. It is sealed with
    the field key before it is queued, because the broker persists what it
    holds to disk (redis-broker runs with AOF) and a password there would
    outlive the invitation by as long as the file does.

    Best effort: the invitation itself already exists, and the admin has the
    sign-in details on screen. A broker outage costs the email, not the invite.
    """
    from app.workers.tasks import send_staff_invitation

    sealed = crypto.encrypt_field(temporary_password) if temporary_password else None
    try:
        send_staff_invitation.delay(str(restaurant_id), str(membership_id), sealed)
    except Exception:
        log.warning("could not queue the staff invitation email", exc_info=True)


@router.post("/staff/accept")
def accept_invitation(
    background: BackgroundTasks,
    user: User = Depends(current_staff_user_ready),
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
):
    """The invitee accepts. This is the only path from INVITED to ACTIVE.

    Requires a staff session on this restaurant's address, with any temporary
    password already replaced: accepting is something the person does
    themselves, with a password only they know.
    """
    invite = db.execute(
        select(RestaurantUser).where(
            RestaurantUser.user_id == user.id,
            RestaurantUser.status == StaffStatus.INVITED.value,
        )
    ).scalar_one_or_none()
    if invite is None:
        raise errors.ApiError(404, "NO_PENDING_INVITE", "No pending invitation here.")

    invite.status = StaffStatus.ACTIVE.value
    invite.accepted_at = utcnow()
    # A welcome to them, and word to whoever invited them.
    background.add_task(staff_emails.queue, "staff_welcome", restaurant.id, membership_id=invite.id)
    background.add_task(staff_emails.queue, "staff_joined", restaurant.id, membership_id=invite.id)
    return {"id": str(invite.id), "role_code": invite.role_code, "status": invite.status}


@router.delete("/staff/{membership_id}")
def revoke_staff(
    membership_id: UUID,
    background: BackgroundTasks,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(STAFF_ADMIN),
):
    """Take someone off the team, or withdraw an invitation.

    Two removals are refused, because either can leave a restaurant nobody
    can administer -- and the only way back from that is the platform's
    support, for a restaurant that may be mid-service:

    Removing yourself. It is the removal most likely to be a slip, and there
    is always another way: a second admin removes you.

    Removing the last active admin. With self-removal refused this can only
    be reached by two admins removing each other at the same moment, which
    is exactly the case a count read without a lock gets wrong: each sees the
    other still there and both go through. So every active admin row is
    locked before counting, and the second request waits and then sees the
    first one's removal.
    """
    active_admins = _lock_active_admins(db)
    target = _live_member(db, membership_id)
    if target.user_id == membership.user_id:
        raise errors.ApiError(
            409, "CANNOT_REMOVE_SELF",
            "You can't remove yourself. Ask another admin to do it.",
        )
    if target in active_admins and len(active_admins) == 1:
        raise _last_admin()

    was_on_team = target.status == StaffStatus.ACTIVE.value
    target.status = StaffStatus.REVOKED.value
    target.revoked_at = utcnow()
    # Told only if they had joined. A withdrawn invitation was never theirs.
    if was_on_team:
        background.add_task(
            staff_emails.queue, "staff_removed", restaurant.id,
            membership_id=target.id, at=target.revoked_at.isoformat(),
        )
    return {"id": str(target.id), "status": target.status}


def _lock_active_admins(db: Session) -> list[RestaurantUser]:
    """Every active admin, locked, before anything that could leave none.

    One statement and first, so concurrent removals and demotions queue here:
    the second waits, then reads the first one's change instead of a count
    from before it.
    """
    return list(
        db.execute(
            select(RestaurantUser)
            .where(
                RestaurantUser.role_code == StaffRole.ADMIN.value,
                RestaurantUser.status == StaffStatus.ACTIVE.value,
            )
            .with_for_update()
        ).scalars().all()
    )


def _live_member(db: Session, membership_id: UUID) -> RestaurantUser:
    target = db.get(RestaurantUser, membership_id)
    if target is None or target.status == StaffStatus.REVOKED.value:
        raise errors.validation_error("No such team member.")
    return target


def _last_admin() -> errors.ApiError:
    return errors.ApiError(
        409, "LAST_ADMIN",
        "This is the restaurant's only admin. Make another team member an admin first.",
    )


class StaffRoleIn(BaseModel):
    role_code: StaffRole


@router.patch("/staff/{membership_id}")
def change_staff_role(
    membership_id: UUID,
    body: StaffRoleIn,
    background: BackgroundTasks,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(STAFF_ADMIN),
):
    """Give a team member a different role, or change the role an invitation
    offers.

    Takes effect on their next request: the role is read from this row every
    time. Before this the only way was to remove someone and invite them
    again, which dropped them from the team in between.

    The same two refusals as removal, for the same reason. Your own role is
    not yours to change -- an admin demoting themselves is the likeliest way
    to leave a restaurant with none -- and the last active admin cannot be
    demoted, under the same lock.
    """
    active_admins = _lock_active_admins(db)
    target = _live_member(db, membership_id)
    if target.user_id == membership.user_id:
        raise errors.ApiError(
            409, "CANNOT_CHANGE_OWN_ROLE",
            "You can't change your own role. Ask another admin to do it.",
        )
    new_role = body.role_code.value
    if (
        new_role != StaffRole.ADMIN.value
        and target in active_admins
        and len(active_admins) == 1
    ):
        raise _last_admin()

    old_role, target.role_code = target.role_code, new_role
    if old_role != new_role and target.status == StaffStatus.ACTIVE.value:
        background.add_task(
            staff_emails.queue, "staff_role_changed", restaurant.id,
            membership_id=target.id, old_role=old_role, new_role=new_role,
            at=utcnow().isoformat(),
        )
    return {"id": str(target.id), "role_code": target.role_code, "status": target.status}


def _login_used_elsewhere(user_id) -> bool:
    """Whether this login is on the team of any other restaurant.

    Asked through the system role, which may read exactly two columns of every
    restaurant's memberships for this (migration 0017). The tenant role cannot
    see beyond its own restaurant, which is the point of it.
    """
    with system_session() as sys_db:
        live = sys_db.execute(
            text(
                "SELECT count(*) FROM restaurant_users "
                "WHERE user_id = :u AND status IN ('ACTIVE', 'INVITED')"
            ),
            {"u": str(user_id)},
        ).scalar_one()
    return live > 1


def _reissue_temporary_password_if_safe(user_id) -> str | None:
    """A fresh temporary password for a pending invitee who never signed in,
    or None where issuing one would hand over more than an invitation.

    Only for a login that has never chosen its own password: nothing of
    theirs is taken over, and nothing has yet been done under their name --
    which is why, unlike a password reset, this is not refused for an admin.

    And only for a login on no other restaurant's team. That is the takeover
    the staff invitation once allowed: another restaurant's brand-new owner is
    exactly a login still on its temporary password. Callers must hold this
    restaurant's membership as a committed, live row, which the count below
    includes -- so more than one is somewhere else.
    """
    with system_session() as sys_db:
        user = sys_db.get(User, user_id)
        if user is None or user.kind != UserKind.STAFF.value or not user.must_change_password:
            return None
    if _login_used_elsewhere(user_id):
        return None
    temp_password = staff_auth.generate_temp_password()
    with system_session() as sys_db:
        user = sys_db.get(User, user_id)
        user.password_hash = staff_auth.hash_password(temp_password)
        user.must_change_password = True
        # The old temporary password stops working, and anyone holding a
        # session from it is signed out with it.
        user.sessions_valid_after = utcnow()
    return temp_password


def _mark_invitation_email_queued(member: RestaurantUser) -> None:
    """Clear the last outcome: a new email is on its way, and the team list
    should say "sending", not report the previous attempt as this one."""
    member.invitation_email_status = None
    member.invitation_email_at = None
    member.invitation_email_problem = None


@router.post(
    "/staff/{membership_id}/resend-invite",
    response_model=StaffInviteOut,
    # Each one emails a real inbox; a stuck button should not become a flood.
    dependencies=[Depends(per_staff_user("staff_resend_invite", limit=10, window_seconds=600))],
)
def resend_staff_invitation(
    membership_id: UUID,
    background: BackgroundTasks,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(STAFF_ADMIN),
):
    """Send a pending invitation's email again.

    For the person who never received it -- a spam filter, a mistyped address, an email
    provider that refused it, email that was not set up when they were first
    invited. If they have never signed in, the temporary password they were
    first given went with that email or was shown once and lost, so a new one
    is issued, emailed and shown here; the old one stops working. Someone with
    their own password, or on another restaurant's team, is sent the link
    alone -- see _reissue_temporary_password_if_safe for why.

    The invitation is re-dated, which is what makes this a new email rather
    than a retry the provider would quietly drop as a duplicate.
    """
    target = db.execute(
        select(RestaurantUser).where(
            RestaurantUser.id == membership_id, RestaurantUser.revoked_at.is_(None)
        )
    ).scalar_one_or_none()
    if target is None:
        raise errors.ApiError(404, "NOT_FOUND", "No such invitation.")
    if target.status != StaffStatus.INVITED.value:
        raise errors.ApiError(
            409, "NOT_PENDING",
            "This person has already accepted. There is no invitation to resend.",
        )

    temp_password = _reissue_temporary_password_if_safe(target.user_id)
    target.invited_at = utcnow()
    target.invited_by_user_id = membership.user_id
    _mark_invitation_email_queued(target)
    db.flush()

    with system_session() as sys_db:
        email = sys_db.get(User, target.user_id).email
    log.info("staff invitation resent to %s at %s", email_for_log(email), restaurant.slug)
    background.add_task(_queue_staff_invitation, restaurant.id, target.id, temp_password)
    return StaffInviteOut(
        id=target.id, email=email, status=target.status, temporary_password=temp_password,
        email_configured=email_service.configured(),
    )


@router.post(
    "/staff/{membership_id}/reset-password",
    response_model=StaffPasswordResetOut,
    dependencies=[Depends(per_staff_user("staff_reset_password", limit=20, window_seconds=3600))],
)
def reset_staff_password(
    membership_id: UUID,
    background: BackgroundTasks,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(STAFF_ADMIN),
):
    """Issue a team member a new temporary password, for a forgotten one.

    Before this a restaurant had no way to do it at all: re-inviting leaves an
    existing login alone, and the super admin reset was the only route.

    The new password is shown once, to the admin, to pass on. The old one
    stops working, every session the person holds ends, and they choose their
    own at next sign-in -- the same state a new invitation leaves them in.

    Three refusals, each because the reset would give the admin more than a
    forgotten password is worth:

    Your own. You know it; change it instead, which asks for the current one.

    Another admin's. Admins are otherwise equal, but a reset hands over the
    login, and with it every action recorded under that person's name. Those
    go through Zenoeats support.

    A login that is also on another restaurant's team. Resetting it would let
    this restaurant's admin sign in there as that person -- the takeover the
    staff invitation used to allow. That person's password is reset by
    support, who can see both restaurants.
    """
    target = _live_member(db, membership_id)
    if target.user_id == membership.user_id:
        raise errors.ApiError(
            409, "CANNOT_RESET_OWN_PASSWORD",
            "This is your own account. Use Change password instead.",
        )
    if target.role_code == StaffRole.ADMIN.value:
        raise errors.ApiError(
            409, "ADMIN_PASSWORD_RESET_BY_SUPPORT",
            "An admin's password is reset by Zenoeats support, not from here.",
        )
    if _login_used_elsewhere(target.user_id):
        raise errors.ApiError(
            409, "LOGIN_SHARED_WITH_ANOTHER_RESTAURANT",
            "This person also works at another Zenoeats restaurant, so their "
            "password can only be reset by Zenoeats support.",
        )

    temp_password = staff_auth.generate_temp_password()
    with system_session() as sys_db:
        user = sys_db.get(User, target.user_id)
        if user is None or user.kind != UserKind.STAFF.value:
            raise errors.validation_error("No such team member.")
        user.password_hash = staff_auth.hash_password(temp_password)
        user.must_change_password = True
        # Whoever is signed in as them now -- a lost phone, a shared tablet --
        # is signed out along with the old password.
        user.sessions_valid_after = utcnow()
        email = user.email
    log.info(
        "staff password reset for %s at %s", email_for_log(email), restaurant.slug
    )
    # To them as well as on the admin's screen: a reset they did not ask for
    # is one they need to hear about.
    background.add_task(
        staff_emails.queue, "staff_password_reset", restaurant.id,
        user_id=target.user_id, temporary_password=temp_password, at=utcnow().isoformat(),
    )
    return StaffPasswordResetOut(
        id=target.id, email=email, temporary_password=temp_password,
        email_configured=email_service.configured(),
    )


# --------------------------------------------------------------- reports ---

@router.get("/reports")
def restaurant_reports(
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    _=Depends(MANAGE),
):
    """This restaurant's numbers for a range of its own days.

    It used to answer one question, "everything ever", in a way no restaurant
    asks it: no today, no this week, and every refunded order counted as full
    revenue, tax included. Now:

    Days are the restaurant's, not the server's. `from` and `to` are local
    dates, inclusive, and an order belongs to the day it was paid there -- a
    Chicago order paid at 11.30pm is that day's, though it is tomorrow in UTC.
    Both default to today.

    Deliveries are counted apart from collections, and per driver, because
    they cost a restaurant something a collection does not: someone's time in
    a car. The figures are the same sales, split, not added.

    Refunds come off. Gross is what was taken; refunds are what has since been
    given back on those same orders (from the refund webhook); net is the
    difference. Tax is net of refunds too, in proportion, since a refund
    returns the tax with the food. Top items leave out cancelled and fully
    refunded orders, which sold nothing.

    Tenant-scoped by RLS, so no query here can reach another restaurant's
    orders even without a WHERE clause.
    """
    zone = _zone(restaurant.timezone)
    today = datetime.now(zone).date()
    start_day = from_ or today
    end_day = to or start_day
    if end_day < start_day:
        raise errors.validation_error("The end date is before the start date.")
    if (end_day - start_day).days >= REPORT_MAX_DAYS:
        raise errors.validation_error(
            f"Choose a range of at most {REPORT_MAX_DAYS} days."
        )

    # Local midnights, as instants. zoneinfo gets the daylight-saving days
    # right: a range across the clock change is 23 or 25 hours long, not 24.
    window = {
        "start": datetime.combine(start_day, time(0), tzinfo=zone),
        "end": datetime.combine(end_day + timedelta(days=1), time(0), tzinfo=zone),
        "tz": zone.key,
    }

    # One succeeded payment per order at most -- a partial unique index holds
    # that -- so the join never counts an order twice.
    paid_in_range = """
        FROM orders o
        LEFT JOIN payments p ON p.order_id = o.id AND p.succeeded_at IS NOT NULL
        WHERE o.paid_at >= :start AND o.paid_at < :end
    """

    totals = db.execute(
        text(
            f"""
            SELECT count(*)                                   AS orders_paid,
                   COALESCE(sum(o.total_minor), 0)            AS gross,
                   COALESCE(sum(p.refunded_minor), 0)         AS refunds,
                   COALESCE(sum(o.tax_minor), 0)              AS tax,
                   COALESCE(sum(CASE WHEN o.total_minor > 0
                       THEN round(o.tax_minor::numeric * COALESCE(p.refunded_minor, 0)
                                  / o.total_minor)
                       ELSE 0 END), 0)                        AS tax_refunded,
                   COALESCE(sum(o.discount_minor), 0)         AS discounts,
                   count(*) FILTER (WHERE o.status = 'COMPLETED')  AS completed,
                   count(*) FILTER (WHERE o.status = 'CANCELLED')  AS cancelled,
                   count(*) FILTER (WHERE p.refunded_minor > 0)    AS refunded,
                   count(*) FILTER (WHERE o.fulfillment_type = 'DELIVERY') AS deliveries,
                   count(*) FILTER (WHERE o.fulfillment_type = 'DELIVERY'
                                    AND o.status = 'COMPLETED')            AS delivered,
                   COALESCE(sum(o.total_minor) FILTER (
                       WHERE o.fulfillment_type = 'DELIVERY'), 0)          AS delivery_gross
            {paid_in_range}
            """
        ),
        window,
    ).mappings().one()

    by_day = db.execute(
        text(
            f"""
            SELECT (o.paid_at AT TIME ZONE :tz)::date         AS day,
                   count(*)                                   AS orders,
                   COALESCE(sum(o.total_minor), 0)            AS gross,
                   COALESCE(sum(p.refunded_minor), 0)         AS refunds
            {paid_in_range}
            GROUP BY 1
            ORDER BY 1
            """
        ),
        window,
    ).mappings().all()

    by_driver = db.execute(
        text(
            f"""
            SELECT o.driver_user_id                            AS driver_id,
                   count(*)                                    AS orders,
                   count(*) FILTER (WHERE o.status = 'COMPLETED') AS delivered,
                   COALESCE(sum(o.total_minor), 0)             AS gross
            {paid_in_range}
              AND o.fulfillment_type = 'DELIVERY'
            GROUP BY 1
            ORDER BY 2 DESC
            """
        ),
        window,
    ).mappings().all()

    driver_names = {}
    ids = [row["driver_id"] for row in by_driver if row["driver_id"]]
    if ids:
        with system_session() as sys_db:
            for user in sys_db.execute(select(User).where(User.id.in_(ids))).scalars():
                driver_names[user.id] = user.full_name or user.email

    top_items = db.execute(
        text(
            """
            SELECT oi.name_snapshot AS name,
                   sum(oi.quantity) AS units,
                   sum(oi.line_total_minor) AS revenue
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            LEFT JOIN payments p ON p.order_id = o.id AND p.succeeded_at IS NOT NULL
            WHERE o.paid_at >= :start AND o.paid_at < :end
              AND o.status <> 'CANCELLED'
              AND COALESCE(p.status, '') <> 'REFUNDED'
            GROUP BY oi.name_snapshot
            ORDER BY units DESC, name
            LIMIT 10
            """
        ),
        window,
    ).mappings().all()

    # Expired checkouts were never paid, so they have no paid day: counted by
    # when they were started. Pending is right now, whatever the range.
    unpaid = db.execute(
        text(
            """
            SELECT count(*) FILTER (WHERE status = 'PENDING_PAYMENT') AS pending,
                   count(*) FILTER (WHERE status = 'EXPIRED'
                                    AND created_at >= :start AND created_at < :end) AS expired
            FROM orders
            """
        ),
        window,
    ).mappings().one()

    paid = totals["orders_paid"] or 0
    gross = int(totals["gross"])
    refunds = int(totals["refunds"])
    return {
        "currency": restaurant.currency,
        "timezone": zone.key,
        "today": today.isoformat(),
        "from": start_day.isoformat(),
        "to": end_day.isoformat(),
        "orders_paid": paid,
        "orders_completed": totals["completed"],
        "orders_cancelled": totals["cancelled"],
        "orders_refunded": totals["refunded"],
        "gross_sales_minor": gross,
        "refunds_minor": refunds,
        "net_sales_minor": gross - refunds,
        "tax_collected_minor": int(totals["tax"]) - int(totals["tax_refunded"]),
        "combo_discounts_minor": int(totals["discounts"]),
        "orders_delivery": totals["deliveries"],
        "orders_delivered": totals["delivered"],
        "delivery_sales_minor": int(totals["delivery_gross"]),
        "by_driver": [
            {
                # A driver taken off the team still appears against the orders
                # they ran; the name is what is left of them here.
                "driver": driver_names.get(row["driver_id"], "no driver"),
                "orders": row["orders"],
                "delivered": row["delivered"],
                "gross_minor": int(row["gross"]),
            }
            for row in by_driver
        ],
        "average_order_value_minor": gross // paid if paid else 0,
        "orders_pending_payment": unpaid["pending"],
        "orders_expired": unpaid["expired"],
        "by_day": [
            {
                "date": row["day"].isoformat(),
                "orders": row["orders"],
                "gross_minor": int(row["gross"]),
                "refunds_minor": int(row["refunds"]),
                "net_minor": int(row["gross"]) - int(row["refunds"]),
            }
            for row in by_day
        ],
        "top_items": [
            {"name": r["name"], "units": r["units"], "revenue_minor": int(r["revenue"])}
            for r in top_items
        ],
    }


REPORT_MAX_DAYS = 366


def _zone(name: str | None) -> ZoneInfo:
    """The restaurant's timezone, or UTC if what is stored is not one.

    Timezones are checked when they are set, but a row from before that check
    should still produce a report -- in UTC, and saying so in the response --
    rather than a 500.
    """
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("restaurant timezone %r is not a timezone; reporting in UTC", name)
        return ZoneInfo("UTC")


# ------------------------------------------------------------ storefront ---

@router.get("/storefront")
def get_storefront(restaurant: Restaurant = Depends(current_restaurant_staff), db: Session = StaffDb, _=Depends(STOREFRONT)):
    return storefront.management(db, restaurant)


@router.patch("/storefront/theme")
def patch_storefront_theme(body: ThemePatch, restaurant: Restaurant = Depends(current_restaurant_staff), db: Session = StaffDb, _=Depends(STOREFRONT)):
    return storefront.save_theme(db, restaurant, body)


@router.put("/storefront/banners")
def put_storefront_banners(body: BannersIn, restaurant: Restaurant = Depends(current_restaurant_staff), db: Session = StaffDb, _=Depends(STOREFRONT)):
    return storefront.save_banners(db, restaurant, body)


@router.patch("/item-types/{type_id}/storefront")
def patch_category_storefront(type_id: UUID, body: CategoryPatch, restaurant: Restaurant = Depends(current_restaurant_staff), db: Session = StaffDb, _=Depends(STOREFRONT)):
    return storefront.save_category(db, restaurant, type_id, body)


@router.put("/storefront/collections")
def put_storefront_collections(body: CollectionsIn, restaurant: Restaurant = Depends(current_restaurant_staff), db: Session = StaffDb, _=Depends(STOREFRONT)):
    return storefront.save_collections(db, restaurant, body)


@router.patch("/storefront/map")
def patch_storefront_map(body: MapPatch, restaurant: Restaurant = Depends(current_restaurant_staff), db: Session = StaffDb, _=Depends(STOREFRONT)):
    return storefront.save_map(db, restaurant, body)


@router.put("/storefront/shortcuts")
def put_storefront_shortcuts(body: ShortcutsIn, restaurant: Restaurant = Depends(current_restaurant_staff), db: Session = StaffDb, _=Depends(STOREFRONT)):
    return storefront.save_shortcuts(db, restaurant, body)
