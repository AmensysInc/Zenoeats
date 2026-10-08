"""Customers, as mirrored from Clerk.

Clerk owns who a customer is. This module keeps the one row per Clerk user
that the rest of the platform needs -- orders carry a foreign key to it, and
the order emails go to its email -- and fills that row from Clerk's
Backend API the first time a customer shows up, rather than waiting for a
webhook. In local development the webhook never arrives at all, and in
production it can land seconds after the customer has already reached
checkout.

The webhook (api/v1/webhooks.py) is still the source of later changes: a new
email address, a new name, a deleted account.
"""

import logging
from dataclasses import dataclass

import httpx
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.core import errors
from app.db.base import utcnow
from app.db.session import system_session
from app.models import User, UserKind

log = logging.getLogger(__name__)

CLERK_API = "https://api.clerk.com/v1"
PENDING_EMAIL_DOMAIN = "pending.local"


@dataclass(frozen=True)
class ClerkProfile:
    clerk_user_id: str
    email: str | None
    email_verified: bool
    full_name: str | None


def profile_from_payload(data: dict) -> ClerkProfile:
    """Read a Clerk user object -- the Backend API's and the webhook's are the
    same shape."""
    emails = data.get("email_addresses") or []
    primary_id = data.get("primary_email_address_id")
    primary = next((e for e in emails if e.get("id") == primary_id), emails[0] if emails else None)
    email = (primary or {}).get("email_address")
    verified = ((primary or {}).get("verification") or {}).get("status") == "verified"
    name = " ".join(p for p in [data.get("first_name"), data.get("last_name")] if p) or None
    return ClerkProfile(
        clerk_user_id=str(data.get("id") or ""),
        email=email.strip().lower() if email else None,
        email_verified=verified,
        full_name=name[:160] if name else None,
    )


def fetch_profile(clerk_user_id: str) -> ClerkProfile | None:
    """The user as Clerk has them, or None if Clerk cannot say right now.

    None is survivable: the customer still gets a row, with a placeholder
    address that is filled in on a later request or by the webhook. A slow
    Clerk must not stand between a customer and their order.
    """
    if not settings.CLERK_SECRET_KEY:
        return None
    try:
        res = httpx.get(
            f"{CLERK_API}/users/{clerk_user_id}",
            headers={"Authorization": f"Bearer {settings.CLERK_SECRET_KEY}"},
            timeout=5.0,
        )
    except httpx.HTTPError as exc:
        log.warning("clerk backend api unreachable: %s", type(exc).__name__)
        return None
    if res.status_code != 200:
        log.warning("clerk backend api answered %s for a user lookup", res.status_code)
        return None
    return profile_from_payload(res.json())


def _placeholder_email(clerk_user_id: str) -> str:
    return f"{clerk_user_id}@{PENDING_EMAIL_DOMAIN}"


def has_placeholder_email(user: User) -> bool:
    return user.email.endswith("@" + PENDING_EMAIL_DOMAIN)


def receipt_address(user: User) -> str | None:
    """Where this customer's order emails may be sent, and the email given
    to Stripe with their payment, or None for nowhere.

    Also asked of guests, who have no Clerk profile at all: they typed their
    address at checkout, so it is real by construction and comes straight
    back. The placeholder case below simply never applies to them.

    A customer whose profile Clerk could not supply yet carries a
    user_...@pending.local placeholder. That is not an address anyone reads,
    and emailing it would send every order email into the void. No email is
    better than a bounced one -- and far better than refusing the payment
    over it: the order page still shows the pickup PIN, which is what the
    customer actually needs.
    """
    if has_placeholder_email(user):
        log.warning("no receipt email for user %s: Clerk profile still unavailable", user.id)
        return None
    return user.email


def customer_for_clerk_user(clerk_user_id: str, token_email: str | None = None) -> User:
    """The customer row for a verified Clerk session, created on first sight.

    Refuses a Clerk id that somehow belongs to a staff or admin row: those
    populations never share a row, and a customer session must not open one.
    """
    with system_session() as session:
        user = session.execute(
            select(User).where(User.clerk_user_id == clerk_user_id)
        ).scalar_one_or_none()
        needs_profile = user is None or has_placeholder_email(user)
        if user is not None and not needs_profile:
            _ensure_customer(user)
            session.expunge(user)
            return user

    # Outside any transaction: a network call must never hold a connection
    # from the narrow system pool.
    profile = fetch_profile(clerk_user_id) if needs_profile else None

    try:
        with system_session() as session:
            user = upsert_customer(
                session, clerk_user_id, profile,
                fallback_email=token_email,
            )
            _ensure_customer(user)
            session.expunge(user)
            return user
    except IntegrityError:
        # Two first requests from the same new customer raced to create the
        # row. The unique index on clerk_user_id let exactly one win.
        with system_session() as session:
            user = session.execute(
                select(User).where(User.clerk_user_id == clerk_user_id)
            ).scalar_one()
            _ensure_customer(user)
            session.expunge(user)
            return user


