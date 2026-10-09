"""Super Admin portal API.

Every cross-tenant read here goes through the system read surface and is
audited. Section 22.4: the normal request-path tenant role is never loosened
for platform analytics.
"""

import csv
import io
import logging
import uuid
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.api.deps import ensure_platform_admin_user, require_platform_admin
from app.config import settings
from app.core import platform_auth
from app.core import ratelimit
from app.core.ratelimit import per_ip
from app.core import staff_auth
from app.core import errors
from app.core.logsafe import email_for_log
from app.core.tenant import admin_host
from app.db.base import utcnow
from app.db.session import system_session, tenant_session
from app.models import (
    ItemType, Location, LocationStatus, Restaurant, RestaurantOrderCounter,
    RestaurantPaymentAccount, RestaurantStatus, RestaurantUser, StaffRole, StaffStatus, TaxMode, User, UserKind,
    STARTER_ITEM_TYPES,
)
from app.schemas.api import (
    AdminOrderOut, AdminOrderPageOut, CreateOwnerIn, CreateOwnerOut,
    CreateRestaurantIn, RestaurantLoginOut, RestaurantOut, RestaurantReportOut, StripeSyncOut,
    UpdateRestaurantIn,
)
from app.services import images, restaurant_profile, stripe_service, stripe_tax
from app.services import email as email_service

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["super-admin"])


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class AdminOut(BaseModel):
    email: str


@router.post(
    "/login",
    response_model=AdminOut,
    # Unauthenticated and credential-bearing, so it is the one endpoint here
    # worth brute forcing. Tight, and by address since there is no session yet.
    dependencies=[Depends(per_ip("admin_login", limit=10, window_seconds=300))],
)
def login(body: LoginIn, response: Response):
    # Per account as well as per address; see ratelimit.sign_in_blocked.
    if ratelimit.sign_in_blocked("admin_login", body.email):
        log.warning("platform admin sign-in throttled for %s", email_for_log(body.email))
        raise ratelimit.sign_in_throttled()
    admin = platform_auth.authenticate(body.email, body.password)
    if admin is None:
        # One message for both "no such admin" and "wrong password". The
        # difference would tell an attacker which addresses are worth attacking.
        log.warning("failed platform admin sign-in for %s", email_for_log(body.email))
        ratelimit.sign_in_failed("admin_login", body.email)
        raise errors.ApiError(401, "INVALID_CREDENTIALS", "Email or password is incorrect.")

    response.set_cookie(
        key=platform_auth.SESSION_COOKIE,
        value=platform_auth.issue_session(admin),
        max_age=settings.ADMIN_SESSION_TTL_MINUTES * 60,
        # httponly: unreadable to JavaScript, so an XSS bug on any page of the
        # platform cannot exfiltrate a super-admin session.
        httponly=True,
        # lax: the cookie rides top-level navigations but not cross-site form
        # posts, which is what stops a CSRF against the mutating endpoints.
        samesite="lax",
        # Development is plain HTTP on .local; live must never send this in
        # the clear, so it follows the environment rather than being hardcoded.
        secure=settings.ENV == "production",
        path="/",
    )
    return AdminOut(email=admin.email)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response):
    """Sign out -- on the server, and everywhere.

    Deleting the cookie alone left the token valid until it expired, so a
    copy taken before sign-out kept working for hours. Recording the moment
    on the account ends every session this admin holds. Answers 204 either
    way, so signing out twice or with an expired cookie is never an error.
    """
    token = request.cookies.get(platform_auth.SESSION_COOKIE)
    admin = platform_auth.verify_session(token) if token else None
    if admin is not None:
        # The account row is otherwise created on the first authenticated
        # request. Sign in and straight back out, and there is no row to mark
        # -- leaving the token that was just signed out still valid.
        try:
            ensure_platform_admin_user(admin.email)
        except errors.ApiError:
            pass  # an inactive account's sessions are refused regardless
        with system_session() as session:
            session.execute(
                # The app's clock, not the database's: tokens carry the app's
                # time, and comparing across two clocks could refuse a
                # sign-in made straight after this one.
                text(
                    "UPDATE users SET sessions_valid_after = :now "
                    "WHERE email = :email AND kind = 'PLATFORM_ADMIN'"
                ),
                {"now": utcnow(), "email": admin.email},
            )
    response.delete_cookie(
        key=platform_auth.SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.ENV == "production",
    )


@router.get("/me", response_model=AdminOut)
def me(admin: User = Depends(require_platform_admin)):
    """Whether the caller holds a valid admin session. The frontend uses this
    to decide between the dashboard and the login form."""
    return AdminOut(email=admin.email)


# The declared cross-tenant read surface. Kept as one named query so the
# system role's access is reviewable in one place.
_REPORT_SQL = text(
    """
    SELECT r.id                AS restaurant_id,
           r.slug,
           r.name,
           r.status,
           r.currency,
           COALESCE(paid.cnt, 0)            AS orders_paid,
           COALESCE(paid.gross, 0)          AS gross_revenue_minor,
           COALESCE(paid.tax, 0)            AS tax_collected_minor,
           COALESCE(pending.cnt, 0)         AS orders_pending_payment,
           COALESCE(expired.cnt, 0)         AS orders_expired,
           paid.last_order_at
    FROM restaurants r
    LEFT JOIN LATERAL (
        SELECT count(*) AS cnt,
               sum(o.total_minor) AS gross,
               sum(o.tax_minor) AS tax,
               max(o.created_at) AS last_order_at
        FROM orders o
        WHERE o.restaurant_id = r.id
          AND o.paid_at IS NOT NULL
    ) paid ON true
    LEFT JOIN LATERAL (
        SELECT count(*) AS cnt FROM orders o
        WHERE o.restaurant_id = r.id AND o.status = 'PENDING_PAYMENT'
    ) pending ON true
    LEFT JOIN LATERAL (
        SELECT count(*) AS cnt FROM orders o
        WHERE o.restaurant_id = r.id AND o.status = 'EXPIRED'
    ) expired ON true
    WHERE r.deleted_at IS NULL
    ORDER BY r.name
    """
)


def _audit(session, actor: User, action: str, scope: dict):
    session.execute(
        text(
            """
            INSERT INTO platform_audit_logs
                (id, actor_user_id, action, scope, correlation_id, created_at)
            VALUES (gen_random_uuid(), :actor, :action, CAST(:scope AS jsonb),
                    gen_random_uuid()::text, now())
            """
        ),
        {"actor": str(actor.id), "action": action, "scope": __import__("json").dumps(scope)},
    )


