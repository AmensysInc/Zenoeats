"""Customer accounts, with credentials we hold ourselves.

The fifth identity, and the one this module exists to take back:

  * this module         signed-in customers       (was Clerk)
  * core/guest_auth     guest customers
  * core/platform_auth  platform administrators
  * core/staff_auth     restaurant staff

Clerk owned customer credentials, sessions, verification and social sign-in.
Everything it did on the session side -- hash a password, mint a token, set a
cookie, end it again -- this codebase already did three times over for staff,
admins and guests, with the same Argon2id hasher and the same signed-JWT
shape. So a customer account is that pattern a fourth time rather than a
vendor, and the API now has no runtime dependency on an identity provider.

WHAT THIS DOES NOT REPLACE. Clerk also brokered Google and Apple sign-in.
Those are OAuth against Google and Apple, not against Clerk, and they need
credentials registered with each of them either way -- no library removes
that. They layer on top of this module: the end of either flow is a verified
email address, and from there it issues exactly the session below.

The password rules are deliberately ordinary. The interesting security here is
not length policy, it is that an unknown address and a wrong password are
indistinguishable to the caller -- in wording, in status code and in timing.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt

from app.config import settings

# Argon2id, exactly as staff and admins are hashed. Imported rather than
# re-instantiated so there is one hasher, one set of parameters, and one place
# to change them: a second PasswordHasher() here would quietly drift.
from app.core.staff_auth import dummy_verify, hash_password, verify_password

log = logging.getLogger(__name__)

__all__ = [
    "MIN_PASSWORD_LENGTH",
    "RESET_TOKEN_TTL_MINUTES",
    "SESSION_COOKIE",
    "CustomerPrincipal",
    "ResetRequest",
    "dummy_verify",
    "hash_password",
    "issue_reset_token",
    "issue_session",
    "password_complaint",
    "verify_password",
    "verify_reset_token",
    "verify_session",
]

TOKEN_TYPE = "customer_account"
SESSION_COOKIE = "zenoeats_customer_session"

# Ten, not the twelve an administrator is held to. A platform admin can empty
# a restaurant; a customer can see their own order history and a pickup PIN.
# Asking for a passphrase at a burger counter buys a sticky note, not safety.
MIN_PASSWORD_LENGTH = 10


@dataclass(frozen=True)
class CustomerPrincipal:
    user_id: UUID
    # Epoch seconds, sub-second precision. Compared against
    # users.sessions_valid_after so "sign out everywhere" can end a session
    # that still has a valid signature and an unexpired exp.
    issued_at: float


def issue_session(user_id: UUID) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id),
            "typ": TOKEN_TYPE,
            # Sub-second, matching staff_auth: a sign-in immediately after a
            # revocation must not land in the same whole second and be refused.
            "iat": now.timestamp(),
            "exp": now + timedelta(minutes=settings.CUSTOMER_SESSION_TTL_MINUTES),
        },
        settings.SESSION_SECRET,
        algorithm="HS256",
    )


def verify_session(token: str) -> CustomerPrincipal | None:
    try:
        claims = jwt.decode(
            token,
            settings.SESSION_SECRET,
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError:
        return None

    # The type claim is what keeps the identity systems apart. All of them are
    # signed with SESSION_SECRET, so without this a customer cookie would
    # satisfy a staff or admin dependency -- and this is the one of them
    # anybody can mint for themselves simply by registering.
    if claims.get("typ") != TOKEN_TYPE:
        return None

    try:
        return CustomerPrincipal(
            user_id=UUID(str(claims.get("sub"))), issued_at=float(claims["iat"])
        )
    except (ValueError, TypeError):
        return None


def password_complaint(password: str) -> str | None:
    """Why this password is refused, or None when it is fine.

    Length only. Composition rules (a digit, a symbol, a capital) push people
    towards Password1! and are no longer recommended by NIST; length is the
    part that actually costs an attacker anything.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > 256:
        # Not a strength rule: Argon2 will happily hash a megabyte, and doing
        # so on an unauthenticated endpoint is free CPU for whoever asked.
        return "That password is too long."
    return None


# ------------------------------------------------------------ password reset

RESET_TOKEN_TYPE = "customer_password_reset"

# Short. This token is a bearer credential sitting in an inbox, and the person
# holding it asked for it a moment ago -- an hour is generous for reading an
# email, and long past the point where a link found later should still work.
RESET_TOKEN_TTL_MINUTES = 60


@dataclass(frozen=True)
class ResetRequest:
    user_id: UUID
    # A fingerprint of the password digest the token was minted against. See
    # _digest_fingerprint for why it is here.
    fingerprint: str


def _digest_fingerprint(password_hash: str | None) -> str:
    """A short, non-reversible marker for the digest in force right now.

    This is what makes a reset link single-use without a table to track spent
    ones. The token carries the fingerprint of the password it was issued
    against; changing the password changes the digest (Argon2 salts every
    hash, so even the same password produces a different one), the
    fingerprint no longer matches, and every link issued before that moment
    stops working -- including the one just used, and including any issued by
    an attacker who asked for a reset at the same time.

    Hashed rather than carried plainly: the digest must not travel in a URL,
    and a fingerprint cannot be worked back into one.
    """
    return hashlib.sha256((password_hash or "").encode()).hexdigest()[:32]


def issue_reset_token(user_id: UUID, password_hash: str | None) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id),
            "typ": RESET_TOKEN_TYPE,
            "pwf": _digest_fingerprint(password_hash),
            "iat": now.timestamp(),
            "exp": now + timedelta(minutes=RESET_TOKEN_TTL_MINUTES),
        },
        settings.SESSION_SECRET,
        algorithm="HS256",
    )


def verify_reset_token(token: str) -> ResetRequest | None:
    """The request this token stands for, or None.

    Signature, expiry and type only. Whether the fingerprint still matches is
    the caller's check, because only the caller has the current digest --
    and answering "this link is spent" needs that comparison, not this one.
    """
    try:
        claims = jwt.decode(
            token,
            settings.SESSION_SECRET,
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError:
        return None

    if claims.get("typ") != RESET_TOKEN_TYPE:
        return None

    try:
        return ResetRequest(
            user_id=UUID(str(claims.get("sub"))), fingerprint=str(claims.get("pwf", ""))
        )
    except (ValueError, TypeError):
        return None


def reset_token_matches(request: ResetRequest, password_hash: str | None) -> bool:
    """Whether this token was issued against the password still in force."""
    import hmac

    return hmac.compare_digest(request.fingerprint, _digest_fingerprint(password_hash))
