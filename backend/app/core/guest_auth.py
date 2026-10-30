"""Guest customers: ordering without an account.

A fourth identity, alongside the three in staff_auth's note, and the weakest
of them on purpose:

  * core/customer_auth  signed-in customers
  * ADMIN_USERS         platform administrators   (core/platform_auth.py)
  * restaurant staff    credentials we issue      (core/staff_auth.py)
  * this module         guest customers

A guest proves nothing. There is no password to check and no address to
verify, so this token is not an authentication -- it is a capability, and the
only thing it can reach is the orders created while holding it. That is why a
guest gets a users row of its own per session rather than one looked up by
email: two people who type the same address are two guests, and neither can
read the other's pickup PIN.

The token is a bearer secret in a cookie, so it is httpOnly and never appears
in a URL, a log line or a redirect. Losing it is the expected end of a guest
session; it cannot be recovered, which is the honest cost of not having an
account and what the sign-in page says before anyone chooses it.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt

from app.config import settings

log = logging.getLogger(__name__)

TOKEN_TYPE = "customer_guest"
SESSION_COOKIE = "zenoeats_guest_session"

# A second, far narrower token: read one order, and nothing else. It is what
# makes the link in a confirmation email work, which for a guest is the only
# way back to their pickup PIN from a different device -- the cookie above
# lives in exactly one browser. Days, not minutes, because an order is
# collected hours later and the email may be opened long after that; scoped to
# a single order id, so a leaked one exposes that order and no other.
ORDER_TOKEN_TYPE = "guest_order_view"
ORDER_TOKEN_TTL_DAYS = 7


@dataclass(frozen=True)
class GuestPrincipal:
    user_id: UUID


def issue_session(user_id: UUID) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id),
            "typ": TOKEN_TYPE,
            "iat": now.timestamp(),
            "exp": now + timedelta(minutes=settings.GUEST_SESSION_TTL_MINUTES),
        },
        settings.SESSION_SECRET,
        algorithm="HS256",
    )


def verify_session(token: str) -> GuestPrincipal | None:
    try:
        claims = jwt.decode(
            token,
            settings.SESSION_SECRET,
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError:
        return None

    # The type claim is what keeps the four systems apart. All of ours are
    # signed with SESSION_SECRET, so without it a guest cookie would satisfy a
    # staff dependency -- and this is the one token anybody can mint for
    # themselves by asking.
    if claims.get("typ") != TOKEN_TYPE:
        return None

    try:
        return GuestPrincipal(user_id=UUID(str(claims.get("sub"))))
    except (ValueError, TypeError):
        return None


def issue_order_token(order_id: UUID) -> str:
    """A link to one order, for the confirmation email."""
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(order_id),
            "typ": ORDER_TOKEN_TYPE,
            "iat": now.timestamp(),
            "exp": now + timedelta(days=ORDER_TOKEN_TTL_DAYS),
        },
        settings.SESSION_SECRET,
        algorithm="HS256",
    )


def order_token_grants(token: str, order_id: UUID) -> bool:
    """Whether `token` is a live view token for exactly this order.

    Takes the order id from the route rather than returning the one in the
    token, so a caller cannot accidentally let the token choose which order
    it is about.
    """
    try:
        claims = jwt.decode(
            token,
            settings.SESSION_SECRET,
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError:
        return False
    if claims.get("typ") != ORDER_TOKEN_TYPE:
        return False
    return str(claims.get("sub")) == str(order_id)