@router.get("/restaurants", response_model=list[RestaurantOut])
def list_restaurants(
    include_deleted: bool = False,
    admin: User = Depends(require_platform_admin),
):
    """Deleted restaurants are hidden by default but reachable, because a
    soft delete you cannot see is a soft delete you cannot undo."""
    with system_session() as session:
        _audit(
            session, admin, "SUPER_ADMIN_LIST_RESTAURANTS",
            {"scope": "all", "include_deleted": include_deleted},
        )
        rows = session.execute(
            text(
                """
                SELECT r.id, r.slug, r.name, r.status, r.currency, r.tax_rate_bps,
                       r.storefront_customization_enabled, r.accepting_orders, r.created_at, r.tagline, r.phone, r.timezone,
                       r.deleted_at, r.tax_mode, r.tax_code, r.address_line1,
                       r.address_line2, r.address_city, r.address_state,
                       r.address_postal_code, r.address_country,
                       rpa.stripe_account_id, rpa.charges_enabled
                FROM restaurants r
                LEFT JOIN restaurant_payment_accounts rpa ON rpa.restaurant_id = r.id
                WHERE (:include_deleted OR r.deleted_at IS NULL)
                ORDER BY r.created_at DESC
                """
            ),
            {"include_deleted": include_deleted},
        ).mappings().all()

    return [
        RestaurantOut(
            id=r["id"], slug=r["slug"], name=r["name"], status=r["status"],
            currency=r["currency"], tax_rate_bps=r["tax_rate_bps"],
            accepting_orders=r["accepting_orders"],
            storefront_customization_enabled=r["storefront_customization_enabled"],
            stripe_account_id=r["stripe_account_id"],
            charges_enabled=bool(r["charges_enabled"]),
            created_at=r["created_at"],
            tagline=r["tagline"], phone=r["phone"], timezone=r["timezone"],
            deleted_at=r["deleted_at"],
            **_tax_fields(r),
        )
        for r in rows
    ]


# Shared with the restaurant's own Settings screen, which writes the same row
# and must not be allowed to write it by looser rules.
TAX_AND_ADDRESS_FIELDS = restaurant_profile.TAX_AND_ADDRESS_FIELDS
_tax_fields = restaurant_profile.tax_fields
_stripe_tax_blockers = restaurant_profile.stripe_tax_blockers


def _restaurant_out(session, restaurant: Restaurant) -> RestaurantOut:
    account = session.execute(
        text(
            "SELECT stripe_account_id, charges_enabled FROM restaurant_payment_accounts "
            "WHERE restaurant_id = :rid"
        ),
        {"rid": str(restaurant.id)},
    ).mappings().one_or_none()
    return RestaurantOut(
        id=restaurant.id, slug=restaurant.slug, name=restaurant.name,
        status=restaurant.status, currency=restaurant.currency,
        tax_rate_bps=restaurant.tax_rate_bps,
        accepting_orders=restaurant.accepting_orders,
        storefront_customization_enabled=restaurant.storefront_customization_enabled,
        stripe_account_id=account["stripe_account_id"] if account else None,
        charges_enabled=bool(account["charges_enabled"]) if account else False,
        created_at=restaurant.created_at, tagline=restaurant.tagline, phone=restaurant.phone,
        timezone=restaurant.timezone, deleted_at=restaurant.deleted_at,
        **_tax_fields(restaurant),
    )


@router.patch("/restaurants/{restaurant_id}", response_model=RestaurantOut)
def update_restaurant(
    restaurant_id: UUID,
    body: UpdateRestaurantIn,
    admin: User = Depends(require_platform_admin),
):
    """Edit a restaurant profile.

    exclude_unset is what makes this a real PATCH: only fields the caller
    actually sent are applied, so clearing the tagline with null stays
    distinguishable from omitting it, and two admins editing different fields
    do not silently overwrite one another.
    """
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise errors.validation_error("No fields to update.")

    changes = restaurant_profile.normalize(changes)

    # Stripe Tax is checked before anything is written, and outside the
    # transaction, because the check asks Stripe. It runs whenever the result
    # would be a Stripe Tax restaurant and a tax or address field changed --
    # clearing the street of a live Stripe Tax restaurant is refused just like
    # switching one on without it.
    with system_session() as session:
        current = session.get(Restaurant, restaurant_id)
        if current is None or current.deleted_at is not None:
            raise errors.ApiError(404, "RESTAURANT_NOT_FOUND", "No such restaurant.")
        # Read inside the session: the check runs after it closes, and a
        # detached instance would raise rather than answer.
        current_tax = restaurant_profile.tax_fields(current)
        account_id = session.execute(
            text("SELECT stripe_account_id FROM restaurant_payment_accounts WHERE restaurant_id = :rid"),
            {"rid": str(restaurant_id)},
        ).scalar_one_or_none()

    restaurant_profile.guard_stripe_tax(current_tax, changes, account_id)

    with system_session() as session:
        restaurant = session.get(Restaurant, restaurant_id)
        if restaurant is None or restaurant.deleted_at is not None:
            raise errors.ApiError(404, "RESTAURANT_NOT_FOUND", "No such restaurant.")

        restaurant_profile.apply_changes(restaurant, changes)

        _audit(
            session, admin, "SUPER_ADMIN_UPDATE_RESTAURANT",
            {"restaurant_id": str(restaurant_id), "fields": sorted(changes)},
        )
        session.flush()
        return _restaurant_out(session, restaurant)


