"""Request dependencies: identity, tenant resolution, tenant-scoped session.

The order matters and it is the same order as the defense-in-depth diagram in
section 16.2:

    TLS -> authenticate -> RBAC -> trusted tenant context -> state validation
        -> transaction with FORCE RLS -> audit
"""

import time
from dataclasses import dataclass
from typing import Iterator

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import errors
from app.core.auth import AuthError, ClerkPrincipal, verify_clerk_token
from app.core import customer_auth, guest_auth, platform_auth, staff_auth
from app.core.tenant import admin_host, extract_slug, host_of
from app.db.session import AppSessionLocal, system_session
from app.models import (
    Restaurant, RestaurantStatus, RestaurantUser, StaffRole, StaffStatus, User,
    UserKind,
)
from app.services import clerk_customers, guest_customers
from sqlalchemy import text


@dataclass
class TenantContext:
    restaurant_id: str
    slug: str


def get_principal(authorization: str | None = Header(default=None)) -> ClerkPrincipal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise errors.ApiError(401, "UNAUTHENTICATED", "Sign in to continue.")
    token = authorization.split(" ", 1)[1].strip()
    try:
        return verify_clerk_token(token)
    except AuthError as exc:
        raise errors.ApiError(401, "UNAUTHENTICATED", str(exc)) from exc


def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> User:
    """Whoever is checking out: a signed-in customer, or a guest.

    A Clerk bearer token is tried first and wins outright. Someone who signed
    in after starting as a guest is the account they signed in as, whatever
    stale guest cookie the browser still holds -- the alternative, letting a
    cookie nobody authenticated shadow a real session, is the wrong way round.

    Either way the answer is a users row, so every caller -- ownership checks,
    idempotency, the rate limiter, receipts -- is written once and does not
    know which kind it got. What a guest cannot do is anything but ordering:
    the row is reachable only through the cookie that created it.
    """
    if authorization and authorization.lower().startswith("bearer "):
        principal = get_principal(authorization)
        return clerk_customers.customer_for_clerk_user(
            principal.clerk_user_id, token_email=principal.email
        )

    # A customer account of our own (core/customer_auth.py). Above the guest
    # cookie for the same reason the Clerk token is: a browser can hold both
    # after someone starts as a guest and then signs in, and the one nobody
    # authenticated must not shadow the one they did.
    account_cookie = request.cookies.get(customer_auth.SESSION_COOKIE)
    if account_cookie:
        principal = customer_auth.verify_session(account_cookie)
        if principal is not None:
            with system_session() as session:
                user = session.get(User, principal.user_id)
                if (
                    user is not None
                    and user.kind == UserKind.CUSTOMER.value
                    and user.deleted_at is None
                    # Honours "sign out everywhere": a signature that is still
                    # valid is not enough once the account has revoked.
                    and not user.session_revoked(principal.issued_at)
                ):
                    session.expunge(user)
                    return user

    cookie = request.cookies.get(guest_auth.SESSION_COOKIE)
    if cookie:
        guest = guest_auth.verify_session(cookie)
        if guest is not None:
            return guest_customers.guest_for_session(guest.user_id)

    # No token and no usable cookie. The wording covers both ways forward,
    # because the sign-in page this sends people to offers both.
    raise errors.ApiError(401, "UNAUTHENTICATED", "Sign in or continue as a guest to order.")


def optional_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> User | None:
    """get_current_user, but None instead of a 401.

    For the one endpoint that has a second way in -- the order-view token in a
    guest's confirmation email -- and therefore must not refuse a caller
    before it has looked at that.
    """
    try:
        return get_current_user(request, authorization)
    except errors.ApiError as exc:
        if exc.status_code == 401:
            return None
        raise


def resolve_tenant(request: Request) -> TenantContext:
    """Trusted tenant context from the Host header.

    A restaurant id in the request body is decoration. This is the only
    thing that decides which tenant the request belongs to.
    """
    slug = extract_slug(request.headers.get("host"))
    if slug is None:
        # Development convenience: allow an explicit header when the client
        # cannot set a subdomain (curl, tests, localhost without wildcard DNS).
        slug = request.headers.get("x-zenoeats-restaurant")
    if not slug:
        raise errors.ApiError(404, "RESTAURANT_NOT_FOUND", "No restaurant for this address.")
    return _resolve(slug, CUSTOMER_VISIBLE)


