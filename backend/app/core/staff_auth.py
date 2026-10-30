"""Restaurant staff authentication.

Three identity systems, deliberately separate and non-overlapping:

  * core/customer_auth  customers
  * ADMIN_USERS         platform administrators   (core/platform_auth.py)
  * this module         restaurant staff

Credentials here live in the database rather than the environment, because
staff accounts are created and revoked continuously by people who never touch
a deployment. The super admin issues the owner's login; the owner manages
their own staff from the restaurant portal.

A session identifies the *person*, never the restaurant. The tenant still
comes from the Host header and authorization still comes from restaurant_users
read under RLS, so a session for one restaurant presented on another's
subdomain simply finds no membership and is refused. Putting a restaurant id
in the token would have created a second, weaker source of truth.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.config import settings

log = logging.getLogger(__name__)

_hasher = PasswordHasher()

TOKEN_TYPE = "restaurant_staff"
SESSION_COOKIE = "zenoeats_staff_session"

# Long enough that a shift does not end with an unexpected sign-out, short
# enough that a tablet left on a counter overnight is not still authenticated.
DEFAULT_TTL_MINUTES = 720


@dataclass(frozen=True)
class StaffPrincipal:
    user_id: UUID
    # When the token was issued, in epoch seconds, so a session ended on the
    # server (users.sessions_valid_after) can be told apart from a live one.
    issued_at: float


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(digest: str, password: str) -> bool:
    try:
        _hasher.verify(digest, password)
        return True
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def dummy_verify(password: str) -> None:
    """Burn comparable time when no account matched.

    Without this, an unknown address answers measurably faster than a known
    one with a wrong password, which turns the login form into a directory of
    who has an account.
    """
    try:
        _hasher.verify(_DUMMY_HASH, password)
    except Exception:
        pass


def issue_session(user_id: UUID) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id),
            "typ": TOKEN_TYPE,
            # Sub-second, not whole seconds: a sign-in straight after a
            # revocation must not land in the same second and be refused.
            "iat": now.timestamp(),
            "exp": now + timedelta(minutes=settings.STAFF_SESSION_TTL_MINUTES),
        },
        settings.SESSION_SECRET,
        algorithm="HS256",
    )


def verify_session(token: str) -> StaffPrincipal | None:
    try:
        claims = jwt.decode(
            token,
            settings.SESSION_SECRET,
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError:
        return None

    # The type claim is what keeps the three systems apart. Both this and the
    # platform-admin token are signed with SESSION_SECRET, so without it an
    # admin session would satisfy a staff dependency and vice versa.
    if claims.get("typ") != TOKEN_TYPE:
        return None

    try:
        return StaffPrincipal(
            user_id=UUID(str(claims.get("sub"))), issued_at=float(claims["iat"])
        )
    except (ValueError, TypeError):
        return None


def generate_temp_password() -> str:
    """A readable one-time password for the super admin to pass on.

    Deliberately not a raw token: it gets read down a phone or typed from a
    note, so it avoids characters that are ambiguous out loud or on a screen.
    The account is flagged must_change_password, so this value is only ever
    valid until first sign-in completes.
    """
    import secrets

    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I, O, 0, 1
    return "-".join(
        "".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)
    )


_DUMMY_HASH = _hasher.hash("a value nobody knows, used only to burn time")