@router.delete("/restaurants/{restaurant_id}", response_model=dict)
def delete_restaurant(restaurant_id: UUID, admin: User = Depends(require_platform_admin)):
    """Soft delete. The row stays and deleted_at is set; every query already
    filters on it.

    Never a hard delete here. Orders, payments and audit rows hang off this
    record and must survive for financial and tax retention. zenoeats_system
    is granted SELECT, INSERT and UPDATE on restaurants but deliberately not
    DELETE, so the database would refuse one from this session anyway.

    A restaurant that never traded can be removed for good afterwards, by the
    purge endpoint below. That one refuses anything holding an order or a
    payment, so the retention promise above is kept where it applies.

    An ACTIVE restaurant has to be suspended first: removing one out from
    under customers mid-checkout should not be a single click.

    The slug stays occupied afterwards, on purpose. It is printed on tables
    and saved in bookmarks, so handing that address to a different business
    would silently inherit an existing customer base.
    """
    with system_session() as session:
        restaurant = session.get(Restaurant, restaurant_id)
        if restaurant is None:
            raise errors.ApiError(404, "RESTAURANT_NOT_FOUND", "No such restaurant.")
        if restaurant.deleted_at is not None:
            raise errors.ApiError(409, "ALREADY_DELETED", "This restaurant is already deleted.")
        if restaurant.status == RestaurantStatus.ACTIVE.value:
            raise errors.ApiError(
                409, "RESTAURANT_ACTIVE", "Suspend this restaurant before deleting it."
            )

        restaurant.deleted_at = utcnow()
        _audit(
            session, admin, "SUPER_ADMIN_DELETE_RESTAURANT",
            {"restaurant_id": str(restaurant_id), "slug": restaurant.slug},
        )

    return {"restaurant_id": str(restaurant_id), "deleted": True}


# Every tenant-owned table, deepest first. A purge walks this in order, so a
# row is always gone before the row it points at.
#
# Written out rather than discovered from the metadata, because the order is
# the whole correctness argument and a loop over table names would hide it.
# A new tenant table missing from this list leaves orphans behind, which is
# why the test beside it checks the list against the schema.
# Tables carrying restaurant_id that a purge DETACHES rather than deletes.
#
# `locations` is platform data that points at a tenant: the restaurant is what
# a location opens, not what owns it. Deleting the row with the tenant would
# take a city off the front page as a side effect of removing one restaurant,
# so the purge nulls the pointer and puts the location back to COMING_SOON.
#
# The gate beside _PURGE_ORDER accepts either treatment -- what it refuses is a
# table that is in neither list, because that is the one that leaves orphans.
_PURGE_DETACH = ["locations"]

_PURGE_ORDER = [
    "storefront_collection_items",
    "storefront_banners",
    "storefront_collections",
    # Before item_types and menu_items, which a shortcut and its items name.
    "storefront_shortcut_items",
    "storefront_shortcuts",
    "order_item_modifiers",
    "order_items",
    # Before orders: an event names the order it happened to.
    "order_events",
    "sent_emails",
    # Before orders: a payment points at the order it paid for.
    "payments",
    "orders",
    "combo_slot_items",
    "combo_slots",
    "combos",
    "item_included_options",
    "item_modifier_groups",
    "modifier_group_item_types",
    "modifier_options",
    "modifier_groups",
    "meal_items",
    # Before menu_items: a favourite points at the item it saved.
    "customer_favourites",
    "menu_items",
    "item_types",
    "meals",
    "delivery_zones",
    "restaurant_users",
    "restaurant_payment_accounts",
    "restaurant_order_counters",
]


@router.delete("/restaurants/{restaurant_id}/permanent", response_model=dict)
def purge_restaurant(restaurant_id: UUID, admin: User = Depends(require_platform_admin)):
    """Remove a restaurant and everything it owns, for good.

    The ordinary delete is a soft one and stays that way: a restaurant that
    has traded has orders, payments and tax history hanging off it, and those
    have to survive. This is for the other case -- a draft nobody ever used, a
    duplicate created by a typo, a throwaway left behind by a test run -- where
    the row is clutter and there is nothing to protect.

    Two guards decide which case this is, and neither is a warning the caller
    can click past:

    It has to be soft-deleted already. Purging is the second half of a
    decision, not a shortcut past it, and a live restaurant cannot be reached
    from here at all.

    It has to have no orders and no payments. That is what "nothing to
    protect" means in a system that keeps financial records, and the count
    comes back in the refusal so the answer is actionable rather than a flat
    no. There is no override: a restaurant that has taken money is not
    purgeable through this API by anyone.

    Audit rows are deliberately left behind. They record what an administrator
    did, not what the restaurant was, and a purge that erased its own trace
    would be the one action on this API nobody could review.

    Runs as zenoeats_app rather than zenoeats_system, because the system role
    is granted SELECT, INSERT and UPDATE on restaurants and deliberately not
    DELETE. Row-level security scopes every statement below to this tenant, so
    a wrong id deletes nothing rather than something else.
    """
    with system_session() as session:
        restaurant = session.get(Restaurant, restaurant_id)
        if restaurant is None:
            raise errors.ApiError(404, "RESTAURANT_NOT_FOUND", "No such restaurant.")
        if restaurant.deleted_at is None:
            raise errors.ApiError(
                409,
                "NOT_DELETED",
                "Delete this restaurant first. Permanent removal is the second step.",
            )
        slug = restaurant.slug
        name = restaurant.name

    with tenant_session(restaurant_id) as session:
        orders = session.execute(
            text("SELECT count(*) FROM orders WHERE restaurant_id = :r"),
            {"r": restaurant_id},
        ).scalar_one()
        payments = session.execute(
            text("SELECT count(*) FROM payments WHERE restaurant_id = :r"),
            {"r": restaurant_id},
        ).scalar_one()

        if orders or payments:
            raise errors.ApiError(
                409,
                "RESTAURANT_HAS_HISTORY",
                f"{name} has {orders} order(s) and {payments} payment(s). "
                "A restaurant that has taken money cannot be removed permanently.",
            )

        # Locations are platform rows that point at a tenant, not tenant rows
        # the tenant owns, so a purge detaches them instead of deleting them:
        # the place still exists and stays on the front page, we have simply
        # stopped serving it, and whoever opens the admin portal next can see
        # that rather than find a city quietly missing.
        #
        # As the system role, because the app role may only read this table.
        # Before the deletes, because the foreign key is RESTRICT and would
        # otherwise refuse the restaurant row at the end of this block.
        with system_session() as platform:
            platform.execute(
                text(
                    "UPDATE locations SET restaurant_id = NULL, status = 'COMING_SOON' "
                    "WHERE restaurant_id = :r"
                ),
                {"r": restaurant_id},
            )

        removed = {}
        for table in _PURGE_ORDER:
            result = session.execute(
                text(f"DELETE FROM {table} WHERE restaurant_id = :r"), {"r": restaurant_id}
            )
            if result.rowcount:
                removed[table] = result.rowcount

        gone = session.execute(
            text("DELETE FROM restaurants WHERE id = :r"), {"r": restaurant_id}
        ).rowcount
        if not gone:
            # RLS returning zero rows here would mean the tenant context and
            # the id disagree, which is a bug rather than a missing row.
            raise errors.ApiError(
                500, "INTERNAL_ERROR", "The restaurant row could not be removed."
            )

    # The rows are gone and committed, so nothing can show these any more.
    # After the transaction rather than inside it: a purge that failed and
    # rolled back must not have already deleted the menu's pictures.
    #
    # Fails soft. The restaurant is removed either way, and a folder left
    # behind is disk to reclaim by hand, not a reason to report the purge as
    # failed when everything it promised has happened.
    try:
        images.storage().delete_restaurant(restaurant_id)
    except (OSError, ValueError):
        log.warning("purged %s but could not remove its images", slug, exc_info=True)

    with system_session() as session:
        _audit(
            session, admin, "SUPER_ADMIN_PURGE_RESTAURANT",
            {"restaurant_id": str(restaurant_id), "slug": slug, "removed": removed},
        )

    return {"restaurant_id": str(restaurant_id), "purged": True, "removed": removed}