# A DRAFT restaurant is invisible to customers -- there is nothing to order
# yet -- but its own staff must be able to reach it, because the whole point
# of the draft period is building the menu, the staff list and the settings
# before anyone can see them. Without this split the portal would only open
# once the restaurant was already live, and every restaurant would go live
# with an empty storefront.
#
# SUSPENDED is staff-visible for the same reason: whatever caused the
# suspension usually has to be fixed from inside the portal.
#
# ARCHIVED is terminal and deleted rows are gone, so neither audience sees
# either.
CUSTOMER_VISIBLE = frozenset({RestaurantStatus.ACTIVE.value})
STAFF_VISIBLE = frozenset(
    {
        RestaurantStatus.ACTIVE.value,
        RestaurantStatus.DRAFT.value,
        RestaurantStatus.INACTIVE.value,
        RestaurantStatus.SUSPENDED.value,
    }
)


# Turning a hostname into a restaurant is on the path of every single request,
# and it reads a row that changes a handful of times in a restaurant's whole
# life. Asking Postgres each time cost a checkout from the narrow system pool
# plus a round trip -- about 3ms against a database on the same machine, and
# considerably more across a network -- to be told the same thing as a moment
# ago.
#
# The entry holds the id and the status. It never holds the decision: which
# statuses an audience may see is applied fresh below on every call, so the
# customer and staff resolvers share one entry and neither can widen the
# other.
#
# A TTL rather than explicit invalidation, because the API runs as several
# worker processes and a suspension applied inside one of them cannot reach
# another's memory. What matters is the bound: after activate, suspend or
# delete, every worker agrees within this many seconds. Keep it short.
_TENANT_TTL_SECONDS = 5.0
_TENANT_CACHE_MAX = 512
_tenant_cache: dict[str, tuple[float, tuple[str, str] | None]] = {}


def _lookup_restaurant(slug: str) -> tuple[str, str] | None:
    """(id, status) for a live restaurant on this slug, or None for no such
    restaurant. Cached briefly; see the note above."""
    now = time.monotonic()
    cached = _tenant_cache.get(slug)
    if cached is not None and cached[0] > now:
        return cached[1]

    with system_session() as session:
        row = session.execute(
            select(Restaurant.id, Restaurant.status)
            .where(Restaurant.slug == slug, Restaurant.deleted_at.is_(None))
        ).one_or_none()

    found = None if row is None else (str(row.id), row.status)

    # Misses are cached too. A sweep of the wildcard domain is exactly what an
    # unknown subdomain looks like, and those must not each cost a query. The
    # cap keeps such a sweep from growing this dict without limit; dropping
    # everything is fine, the next request simply re-reads.
    if len(_tenant_cache) >= _TENANT_CACHE_MAX:
        _tenant_cache.clear()
    _tenant_cache[slug] = (now + _TENANT_TTL_SECONDS, found)
    return found


def _resolve(slug: str, allowed: frozenset[str]) -> TenantContext:
    found = _lookup_restaurant(slug)

    # One message for "no such restaurant" and "not visible to you". The
    # difference would let anyone enumerate which subdomains exist.
    if found is None or found[1] not in allowed:
        raise errors.ApiError(404, "RESTAURANT_NOT_FOUND", "No restaurant for this address.")

    return TenantContext(restaurant_id=found[0], slug=slug)


def resolve_tenant_staff(request: Request) -> TenantContext:
    """Tenant context for the restaurant portal.

    Same host resolution as the customer surface, wider set of statuses. This
    grants no access on its own: membership of this restaurant, read from
    restaurant_users under RLS, still decides everything.
    """
    slug = extract_slug(request.headers.get("host"))
    if slug is None:
        slug = request.headers.get("x-zenoeats-restaurant")
    if not slug:
        raise errors.ApiError(404, "RESTAURANT_NOT_FOUND", "No restaurant for this address.")
    return _resolve(slug, STAFF_VISIBLE)


def _open_tenant_session(tenant: TenantContext) -> Iterator[Session]:
    """A zenoeats_app transaction with SET LOCAL app.current_tenant.

    Everything read or written through this session is filtered by FORCE ROW
    LEVEL SECURITY.
    """
    session = AppSessionLocal()
    try:
        session.begin()
        session.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": tenant.restaurant_id},
        )
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def tenant_db(tenant: TenantContext = Depends(resolve_tenant)) -> Iterator[Session]:
    """Tenant session for the customer surface."""
    yield from _open_tenant_session(tenant)


