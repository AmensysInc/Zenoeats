"""Sign in with Google, talking to Google directly.

Clerk used to broker this. It is OAuth 2.0 against Google either way -- the
broker never removed the need to register a client with Google, it only hid
where the redirect went. So this is the same flow without the middleman:

    1. /customer/google/start    302 to Google, carrying a signed `state`
    2. the customer approves at Google
    3. /customer/google/callback Google returns a one-time `code`
    4. we exchange the code for an ID token, server to server
    5. we verify that token's signature against Google's JWKS
    6. the verified `sub` and `email` become one of our sessions

WHY THE CALLBACK IS ON THE ROOT DOMAIN. Google matches redirect URIs exactly:
no wildcards, no subdomain patterns. A platform that gives every restaurant its
own subdomain cannot register one callback per restaurant, so there is exactly
one, on the platform root, and the storefront the customer started from rides
along in `state` to be redirected back to afterwards.

That is also why the session cookie is set on the parent domain (see
api/v1/customer_accounts.py). It is the honest shape: a customer account has
always been one account for every storefront -- the same person ordering from
two restaurants is one row -- and a host-only cookie made them sign in again
per subdomain for no benefit.

WHAT `state` IS FOR. It is the CSRF defence for the redirect: without it,
somebody can hand a victim a callback URL carrying their own `code` and have
the victim's browser sign into the attacker's account. The value is signed
with SESSION_SECRET, short-lived, and must also match a cookie set when the
flow started -- so forging one needs both the signing key and a write to the
victim's cookie jar.
"""

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
import jwt
from jwt import PyJWKClient

from app.config import settings

log = logging.getLogger(__name__)

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
# Google's two accepted issuer spellings. Both are legitimate; a token
# claiming anything else is not Google's.
ISSUERS = ("https://accounts.google.com", "accounts.google.com")

STATE_COOKIE = "zenoeats_google_state"
STATE_TOKEN_TYPE = "google_oauth_state"
# The window between pressing the button and Google sending them back. Long
# enough to read a consent screen and pick an account, short enough that a
# state left in a browser is not a credential anyone can come back to.
STATE_TTL_MINUTES = 15

# Only what is needed to know who signed in. Not profile, not contacts.
SCOPES = "openid email"

_jwk_client: PyJWKClient | None = None


class GoogleError(Exception):
    """The sign-in did not complete. The message is for a customer."""


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str
    email_verified: bool
    full_name: str | None


def configured() -> bool:
    return bool(settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET)


def redirect_uri() -> str:
    """The one URI registered with Google. Must match theirs exactly."""
    root = settings.PLATFORM_URL_TEMPLATE.format(root_domain=settings.ROOT_DOMAIN)
    return f"{root}/api/v1/customer/google/callback"


def _jwks() -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = PyJWKClient(JWKS_URL, cache_keys=True)
    return _jwk_client


def issue_state(*, origin: str, next_path: str) -> tuple[str, str]:
    """A signed state, and the nonce the cookie must echo back.

    `origin` is the storefront the customer pressed the button on, so the
    callback -- which always lands on the root domain -- knows where to send
    them back to. `next_path` is where on that storefront.
    """
    nonce = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "typ": STATE_TOKEN_TYPE,
            "nonce": nonce,
            "origin": origin,
            "next": next_path,
            "iat": now.timestamp(),
            "exp": now + timedelta(minutes=STATE_TTL_MINUTES),
        },
        settings.SESSION_SECRET,
        algorithm="HS256",
    )
    return token, nonce