@router.post("/restaurants/{restaurant_id}/restore", response_model=dict)
def restore_restaurant(restaurant_id: UUID, admin: User = Depends(require_platform_admin)):
    """Undo a soft delete. Comes back SUSPENDED rather than ACTIVE, so
    reactivating still has to pass the readiness gate."""
    with system_session() as session:
        restaurant = session.get(Restaurant, restaurant_id)
        if restaurant is None:
            raise errors.ApiError(404, "RESTAURANT_NOT_FOUND", "No such restaurant.")
        if restaurant.deleted_at is None:
            raise errors.ApiError(409, "NOT_DELETED", "This restaurant is not deleted.")

        restaurant.deleted_at = None
        restaurant.status = RestaurantStatus.SUSPENDED.value
        _audit(
            session, admin, "SUPER_ADMIN_RESTORE_RESTAURANT",
            {"restaurant_id": str(restaurant_id), "slug": restaurant.slug},
        )

    return {"restaurant_id": str(restaurant_id), "status": RestaurantStatus.SUSPENDED.value}


@router.post("/restaurants", response_model=RestaurantOut, status_code=201)
def create_restaurant(body: CreateRestaurantIn, admin: User = Depends(require_platform_admin)):
    """Create a tenant in DRAFT. Wildcard DNS already routes the subdomain;
    the portal only becomes orderable after activation."""
    from app.core.tenant import RESERVED_SLUGS

    if body.slug in RESERVED_SLUGS:
        raise errors.validation_error("That subdomain is reserved.")

    # A friendly answer for the ordinary case. The unique index on slug is
    # still what decides a race between two admins, below.
    with system_session() as session:
        exists = session.execute(
            text("SELECT 1 FROM restaurants WHERE slug = :slug"), {"slug": body.slug}
        ).first()
        if exists:
            raise errors.ApiError(409, "SLUG_TAKEN", "That subdomain is already in use.")

    # Everything the new restaurant owns is written in ONE transaction, as the
    # tenant role scoped to the new id -- so it is created whole or not at all.
    #
    # Not the system role. Item types are tenant menu data, and
    # zenoeats_system is deliberately barred from menu tables (the RLS gate
    # test asserts it). Writing them through it failed with "permission denied
    # for table item_types" and rolled the whole creation back. The tenant
    # role's policies already allow exactly this: a row whose restaurant is
    # the current tenant, including the restaurant row itself.
    rid = uuid.uuid4()
    try:
        with tenant_session(rid) as session:
            session.add(
                Restaurant(
                    id=rid, slug=body.slug, name=body.name, timezone=body.timezone,
                    currency=body.currency, tax_rate_bps=body.tax_rate_bps,
                    tagline=body.tagline, status=RestaurantStatus.DRAFT.value,
                )
            )
            session.flush()
            session.add(
                RestaurantOrderCounter(
                    restaurant_id=rid, next_order_number=1001, updated_at=utcnow()
                )
            )
            # Item types are the restaurant's own, but it has to start with
            # some: an item cannot be created without one, so a restaurant with
            # an empty list would have a menu builder that refuses every first
            # step. These four are a starting vocabulary, not a rule -- rename,
            # reorder, add to or delete them from the portal like anything else.
            for order, name in enumerate(STARTER_ITEM_TYPES):
                session.add(ItemType(restaurant_id=rid, name=name, sort_order=order))
    except IntegrityError as exc:
        # Another admin took the slug between the check above and this insert.
        raise errors.ApiError(409, "SLUG_TAKEN", "That subdomain is already in use.") from exc

    with system_session() as session:
        _audit(session, admin, "SUPER_ADMIN_CREATE_RESTAURANT",
               {"restaurant_id": str(rid), "slug": body.slug})

    return RestaurantOut(
        id=rid, slug=body.slug, name=body.name, status=RestaurantStatus.DRAFT.value,
        currency=body.currency, tax_rate_bps=body.tax_rate_bps, accepting_orders=True,
        stripe_account_id=None, charges_enabled=False, created_at=utcnow(),
    )