def tenant_db_staff(tenant: TenantContext = Depends(resolve_tenant_staff)) -> Iterator[Session]:
    """Tenant session for the restaurant portal.

    Identical transaction and identical RLS. The only difference is which
    restaurant statuses resolve at all, so a draft restaurant's staff can set
    it up before it goes live.
    """
    yield from _open_tenant_session(tenant)


# How every endpoint asks for its database session.
#
# scope="function" is the whole point of these aliases. A dependency with
# yield defaults to scope "request", which FastAPI closes after the response
# has been sent -- and after any background task has run. The commit therefore
# landed after the caller had already been told the write succeeded: an
# invitation whose email took seconds to queue was not yet readable when the
# invitee signed in, and a customer's order could be missing from the payment
# request that followed it. "function" closes the session as soon as the
# endpoint returns, before the response leaves.
#
# Asking for the session any other way would open a second one: FastAPI keys
# its per-request cache on the scope as well as the callable, so a mixed
# request would run two transactions. tests/test_commit_before_response.py
# fails if an endpoint does that.
TenantDb = Depends(tenant_db, scope="function")
StaffDb = Depends(tenant_db_staff, scope="function")


def current_restaurant_staff(
    tenant: TenantContext = Depends(resolve_tenant_staff),
    db: Session = StaffDb,
) -> Restaurant:
    """The restaurant behind the portal, including one still in draft."""
    restaurant = db.get(Restaurant, tenant.restaurant_id)
    if restaurant is None:
        raise errors.tenant_scope_denied()
    return restaurant


def current_restaurant(
    tenant: TenantContext = Depends(resolve_tenant),
    db: Session = TenantDb,
) -> Restaurant:
    restaurant = db.get(Restaurant, tenant.restaurant_id)
    if restaurant is None:
        # Would mean RLS filtered the tenant root, which should be
        # impossible. Fail closed.
        raise errors.tenant_scope_denied()
    return restaurant


def current_staff_user(request: Request) -> User:
    """Authenticate restaurant staff from their session cookie.

    Not Clerk: Clerk is the customer identity provider. Staff credentials are
    issued by the platform and live in users.password_hash.

    Deliberately does NOT enforce the password-change gate, because
    change_password itself depends on this -- an account holding a temporary
    password has to be able to reach exactly one endpoint.
    """
    token = request.cookies.get(staff_auth.SESSION_COOKIE)
    if not token:
        raise errors.ApiError(401, "UNAUTHENTICATED", "Sign in to continue.")

    principal = staff_auth.verify_session(token)
    if principal is None:
        raise errors.ApiError(401, "UNAUTHENTICATED", "Your session has expired. Sign in again.")

    with system_session() as session:
        user = session.get(User, principal.user_id)
        # password_hash is the marker of a credentialed account. A customer or
        # a platform admin has none, so a token naming one of them -- however
        # it arose -- is not a staff session.
        if user is None or user.password_hash is None or user.kind != UserKind.STAFF.value:
            raise errors.ApiError(401, "UNAUTHENTICATED", "Sign in to continue.")
        if user.session_revoked(principal.issued_at):
            # The password changed, or a super admin reset it, after this
            # token was issued.
            raise errors.ApiError(401, "UNAUTHENTICATED", "Your session has ended. Sign in again.")
        if not user.is_active or user.deleted_at is not None:
            raise errors.ApiError(403, "ACCOUNT_INACTIVE", "This account is not active.")
        session.expunge(user)
        return user


def current_staff_user_ready(user: User = Depends(current_staff_user)) -> User:
    """A staff account that has finished setting itself up.

    Enforced here rather than in the frontend: a temporary password is issued
    over the phone or on a note, so an account still holding one must not be
    able to do anything by calling the API directly.
    """
    if user.must_change_password:
        raise errors.ApiError(
            403, "PASSWORD_CHANGE_REQUIRED", "Choose a new password before continuing."
        )
    return user