def read_state(token: str, cookie_nonce: str | None) -> tuple[str, str]:
    """(origin, next) from a state this server issued for this browser.

    Both halves are required. The signature proves we minted it; the cookie
    proves it was minted for the browser presenting it. A forged callback has
    neither.
    """
    try:
        claims = jwt.decode(
            token,
            settings.SESSION_SECRET,
            algorithms=["HS256"],
            options={"require": ["exp", "iat"]},
        )
    except jwt.PyJWTError as exc:
        raise GoogleError("That sign-in link has expired. Try again.") from exc

    if claims.get("typ") != STATE_TOKEN_TYPE:
        raise GoogleError("That sign-in link has expired. Try again.")
    if not cookie_nonce or not secrets.compare_digest(
        str(claims.get("nonce", "")), cookie_nonce
    ):
        # The signature was fine but this browser did not start the flow.
        log.warning("google callback state did not match the browser's cookie")
        raise GoogleError("That sign-in didn't complete. Try again.")

    return str(claims.get("origin", "")), str(claims.get("next", "/"))


def authorize_url(state: str) -> str:
    """Where to send the browser to ask Google who they are."""
    from urllib.parse import urlencode

    return AUTHORIZE_URL + "?" + urlencode(
        {
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "redirect_uri": redirect_uri(),
            "response_type": "code",
            "scope": SCOPES,
            "state": state,
            # Ask every time rather than silently reusing a session: this
            # button is on a shared-device storefront as often as a phone.
            "prompt": "select_account",
        }
    )


def exchange(code: str) -> GoogleIdentity:
    """Turn Google's one-time code into a verified identity.

    Server to server, carrying the client secret, so the browser never holds
    anything reusable. The ID token that comes back is then verified against
    Google's published keys rather than trusted because of where it arrived
    from -- the response body is not evidence on its own.
    """
    if not configured():
        raise GoogleError("Google sign-in is not available here.")

    try:
        with httpx.Client(timeout=10.0) as client:
            answer = client.post(
                TOKEN_URL,
                data={
                    "code": code,
                    "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                    "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                    "redirect_uri": redirect_uri(),
                    "grant_type": "authorization_code",
                },
            )
    except httpx.HTTPError as exc:
        log.warning("could not reach Google to exchange the code", exc_info=True)
        raise GoogleError("Couldn't reach Google. Try again.") from exc

    if answer.status_code != 200:
        # Google says why in the body; a customer cannot act on any of it.
        log.warning("google token exchange refused: %s", answer.text[:200])
        raise GoogleError("That sign-in didn't complete. Try again.")

    id_token = answer.json().get("id_token")
    if not id_token:
        log.warning("google token response carried no id_token")
        raise GoogleError("That sign-in didn't complete. Try again.")

    return verify_id_token(id_token)


def verify_id_token(id_token: str) -> GoogleIdentity:
    """The identity in a Google ID token, or raise.

    Audience is checked, because a token minted for some other application is
    not a sign-in here -- that check is what stops a token obtained by any
    other site using Google from being replayed against this one.
    """
    try:
        key = _jwks().get_signing_key_from_jwt(id_token).key
        claims = jwt.decode(
            id_token,
            key,
            algorithms=["RS256"],
            audience=settings.GOOGLE_OAUTH_CLIENT_ID,
            issuer=list(ISSUERS),
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except jwt.PyJWTError as exc:
        log.warning("google id token failed verification: %s", exc)
        raise GoogleError("That sign-in didn't complete. Try again.") from exc

    email = str(claims.get("email") or "").strip().lower()
    verified = bool(claims.get("email_verified"))
    if not email:
        raise GoogleError("Google didn't share an email address for that account.")
    if not verified:
        # An unverified address is not proof of anything, and this flow uses
        # the address to find an existing account. Accepting one would let
        # somebody claim an account by signing up to Google with its address.
        raise GoogleError(
            "That Google account's email isn't verified, so we can't use it to sign in."
        )

    # Not str(claims.get("name")): a missing claim is None, and str(None) is
    # the four-character string "None" -- which became the customer's name and
    # greeted them as "Welcome back, None".
    name = claims.get("name")
    return GoogleIdentity(
        subject=str(claims["sub"]),
        email=email,
        email_verified=verified,
        full_name=name.strip() or None if isinstance(name, str) else None,
    )