@router.post(
    "/restaurants/{restaurant_id}/owner",
    response_model=CreateOwnerOut,
    status_code=201,
)
def create_restaurant_owner(
    restaurant_id: UUID,
    body: CreateOwnerIn,
    background: BackgroundTasks,
    admin: User = Depends(require_platform_admin),
):
    """Give a restaurant its owner: a staff account with the ADMIN role.

    The platform sets up the owner; the owner invites their own staff from the
    restaurant portal. Three cases, by what the address already has:

      no staff login        A login is created with a temporary password, and
                            the owner is ACTIVE here straight away -- the
                            account did not exist, so nobody else's access is
                            being extended.

      a login never used    Its password is still a temporary one nobody
                            chose (a restaurant purged before its owner signed
                            in, say). A fresh temporary password is issued and
                            the owner is ACTIVE, exactly as for a new login.

      a login in real use   Someone who already runs another restaurant.
                            Their account is left alone -- no password is
                            issued or changed -- and they are INVITED as owner
                            here. Rule 27: an email match never grants access
                            on its own; they sign in with the password they
                            have and accept. They are emailed the invitation.

    This used to refuse every address that already had a login, and the
    password reset only works for staff already at the restaurant, so a
    person who owned two restaurants could not be set up at all.

    A temporary password is returned once, in this response, and never again:
    only its argon2 hash is stored.
    """
    email = body.email.strip().lower()

    with system_session() as session:
        restaurant = session.get(Restaurant, restaurant_id)
        if restaurant is None or restaurant.deleted_at is not None:
            raise errors.ApiError(404, "RESTAURANT_NOT_FOUND", "No such restaurant.")
        slug = restaurant.slug

        user = session.execute(
            select(User)
            .where(User.email == email, User.kind == UserKind.STAFF.value)
            # A real login before a leftover invite placeholder, if both exist.
            .order_by(User.password_hash.is_(None))
            .limit(1)
        ).scalar_one_or_none()
        user_id = user.id if user else None
        in_use = bool(user and user.password_hash and not user.must_change_password)

    # Membership is tenant-owned, so it is read and written under the tenant
    # role with RLS in force. Checked before any password is touched, so a
    # refusal changes nothing.
    with tenant_session(restaurant_id) as session:
        membership = (
            session.execute(
                select(RestaurantUser).where(RestaurantUser.user_id == user_id)
            ).scalar_one_or_none()
            if user_id else None
        )
        if membership is not None and membership.status == StaffStatus.ACTIVE.value:
            raise errors.ApiError(
                409, "ALREADY_STAFF", "That person is already on this restaurant's team."
            )

    temp_password = None
    with system_session() as session:
        if user_id is None:
            temp_password = staff_auth.generate_temp_password()
            owner = User(
                kind=UserKind.STAFF.value,
                email=email,
                full_name=body.full_name,
                password_hash=staff_auth.hash_password(temp_password),
                must_change_password=True,
            )
            session.add(owner)
            session.flush()
            user_id = owner.id
        elif not in_use:
            temp_password = staff_auth.generate_temp_password()
            owner = session.get(User, user_id)
            owner.password_hash = staff_auth.hash_password(temp_password)
            owner.must_change_password = True
            if body.full_name and not owner.full_name:
                owner.full_name = body.full_name

        status = StaffStatus.INVITED.value if in_use else StaffStatus.ACTIVE.value
        _audit(
            session, admin, "SUPER_ADMIN_CREATE_RESTAURANT_OWNER",
            {"restaurant_id": str(restaurant_id), "slug": slug, "owner_email": email,
             "status": status},
        )

    now = utcnow()
    with tenant_session(restaurant_id) as session:
        membership = session.execute(
            select(RestaurantUser).where(RestaurantUser.user_id == user_id)
        ).scalar_one_or_none()
        if membership is None:
            membership = RestaurantUser(restaurant_id=restaurant_id, user_id=user_id)
            session.add(membership)
        membership.role_code = StaffRole.ADMIN.value
        membership.status = status
        membership.invited_by_user_id = admin.id
        membership.invited_at = now
        membership.accepted_at = now if status == StaffStatus.ACTIVE.value else None
        membership.revoked_at = None
        session.flush()
        membership_id = membership.id

    if status == StaffStatus.INVITED.value:
        background.add_task(_queue_owner_invitation, restaurant_id, membership_id)

    return CreateOwnerOut(
        user_id=user_id, email=email, temporary_password=temp_password, status=status,
        email_configured=email_service.configured(),
    )


def _queue_owner_invitation(restaurant_id, membership_id) -> None:
    """Email an existing login that it has been invited as owner. After the
    response, so the membership has committed; best effort, like staff
    invitations -- the invitation stands without the email."""
    from app.workers.tasks import send_staff_invitation

    try:
        send_staff_invitation.delay(str(restaurant_id), str(membership_id))
    except Exception:
        log.warning("could not queue the owner invitation email", exc_info=True)


@router.post("/restaurants/{restaurant_id}/owner/reset-password", response_model=CreateOwnerOut)
def reset_owner_password(
    restaurant_id: UUID,
    body: CreateOwnerIn,
    background: BackgroundTasks,
    admin: User = Depends(require_platform_admin),
):
    """Reissue a temporary password for an existing staff account.

    Stands in for a self-service reset until there is an email provider to
    send a link through. The account must already be staff at this restaurant,
    so this cannot be used to take over an arbitrary address.
    """
    email = body.email.strip().lower()

    with system_session() as session:
        restaurant = session.get(Restaurant, restaurant_id)
        if restaurant is None or restaurant.deleted_at is not None:
            raise errors.ApiError(404, "RESTAURANT_NOT_FOUND", "No such restaurant.")
        # Any staff account, including a placeholder with no password yet: a
        # restaurant's invite never issues one to an existing account, so this
        # is the only way such a person is ever given a login.
        user = session.execute(
            select(User)
            .where(User.email == email, User.kind == UserKind.STAFF.value)
            .order_by(User.password_hash.is_(None))
            .limit(1)
        ).scalar_one_or_none()
        if user is None:
            raise errors.ApiError(404, "USER_NOT_FOUND", "No login for that email.")
        user_id = user.id

    with tenant_session(restaurant_id) as session:
        membership = session.execute(
            select(RestaurantUser).where(RestaurantUser.user_id == user_id)
        ).scalar_one_or_none()
        if membership is None:
            raise errors.ApiError(
                404, "USER_NOT_FOUND", "That login is not staff at this restaurant."
            )
        membership_status = membership.status

    temp_password = staff_auth.generate_temp_password()
    with system_session() as session:
        user = session.get(User, user_id)
        user.password_hash = staff_auth.hash_password(temp_password)
        user.must_change_password = True
        # A reset usually means a lost password or a lost device. Whoever
        # holds a session from before it is signed out.
        user.sessions_valid_after = utcnow()
        _audit(
            session, admin, "SUPER_ADMIN_RESET_STAFF_PASSWORD",
            {"restaurant_id": str(restaurant_id), "user_email": email},
        )

    from app.services import staff_emails

    background.add_task(
        staff_emails.queue, "staff_password_reset", restaurant_id,
        user_id=user_id, temporary_password=temp_password, at=utcnow().isoformat(),
    )
    return CreateOwnerOut(
        user_id=user_id, email=email, temporary_password=temp_password,
        status=membership_status, email_configured=email_service.configured(),
    )