def require_staff(*roles: StaffRole):
    """Authorize a staff role within the resolved tenant.

    The session identifies the person; the tenant still comes from the Host
    header and the authoritative record is restaurant_users, read under the
    tenant-scoped session. So a session issued for one restaurant, presented
    on another's subdomain, finds no membership and is refused -- without the
    token needing to carry a restaurant id it could be wrong about.
    """
    allowed = {r.value for r in roles} if roles else {r.value for r in StaffRole}

    def _dep(
        user: User = Depends(current_staff_user_ready),
        db: Session = StaffDb,
        tenant: TenantContext = Depends(resolve_tenant_staff),
    ) -> RestaurantUser:
        membership = db.execute(
            select(RestaurantUser).where(
                RestaurantUser.user_id == user.id,
                RestaurantUser.status == StaffStatus.ACTIVE.value,
            )
        ).scalar_one_or_none()

        if membership is None or membership.role_code not in allowed:
            raise errors.tenant_scope_denied()
        return membership

    # Readable, so a test can prove every endpoint carries a role check and
    # say which roles each one admits.
    _dep.staff_roles = frozenset(allowed)
    return _dep


def require_admin_host(request: Request) -> None:
    """The platform portal answers on admin.<root domain> and nowhere else.

    Every restaurant is a subdomain of the same root, and a cookie set on one
    subdomain counts as same-site on all of them: a session cookie still rides
    a request made from a storefront page. Serving the super-admin API on
    those hostnames too meant a script running on any storefront -- ours or
    injected -- could drive it with a signed-in operator's session. The portal
    now exists at exactly one address, and requests to any other are refused
    before authentication is even attempted.
    """
    if host_of(request.headers.get("host")) != admin_host():
        raise errors.ApiError(404, "NOT_FOUND", "No such endpoint.")


def require_same_origin(request: Request) -> None:
    """Refuse a cookie-authenticated request made from another origin.

    Browsers send Origin on every cross-origin request and on same-origin
    writes. Our portals are served from the same origin as the API they call,
    so an Origin naming a different host is either a cross-site request or a
    page on a neighbouring subdomain -- neither of which should be able to act
    with an operator's session. Requests with no Origin at all (curl, a server,
    a same-origin GET) are left to the session checks.
    """
    origin = request.headers.get("origin")
    if origin is None or origin == "null":
        return
    if host_of(origin) != host_of(request.headers.get("host")):
        raise errors.ApiError(403, "CROSS_ORIGIN_DENIED", "This request came from another site.")


def require_platform_admin(request: Request) -> User:
    """Authorize a platform administrator from the environment-backed session.

    Deliberately not Clerk. Clerk is the customer identity provider; platform
    operators are a separate, tiny population declared in ADMIN_USERS. A Clerk
    token cannot satisfy this dependency and an admin session cannot satisfy
    the customer ones -- the two never overlap.

    A users row is resolved (and created on first sign-in) because
    platform_audit_logs.actor_user_id is a NOT NULL foreign key to users.id.
    Every audited super-admin action needs a real actor, and naming each
    operator in ADMIN_USERS is what keeps that column worth reading.
    """
    token = request.cookies.get(platform_auth.SESSION_COOKIE)
    if not token:
        raise errors.ApiError(401, "UNAUTHENTICATED", "Sign in to continue.")

    admin = platform_auth.verify_session(token)
    if admin is None:
        raise errors.ApiError(401, "UNAUTHENTICATED", "Your session has expired. Sign in again.")

    user = ensure_platform_admin_user(admin.email)
    if admin.issued_at is not None and user.session_revoked(admin.issued_at):
        # This admin signed out after the token was issued. Signing out ends
        # every session, so a copied cookie is as dead as the deleted one.
        raise errors.ApiError(401, "UNAUTHENTICATED", "Your session has ended. Sign in again.")
    return user


def ensure_platform_admin_user(email: str) -> User:
    """The users row backing an environment-declared administrator.

    is_platform_admin is re-asserted on every sign-in so the environment stays
    the single source of truth: revoking someone in ADMIN_USERS is enough, and
    a stale row cannot grant access on its own.
    """
    with system_session() as session:
        user = session.execute(
            select(User).where(
                User.email == email, User.kind == UserKind.PLATFORM_ADMIN.value
            )
        ).scalar_one_or_none()

        if user is None:
            user = User(
                kind=UserKind.PLATFORM_ADMIN.value,
                email=email,
                full_name="Platform Administrator",
                is_platform_admin=True,
            )
            session.add(user)
            session.flush()
        elif not user.is_platform_admin:
            user.is_platform_admin = True

        if not user.is_active or user.deleted_at is not None:
            raise errors.ApiError(403, "ACCOUNT_INACTIVE", "This account is not active.")

        session.expunge(user)
        return user
