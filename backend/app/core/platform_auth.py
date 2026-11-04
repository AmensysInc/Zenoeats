"""Platform administrator authentication.

Customers have their own sign-in. Platform administrators are a different
population entirely -- a handful of trusted operators -- and authenticate
against credentials declared in the environment instead.

Two properties this file is responsible for:

  * The password is never stored, anywhere. ADMIN_USERS carries an argon2
    hash. .env travels: it is copied between machines, read by three
    processes and pasted into support threads. A leaked hash is inert; a
    leaked password is immediate control of every restaurant's revenue data.

  * Admins are named, not shared. platform_audit_logs.actor_user_id records
    who suspended a restaurant or exported platform revenue. One shared
    login would make that column meaningless, so each operator gets their
    own entry and their own users row.
"""

import base64
import binascii
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

from app.config import settings
from app.core.admin_password import hash_password  # noqa: F401  (re-exported)
from app.core.admin_password import hasher as _hasher

log = logging.getLogger(__name__)


# Distinguishes a platform-admin session from any other token that might
# reach a customer dependency. A customer session can never satisfy this and vice versa.
TOKEN_TYPE = "platform_admin"
SESSION_COOKIE = "zenoeats_admin_session"


@dataclass(frozen=True)
class PlatformAdmin:
    email: str
    # Epoch seconds the session token was issued; None for a fresh sign-in.
    issued_at: float | None = None


def _registry() -> dict[str, str]:
    """email -> argon2 hash, parsed from ADMIN_USERS.

    Format: "alice@example.com:<base64>;bob@example.com:<base64>"

    Entries are separated by ";" and not "," because an argon2 hash contains
    commas of its own. The hash itself is base64 encoded; see hash_password.
    """
    registry: dict[str, str] = {}
    for entry in settings.ADMIN_USERS.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        email, sep, digest = entry.partition(":")
        if not sep or not digest:
            log.error("ADMIN_USERS entry is malformed and was skipped: %r", email)
            continue
        try:
            decoded = base64.b64decode(digest.strip(), validate=True).decode()
        except (binascii.Error, UnicodeDecodeError):
            log.error("ADMIN_USERS hash for %r is not valid base64; skipped", email)
            continue
        registry[email.strip().lower()] = decoded
    return registry


def authenticate(email: str, password: str) -> PlatformAdmin | None:
    """Verify credentials. Returns None on any failure, without saying which.

    Deliberately does not distinguish "no such admin" from "wrong password":
    that difference tells an attacker which addresses are worth attacking.
    An unknown email still runs a verification against a dummy hash so the
    two paths take comparable time.
    """
    registry = _registry()
    if not registry:
        log.error("ADMIN_USERS is empty; no platform admin can sign in")
        return None

    normalized = email.strip().lower()
    digest = registry.get(normalized)

    if digest is None:
        # Constant-ish work for unknown accounts, so response time does not
        # reveal whether the address exists.
        try:
            _hasher.verify(_DUMMY_HASH, password)
        except Exception:
            pass
        return None

    try:
        _hasher.verify(digest, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return None

    return PlatformAdmin(email=normalized)


def issue_session(admin: PlatformAdmin) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": admin.email,
            "typ": TOKEN_TYPE,
            # Sub-second, so signing back in straight after signing out is not
            # caught by the revocation that sign-out just recorded.
            "iat": now.timestamp(),
            "exp": now + timedelta(minutes=settings.ADMIN_SESSION_TTL_MINUTES),
        },
        settings.SESSION_SECRET,
        algorithm="HS256",
    )


def verify_session(token: str) -> PlatformAdmin | None:
    """Validate a session token and confirm the admin is still declared.

    Re-checking ADMIN_USERS on every request is the revocation mechanism:
    removing someone from .env ends their access at the next request rather
    than whenever their token happens to expire.
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

    if claims.get("typ") != TOKEN_TYPE:
        return None

    email = str(claims.get("sub", "")).lower()
    if email not in _registry():
        log.info("session rejected: %s is no longer in ADMIN_USERS", email)
        return None

    return PlatformAdmin(email=email, issued_at=float(claims["iat"]))


# A real argon2 hash of a value nobody knows, used only to burn time on the
# unknown-account path.
_DUMMY_HASH = _hasher.hash("a value nobody knows, used only to burn time")