@router.get("/restaurants/{restaurant_id}/logins", response_model=list[RestaurantLoginOut])
def list_restaurant_logins(restaurant_id: UUID, admin: User = Depends(require_platform_admin)):
    """Every staff login on a restaurant: who has one, in what role, and
    whether they have signed in yet.

    Without it the only trace of an owner created here was the one-time
    password panel, so an admin who closed it could not tell whether a login
    existed -- until "Create login" refused with ALREADY_STAFF. Owners first,
    then by when they were invited. Revoked logins are kept in the list, marked
    so: they still exist, and the restaurant can re-invite them.
    """
    with tenant_session(restaurant_id) as session:
        if session.get(Restaurant, restaurant_id) is None:
            raise errors.ApiError(404, "RESTAURANT_NOT_FOUND", "No such restaurant.")
        memberships = session.execute(
            select(RestaurantUser).order_by(RestaurantUser.invited_at, RestaurantUser.created_at)
        ).scalars().all()
        members = [
            (m.user_id, m.role_code, m.status, m.invited_at or m.created_at) for m in memberships
        ]

    with system_session() as session:
        _audit(session, admin, "SUPER_ADMIN_LIST_RESTAURANT_LOGINS",
               {"restaurant_id": str(restaurant_id)})
        users = {
            u.id: u for u in session.execute(
                select(User).where(User.id.in_([m[0] for m in members]))
            ).scalars().all()
        } if members else {}

        out = [
            RestaurantLoginOut(
                user_id=user_id, email=users[user_id].email,
                full_name=users[user_id].full_name, role=role, status=status,
                signed_in=bool(users[user_id].password_hash)
                and not users[user_id].must_change_password,
                invited_at=invited_at,
            )
            for user_id, role, status, invited_at in members
            if user_id in users
        ]
    out.sort(key=lambda login: login.role != StaffRole.ADMIN.value)
    return out


def _restaurant_owner_email(restaurant_id: UUID) -> str | None:
    """The address of the restaurant's owner, or None if it has no owner yet.

    The earliest ADMIN membership, which is the account the super admin issued
    when the restaurant was set up. An invitation that has not been accepted
    still counts: the person exists and the address is theirs, and waiting for
    them to click accept would only delay onboarding.
    """
    with tenant_session(restaurant_id) as session:
        user_id = session.execute(
            select(RestaurantUser.user_id)
            .where(
                RestaurantUser.restaurant_id == restaurant_id,
                RestaurantUser.role_code == StaffRole.ADMIN.value,
                RestaurantUser.status.in_(
                    [StaffStatus.ACTIVE.value, StaffStatus.INVITED.value]
                ),
            )
            .order_by(RestaurantUser.created_at)
            .limit(1)
        ).scalar_one_or_none()

    if user_id is None:
        return None
    with system_session() as session:
        return session.execute(
            select(User.email).where(User.id == user_id)
        ).scalar_one_or_none()


def _admin_portal_url(request: Request) -> str:
    """The portal's restaurants page, as an absolute URL.

    Built from the hostname the request arrived on, which require_admin_host
    has already pinned to admin.<root domain>, so this cannot be pointed
    anywhere else. The scheme is forced to https in production rather than
    read from the request, which reaches the app over plain HTTP behind the
    proxy that terminated TLS.
    """
    host = request.headers.get("host", admin_host())
    scheme = "https" if settings.ENV == "production" else request.url.scheme
    return f"{scheme}://{host}/admin"


@router.post("/restaurants/{restaurant_id}/stripe-onboarding")
def start_stripe_onboarding(
    request: Request,
    restaurant_id: UUID,
    admin: User = Depends(require_platform_admin),
):
    """Create or reuse the connected account and return a hosted onboarding
    link. Zenoeats never touches KYC data.

    Where Stripe sends the operator back is decided here, not by the caller.
    Both URLs used to arrive as query parameters, so anyone who could make
    this call could have Stripe's own onboarding flow hand the operator on to
    an address of their choosing -- a redirect carrying Stripe's credibility,
    landing on a page that looks like the portal and asks to sign in again.
    There was never a second destination to choose: the portal has one page
    for this, and the server knows its address.
    """
    portal_url = _admin_portal_url(request)
    with tenant_session(restaurant_id) as session:
        restaurant = session.get(Restaurant, restaurant_id)
        if restaurant is None:
            raise errors.ApiError(404, "RESTAURANT_NOT_FOUND", "No such restaurant.")
        account = session.execute(select(RestaurantPaymentAccount)).scalar_one_or_none()
        existing_account_id = account.stripe_account_id if account else None
        restaurant_name = restaurant.name

    if existing_account_id is None:
        # The account's contact is the restaurant's owner, because everything
        # Stripe sends there is the restaurant's business: identity documents
        # to upload, a payout that failed, a dispute with a deadline. This was
        # the platform admin's address, which quietly made us the middleman
        # for every one of those -- on an account we are deliberately not the
        # merchant of record for.
        owner_email = _restaurant_owner_email(restaurant_id)
        if owner_email is None:
            raise errors.ApiError(
                409,
                "OWNER_REQUIRED",
                "Create the owner account first. Stripe sends verification and payout "
                "notices to this address, and they have to reach the restaurant.",
            )
        account_id = stripe_service.create_connected_account(
            email=owner_email,
            display_name=restaurant_name,
        )
        with tenant_session(restaurant_id) as session:
            session.add(
                RestaurantPaymentAccount(
                    restaurant_id=restaurant_id,
                    stripe_account_id=account_id,
                    onboarding_status="PENDING",
                )
            )
        existing_account_id = account_id

    # Both are the portal's own page. Stripe sends the operator to return_url
    # when the form is finished and to refresh_url when the link has expired
    # or was abandoned; the portal re-reads the account from Stripe on load,
    # so the same page answers both.
    url = stripe_service.create_account_link(existing_account_id, portal_url, portal_url)
    return {"onboarding_url": url, "stripe_account_id": existing_account_id}