def upsert_customer(
    session, clerk_user_id: str, profile: ClerkProfile | None, fallback_email: str | None = None
) -> User:
    """Create or refresh the row for a Clerk user. Shared by the request path
    and the webhook worker so both agree on what a customer row holds."""
    user = session.execute(
        select(User).where(User.clerk_user_id == clerk_user_id)
    ).scalar_one_or_none()

    email = (profile.email if profile else None) or (fallback_email or "").strip().lower() or None

    if user is None:
        user = User(
            kind=UserKind.CUSTOMER.value,
            clerk_user_id=clerk_user_id,
            email=email or _placeholder_email(clerk_user_id),
            full_name=profile.full_name if profile else None,
        )
        session.add(user)
        session.flush()
        return user

    if email:
        user.email = email
    # Only when we have none. A customer can change their name on their
    # profile page, which does not write back to Clerk, and a later
    # user.updated webhook must not quietly undo that.
    if profile and profile.full_name and not user.full_name:
        user.full_name = profile.full_name
    session.flush()
    return user


def deactivate(session, clerk_user_id: str) -> tuple[str, str | None] | None:
    """A user deleted in Clerk, by them or by us. Closes the account here.

    Returns (address, first name) to tell them at, when this is what closed
    it and there is a real address; None when it was closed already -- by
    the customer on a storefront, which told them itself -- or there is no
    one to tell.
    """
    user = session.execute(
        select(User).where(User.clerk_user_id == clerk_user_id)
    ).scalar_one_or_none()
    if user is None or user.kind != UserKind.CUSTOMER.value:
        return None
    tell = contact_before_closing(user)
    close_account(session, user)
    return tell


def contact_before_closing(user: User) -> tuple[str, str | None] | None:
    """Where to tell someone their account closed, read before closing erases
    it. None if it is already closed, or its address was never real."""
    if user.deleted_at is not None or has_placeholder_email(user):
        return None
    return user.email, (user.full_name or "").split(" ")[0] or None


def close_account(session, user: User) -> None:
    """Close a customer's account and take their details off it.

    What legal/data-deletion.html promises, and no more: the name, phone
    number, address and email go, the favourites go, and the account can no
    longer be signed in to. Orders stay, with the name and address that were
    on them when they were placed -- they are the restaurant's record of a
    sale, and the page says so.

    The row itself stays because orders, audit trails and tax records point
    at it with NOT NULL keys. Emptied of everything personal, it is an
    anchor rather than a profile.

    Idempotent: a customer who deletes their account here and a user.deleted
    webhook arriving afterwards are the same operation twice, and the second
    must be harmless.
    """
    from app.models import CustomerFavourite

    user.is_active = False
    user.deleted_at = user.deleted_at or utcnow()
    user.full_name = None
    user.phone = None
    user.address = None
    # The credential itself, for an account whose password we hold. Clerk
    # accounts have theirs removed by delete_clerk_user; these have it here,
    # and leaving it would be an account that is closed and still signs in.
    user.password_hash = None
    # Email is NOT NULL and unique, so it is replaced rather than emptied.
    # The placeholder is the one a customer awaiting verification already
    # gets, so nothing downstream meets a shape it has not seen.
    #
    # Keyed on the user id when there is no Clerk id -- a password account has
    # none. Without that fallback the address stays on the closed row, and
    # uq_users_customer_email then stops that person ever registering again
    # with the address they just asked us to forget.
    if not has_placeholder_email(user):
        user.email = _placeholder_email(user.clerk_user_id or str(user.id))
    session.execute(delete(CustomerFavourite).where(CustomerFavourite.user_id == user.id))
    session.flush()
    log.info("closed customer account %s", user.id)


def delete_clerk_user(clerk_user_id: str) -> None:
    """Remove the sign-in itself, which Clerk owns.

    Raises rather than shrugging: an account whose details were emptied here
    but which can still be signed in to is the worst of both, so the caller
    stops before touching anything local.
    """
    if not settings.CLERK_SECRET_KEY:
        raise errors.ApiError(
            503, "IDENTITY_UNAVAILABLE",
            "Accounts cannot be closed right now. Try again later.",
        )
    try:
        res = httpx.delete(
            f"{CLERK_API}/users/{clerk_user_id}",
            headers={"Authorization": f"Bearer {settings.CLERK_SECRET_KEY}"},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        log.warning("clerk could not be reached to delete a user: %s", type(exc).__name__)
        raise errors.ApiError(
            503, "IDENTITY_UNAVAILABLE",
            "Accounts cannot be closed right now. Try again later.",
        ) from exc
    # 404 means Clerk has no such user, which is the state being asked for.
    if res.status_code not in (200, 204, 404):
        log.error("clerk answered %s deleting a user", res.status_code)
        raise errors.ApiError(
            503, "IDENTITY_UNAVAILABLE",
            "Accounts cannot be closed right now. Try again later.",
        )


def _ensure_customer(user: User) -> None:
    if user.kind != UserKind.CUSTOMER.value:
        log.error("a clerk session named a %s row; refused", user.kind)
        raise errors.ApiError(401, "UNAUTHENTICATED", "Sign in to continue.")
    if not user.is_active or user.deleted_at is not None:
        raise errors.ApiError(403, "ACCOUNT_INACTIVE", "This account is not active.")