@router.post("/restaurants/{restaurant_id}/stripe-refresh", response_model=StripeSyncOut)
def refresh_stripe_status(
    restaurant_id: UUID,
    admin: User = Depends(require_platform_admin),
):
    """Re-read the connected account from Stripe and store what it says.

    charges_enabled was only ever written by the account.updated webhook. That
    leaves the portal wrong whenever the webhook did not arrive -- no endpoint
    configured, a placeholder signing secret, or no worker running -- and the
    restaurant then cannot be activated even though Stripe is happy. It is
    also what Stripe's hosted onboarding guidance prescribes, because the
    return_url carries no state of its own.

    Stripe is the authority here; this only copies its answer. The reply
    includes why charges are disabled so the operator has something to act on
    rather than just "incomplete".
    """
    with tenant_session(restaurant_id) as session:
        restaurant = session.get(Restaurant, restaurant_id)
        if restaurant is None:
            raise errors.ApiError(404, "RESTAURANT_NOT_FOUND", "No such restaurant.")
        account = session.execute(select(RestaurantPaymentAccount)).scalar_one_or_none()
        if account is None:
            raise errors.ApiError(
                409, "NO_STRIPE_ACCOUNT",
                "This restaurant has no connected account yet. Start onboarding first.",
            )
        account_id = account.stripe_account_id
        slug = restaurant.slug

    # Outside the transaction: a Stripe round trip must never hold a database
    # lock open.
    status = stripe_service.retrieve_account_status(account_id)
    # Also how a restaurant activated before wallets were registered gets
    # them: one press of "Refresh Stripe". Only once it can take charges.
    wallets = (stripe_service.ensure_wallet_domain(account_id, slug)
               if status["charges_enabled"] else None)

    with tenant_session(restaurant_id) as session:
        account = session.execute(select(RestaurantPaymentAccount)).scalar_one()
        changed = (
            account.charges_enabled != status["charges_enabled"]
            or account.payouts_enabled != status["payouts_enabled"]
            or account.details_submitted != status["details_submitted"]
        )
        account.charges_enabled = status["charges_enabled"]
        account.payouts_enabled = status["payouts_enabled"]
        account.details_submitted = status["details_submitted"]
        account.onboarding_status = "COMPLETE" if status["details_submitted"] else "PENDING"
        onboarding_status = account.onboarding_status

    with system_session() as session:
        _audit(
            session, admin, "SUPER_ADMIN_REFRESH_STRIPE_STATUS",
            {
                "restaurant_id": str(restaurant_id),
                "stripe_account_id": account_id,
                "charges_enabled": status["charges_enabled"],
                "changed": changed,
            },
        )

    return StripeSyncOut(
        stripe_account_id=account_id,
        charges_enabled=status["charges_enabled"],
        payouts_enabled=status["payouts_enabled"],
        details_submitted=status["details_submitted"],
        onboarding_status=onboarding_status,
        disabled_reason=status["disabled_reason"],
        currently_due=status["currently_due"],
        past_due=status["past_due"],
        changed=changed,
        wallet_domain=wallets,
    )


@router.post("/restaurants/{restaurant_id}/activate", response_model=dict)
def activate_restaurant(restaurant_id: UUID, admin: User = Depends(require_platform_admin)):
    """Put a restaurant live and list it on the platform root.

    Neither a phone number nor a Stripe account is required. A restaurant is
    set up in this order: the owner builds the menu, the restaurant is put
    live to be seen and tested, and Stripe is connected afterwards. Until it
    is, the storefront and menu are public and checkout refuses payment with
    "not set up for card payments" (orders.py) -- nothing is charged, and the
    unpaid order expires like any abandoned checkout.

    Stripe Tax is the one check kept, and only once an account exists: a
    restaurant that asked Stripe to calculate its tax would otherwise charge
    customers none.
    """
    with tenant_session(restaurant_id) as session:
        restaurant = session.get(Restaurant, restaurant_id)
        if restaurant is None:
            raise errors.ApiError(404, "RESTAURANT_NOT_FOUND", "No such restaurant.")

        account = session.execute(select(RestaurantPaymentAccount)).scalar_one_or_none()
        uses_stripe_tax = restaurant.tax_mode == TaxMode.STRIPE_TAX.value
        tax_view = dict(_tax_fields(restaurant))
        account_id = account.stripe_account_id if account else None
        charges_enabled = bool(account and account.charges_enabled)
        slug = restaurant.slug

    if uses_stripe_tax and account_id:
        from types import SimpleNamespace

        blockers = _stripe_tax_blockers(SimpleNamespace(**tax_view), account_id)
        if blockers:
            raise errors.ApiError(409, "NOT_READY_FOR_ACTIVATION", " ".join(blockers))

    with tenant_session(restaurant_id) as session:
        restaurant = session.get(Restaurant, restaurant_id)
        if restaurant is None:
            raise errors.ApiError(404, "RESTAURANT_NOT_FOUND", "No such restaurant.")
        restaurant.status = RestaurantStatus.ACTIVE.value
        listing = {
            "slug": restaurant.slug, "name": restaurant.name,
            "city": restaurant.address_city or "", "region": restaurant.address_state,
            "address_line": restaurant.address_line1, "blurb": restaurant.tagline,
        }

    # Wallet registration belongs to the connected account, so there is
    # nothing to register until Stripe is connected.
    wallets = (
        stripe_service.ensure_wallet_domain(account_id, slug) if charges_enabled else None
    )

    with system_session() as session:
        location = _list_on_platform_root(session, restaurant_id, listing)
        _audit(session, admin, "SUPER_ADMIN_ACTIVATE_RESTAURANT",
               {"restaurant_id": str(restaurant_id), "wallet_domain": wallets,
                "location": location})

    return {"restaurant_id": str(restaurant_id), "status": RestaurantStatus.ACTIVE.value,
            "wallet_domain": wallets, "location": location}


def _list_on_platform_root(session, restaurant_id: UUID, listing: dict) -> str:
    """Make sure the platform root shows this restaurant, and say what was done.

    The root lists locations, not restaurants, so an activated restaurant with
    no location is live but invisible from zenoeats.com. In order:

      already linked   Opened if it was COMING_SOON. A HIDDEN one stays hidden:
                       somebody pulled it from the front page on purpose.
      unlinked, same   A location announced under this slug before the
      slug             restaurant existed is linked and opened.
      none             One is made from the restaurant's own name, address and
                       tagline, last in the list.

    As the system role: the app role may only read this table.
    """
    linked = session.execute(
        select(Location).where(Location.restaurant_id == restaurant_id).limit(1)
    ).scalar_one_or_none()
    if linked is not None:
        if linked.status == LocationStatus.COMING_SOON.value:
            linked.status = LocationStatus.OPEN.value
            return "opened"
        return "unchanged"

    announced = session.execute(
        select(Location).where(Location.slug == listing["slug"], Location.restaurant_id.is_(None))
    ).scalar_one_or_none()
    if announced is not None:
        announced.restaurant_id = restaurant_id
        if announced.status != LocationStatus.HIDDEN.value:
            announced.status = LocationStatus.OPEN.value
        return "linked"

    # Location slugs are unique on their own, so a slug taken by another
    # restaurant's location falls back to one that cannot be.
    slug = listing["slug"]
    if session.execute(select(Location.id).where(Location.slug == slug)).first():
        slug = f"{slug}-{uuid.uuid4().hex[:6]}"
    last = session.execute(text("SELECT COALESCE(max(sort_order), 0) FROM locations")).scalar_one()
    session.add(Location(
        slug=slug, name=listing["name"], city=listing["city"], region=listing["region"],
        address_line=listing["address_line"], blurb=listing["blurb"],
        status=LocationStatus.OPEN.value, restaurant_id=restaurant_id, sort_order=last + 1,
    ))
    return "created"


@router.post("/restaurants/{restaurant_id}/suspend", response_model=dict)
def suspend_restaurant(restaurant_id: UUID, admin: User = Depends(require_platform_admin)):
    with tenant_session(restaurant_id) as session:
        restaurant = session.get(Restaurant, restaurant_id)
        if restaurant is None:
            raise errors.ApiError(404, "RESTAURANT_NOT_FOUND", "No such restaurant.")
        restaurant.status = RestaurantStatus.SUSPENDED.value

    with system_session() as session:
        _audit(session, admin, "SUPER_ADMIN_SUSPEND_RESTAURANT",
               {"restaurant_id": str(restaurant_id)})
    return {"restaurant_id": str(restaurant_id), "status": RestaurantStatus.SUSPENDED.value}


@router.get("/restaurants/{restaurant_id}/orders", response_model=AdminOrderPageOut)
def restaurant_orders(
    restaurant_id: UUID,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    admin: User = Depends(require_platform_admin),
):
    """Orders for one restaurant, for platform support and billing questions.

    A cross-tenant read, so it runs on the declared system surface and is
    audited like every other one. The column list is not a formatting choice:
    zenoeats_system has column-level SELECT on orders, and customer_note,
    pickup_pin_encrypted and customer_user_id are excluded from it. Asking for
    them here would be refused by Postgres, which is the point -- answering
    "why was this card charged" never requires reading what the customer wrote
    or the PIN that releases their food.

    currency comes from the restaurant rather than the order for the same
    reason: orders.currency is outside the grant.
    """
    with system_session() as session:
        restaurant = session.execute(
            text(
                "SELECT slug, currency FROM restaurants "
                "WHERE id = :rid AND deleted_at IS NULL"
            ),
            {"rid": str(restaurant_id)},
        ).mappings().one_or_none()
        if restaurant is None:
            raise errors.ApiError(404, "RESTAURANT_NOT_FOUND", "No such restaurant.")

        _audit(
            session, admin, "SUPER_ADMIN_READ_RESTAURANT_ORDERS",
            {"restaurant_id": str(restaurant_id), "status": status, "limit": limit},
        )

        total = session.execute(
            text(
                "SELECT count(*) FROM orders WHERE restaurant_id = :rid "
                "AND (:status IS NULL OR status = :status)"
            ),
            {"rid": str(restaurant_id), "status": status},
        ).scalar_one()

        rows = session.execute(
            text(
                """
                SELECT o.id, o.order_number, o.status, o.total_minor, o.tax_minor,
                       o.created_at, o.paid_at, o.expires_at,
                       p.status AS payment_status,
                       p.stripe_payment_intent_id
                FROM orders o
                LEFT JOIN payments p ON p.order_id = o.id
                WHERE o.restaurant_id = :rid
                  AND (:status IS NULL OR o.status = :status)
                ORDER BY o.created_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"rid": str(restaurant_id), "status": status, "limit": limit, "offset": offset},
        ).mappings().all()

    return AdminOrderPageOut(
        restaurant_id=restaurant_id,
        slug=restaurant["slug"],
        total=total,
        orders=[
            AdminOrderOut(
                order_id=r["id"], order_number=r["order_number"], status=r["status"],
                total_minor=r["total_minor"], tax_minor=r["tax_minor"],
                currency=restaurant["currency"],
                created_at=r["created_at"], paid_at=r["paid_at"],
                expires_at=r["expires_at"],
                payment_status=r["payment_status"],
                stripe_payment_intent_id=r["stripe_payment_intent_id"],
            )
            for r in rows
        ],
    )


@router.get("/reports", response_model=list[RestaurantReportOut])
def platform_reports(admin: User = Depends(require_platform_admin)):
    with system_session() as session:
        _audit(session, admin, "SUPER_ADMIN_READ_REPORTS", {"scope": "all_restaurants"})
        rows = session.execute(_REPORT_SQL).mappings().all()

    return [
        RestaurantReportOut(
            restaurant_id=r["restaurant_id"], slug=r["slug"], name=r["name"],
            status=r["status"], currency=r["currency"],
            orders_paid=r["orders_paid"],
            gross_revenue_minor=int(r["gross_revenue_minor"] or 0),
            tax_collected_minor=int(r["tax_collected_minor"] or 0),
            average_order_value_minor=(
                int(r["gross_revenue_minor"] or 0) // r["orders_paid"]
                if r["orders_paid"] else 0
            ),
            orders_pending_payment=r["orders_pending_payment"],
            orders_expired=r["orders_expired"],
            last_order_at=r["last_order_at"],
        )
        for r in rows
    ]


# A spreadsheet runs a cell that starts with one of these as a formula. The
# restaurant's name is typed by its own owner in Settings, so "=HYPERLINK(...)"
# or a DDE payload would execute on the platform admin's machine the moment
# the export is opened. Prefixed with an apostrophe, it is shown as text.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_text(value: object) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


@router.get("/reports.csv")
def platform_reports_csv(admin: User = Depends(require_platform_admin)):
    with system_session() as session:
        _audit(session, admin, "SUPER_ADMIN_EXPORT_REPORTS", {"scope": "all_restaurants"})
        rows = session.execute(_REPORT_SQL).mappings().all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "restaurant", "slug", "status", "currency", "orders_paid",
        "gross_revenue", "tax_collected", "average_order_value",
        "pending_payment", "expired", "last_order_at",
    ])
    for r in rows:
        paid = r["orders_paid"] or 0
        gross = int(r["gross_revenue_minor"] or 0)
        writer.writerow([
            _csv_text(r["name"]), _csv_text(r["slug"]), _csv_text(r["status"]),
            _csv_text(r["currency"]), paid,
            f"{gross / 100:.2f}",
            f"{int(r['tax_collected_minor'] or 0) / 100:.2f}",
            f"{(gross // paid) / 100:.2f}" if paid else "0.00",
            r["orders_pending_payment"], r["orders_expired"],
            r["last_order_at"].isoformat() if r["last_order_at"] else "",
        ])

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="zenoeats-report.csv"'},
    )
