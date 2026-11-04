"""Sign in with Google, Apple or Facebook, talking to each of them directly.

Each is OAuth 2.0 with its own spelling, and all three end the same way: a
provider-issued subject and, usually, an email address, which become one of our
own sessions (core/customer_auth.py). The flow:

    1. /customer/<provider>/start     302 to the provider, with a signed `state`
    2. the customer approves there
    3. /customer/<provider>/callback  the provider returns a one-time `code`
    4. we exchange the code, server to server, carrying our secret
    5. we check who it says this is -- a signed ID token for Google and Apple,
       the Graph API for Facebook
    6. the identity becomes a customer account (api/v1/customer_accounts.py)

WHY THE CALLBACK IS ON THE ROOT DOMAIN. Providers match redirect URIs exactly:
no wildcards, no subdomain patterns. A platform that gives every restaurant its
own subdomain cannot register one callback per restaurant, so there is exactly
one per provider, on the platform root, and the storefront the customer started
from rides along in `state` to be redirected back to afterwards. The state
cookie and the session cookie are both set on the parent domain for the same
reason: the flow starts on one host and finishes on another.

WHAT `state` IS FOR. It is the CSRF defence for the redirect: without it,
somebody can hand a victim a callback URL carrying their own `code` and have
the victim's browser sign into the attacker's account. The value is signed
with SESSION_SECRET, short-lived, names the provider it was issued for, and must
also match a cookie set when the flow started -- so forging one needs both the
signing key and a write to the victim's cookie jar.

WHY APPLE IS DIFFERENT. Apple sends the browser back with a cross-site POST
(`response_mode=form_post`, compulsory once a name or email is asked for), and
it hands over the customer's name exactly once, in that POST, the first time
they ever approve this app. SameSite=Lax withholds cookies from a cross-site
POST, so Apple's state cookie alone is SameSite=None; see state_cookie_samesite.
Apple's client secret is not a stored string either: it is a short JWT we sign
with the private key from the Apple Developer portal.

WHY FACEBOOK IS DIFFERENT. There is no ID token in this flow, so identity comes
from asking the Graph API with the token just issued to our app. And Facebook
does not promise the address it returns was verified, so that address can create
a new account but must not open an existing one; see can_link_by_email.
"""

import hashlib
import hmac
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient

from app.config import settings

log = logging.getLogger(__name__)

GOOGLE = "google"
APPLE = "apple"
FACEBOOK = "facebook"
# Page order: the order the buttons are drawn in.
PROVIDERS = (GOOGLE, APPLE, FACEBOOK)

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
# Google's two accepted issuer spellings. Both are legitimate; a token
# claiming anything else is not Google's.
GOOGLE_ISSUERS = ["https://accounts.google.com", "accounts.google.com"]

APPLE_ISSUER = "https://appleid.apple.com"
APPLE_AUTHORIZE_URL = f"{APPLE_ISSUER}/auth/authorize"
APPLE_TOKEN_URL = f"{APPLE_ISSUER}/auth/token"
APPLE_JWKS_URL = f"{APPLE_ISSUER}/auth/keys"

FACEBOOK_GRAPH_VERSION = "v23.0"
FACEBOOK_AUTHORIZE_URL = f"https://www.facebook.com/{FACEBOOK_GRAPH_VERSION}/dialog/oauth"
FACEBOOK_GRAPH_URL = f"https://graph.facebook.com/{FACEBOOK_GRAPH_VERSION}"

STATE_COOKIE = "zenoeats_social_state"
STATE_TOKEN_TYPE = "social_oauth_state"
# The window between pressing the button and the provider sending them back.
# Long enough to read a consent screen and pick an account, short enough that a
# state left in a browser is not a credential anyone can come back to.
STATE_TTL_MINUTES = 15

_jwk_clients: dict[str, PyJWKClient] = {}


class SocialError(Exception):
    """The sign-in did not complete.

    `code` is what the sign-in page is told, and it words the reason itself
    (RETURN_ERRORS in web/login/customer-shared.ts). The message is for the log.
    """

    def __init__(self, message: str, code: str = "social_failed"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SocialIdentity:
    provider: str
    subject: str
    # None when the provider did not share one. Survivable for someone who has
    # signed in this way before, since the subject finds them.
    email: str | None
    # Whether the provider vouches for the address strongly enough that it may
    # open an existing account.
    can_link_by_email: bool
    full_name: str | None


@dataclass(frozen=True)
class SocialState:
    origin: str
    next_path: str
    # Whether the terms box was ticked on the page the flow started from.
    agreed: bool


def configured(provider: str) -> bool:
    if provider == GOOGLE:
        return bool(settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET)
    if provider == APPLE:
        return bool(
            settings.APPLE_OAUTH_CLIENT_ID and settings.APPLE_OAUTH_TEAM_ID
            and settings.APPLE_OAUTH_KEY_ID and settings.APPLE_OAUTH_PRIVATE_KEY
        )
    if provider == FACEBOOK:
        return bool(settings.FACEBOOK_OAUTH_APP_ID and settings.FACEBOOK_OAUTH_APP_SECRET)
    return False


def available() -> list[str]:
    """The providers with credentials behind them. A button that cannot work
    should not be drawn."""
    return [provider for provider in PROVIDERS if configured(provider)]


def redirect_uri(provider: str) -> str:
    """The one URI registered with this provider. Must match theirs exactly."""
    root = settings.PLATFORM_URL_TEMPLATE.format(root_domain=settings.ROOT_DOMAIN)
    return f"{root}/api/v1/customer/{provider}/callback"


def state_cookie_samesite(provider: str) -> str:
    """Lax wherever Lax works, which is everywhere but Apple.

    Google and Facebook come back by a top-level GET, which Lax permits. Apple
    comes back by a cross-site POST, which it does not: with Lax the cookie
    stays behind, the nonce cannot be checked, and every Apple sign-in fails.
    """
    return "none" if provider == APPLE else "lax"


def _jwks(url: str) -> PyJWKClient:
    if url not in _jwk_clients:
        _jwk_clients[url] = PyJWKClient(url, cache_keys=True)
    return _jwk_clients[url]


def issue_state(
    *, provider: str, origin: str, next_path: str, agreed: bool = False
) -> tuple[str, str]:
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
            "provider": provider,
            "nonce": nonce,
            "origin": origin,
            "next": next_path,
            "agreed": agreed,
            "iat": now.timestamp(),
            "exp": now + timedelta(minutes=STATE_TTL_MINUTES),
        },
        settings.SESSION_SECRET,
        algorithm="HS256",
    )
    return token, nonce


def read_state(provider: str, token: str, cookie_nonce: str | None) -> SocialState:
    """What a state this server issued for this browser and provider says.

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
        raise SocialError("the state did not verify") from exc

    if claims.get("typ") != STATE_TOKEN_TYPE or claims.get("provider") != provider:
        raise SocialError("the state is not one of ours for this provider")
    if not cookie_nonce or not secrets.compare_digest(
        str(claims.get("nonce", "")), cookie_nonce
    ):
        # The signature was fine but this browser did not start the flow.
        log.warning("%s callback state did not match the browser's cookie", provider)
        raise SocialError("the state did not match this browser")

    return SocialState(
        origin=str(claims.get("origin", "")),
        next_path=str(claims.get("next", "/")),
        agreed=claims.get("agreed") is True,
    )


def authorize_url(provider: str, state: str) -> str:
    """Where to send the browser to ask the provider who they are."""
    if provider == GOOGLE:
        return GOOGLE_AUTHORIZE_URL + "?" + urlencode(
            {
                "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                "redirect_uri": redirect_uri(GOOGLE),
                "response_type": "code",
                # `profile` because the name is in the ID token only with it.
                # Contacts, Drive and the rest stay unasked for.
                "scope": "openid email profile",
                "state": state,
                # Ask every time rather than silently reusing a session: this
                # button is on a shared-device storefront as often as a phone.
                "prompt": "select_account",
            }
        )
    if provider == APPLE:
        return APPLE_AUTHORIZE_URL + "?" + urlencode(
            {
                "client_id": settings.APPLE_OAUTH_CLIENT_ID,
                "redirect_uri": redirect_uri(APPLE),
                "response_type": "code",
                "response_mode": "form_post",
                "scope": "name email",
                "state": state,
            }
        )
    if provider == FACEBOOK:
        return FACEBOOK_AUTHORIZE_URL + "?" + urlencode(
            {
                "client_id": settings.FACEBOOK_OAUTH_APP_ID,
                "redirect_uri": redirect_uri(FACEBOOK),
                "response_type": "code",
                "scope": "email,public_profile",
                "state": state,
            }
        )
    raise SocialError(f"no such provider: {provider}")


def exchange(provider: str, code: str, apple_user: str | None = None) -> SocialIdentity:
    """Turn a provider's one-time code into a verified identity.

    Server to server, carrying the secret, so the browser never holds anything
    reusable. `apple_user` is the JSON Apple posts alongside the code on the
    first sign-in, and is the only place it ever says the customer's name.
    """
    if not configured(provider):
        raise SocialError(f"{provider} sign-in is not configured")
    if provider == GOOGLE:
        return _exchange_google(code)
    if provider == APPLE:
        return _exchange_apple(code, apple_user)
    return _exchange_facebook(code)


def _post_for_token(provider: str, url: str, data: dict) -> dict:
    try:
        with httpx.Client(timeout=10.0) as client:
            answer = client.post(url, data=data)
    except httpx.HTTPError as exc:
        log.warning("could not reach %s to exchange the code", provider, exc_info=True)
        raise SocialError(f"could not reach {provider}") from exc

    if answer.status_code != 200:
        # They say why in the body; a customer cannot act on any of it.
        log.warning("%s token exchange refused: %s", provider, answer.text[:200])
        raise SocialError(f"{provider} refused the code")
    return answer.json()


def _exchange_google(code: str) -> SocialIdentity:
    tokens = _post_for_token(
        GOOGLE,
        GOOGLE_TOKEN_URL,
        {
            "code": code,
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "redirect_uri": redirect_uri(GOOGLE),
            "grant_type": "authorization_code",
        },
    )
    return verify_google_id_token(_id_token_from(GOOGLE, tokens))


def _id_token_from(provider: str, tokens: dict) -> str:
    id_token = tokens.get("id_token")
    if not id_token:
        log.warning("%s token response carried no id_token", provider)
        raise SocialError(f"{provider} sent no id_token")
    return id_token


def _verified_claims(
    provider: str, id_token: str, jwks_url: str, audience: str, issuer: list[str] | str
) -> dict:
    """An ID token's claims, once its signature, audience and issuer check out.

    Audience is checked, because a token minted for some other application is
    not a sign-in here -- that check is what stops a token obtained by any
    other site using the provider from being replayed against this one. The
    response body it came in is not evidence on its own.
    """
    try:
        key = _jwks(jwks_url).get_signing_key_from_jwt(id_token).key
        return jwt.decode(
            id_token,
            key,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except jwt.PyJWTError as exc:
        log.warning("%s id token failed verification: %s", provider, exc)
        raise SocialError(f"{provider} id token failed verification") from exc


def _clean_email(value: object) -> str | None:
    return str(value or "").strip().lower() or None


def verify_google_id_token(id_token: str) -> SocialIdentity:
    claims = _verified_claims(
        GOOGLE, id_token, GOOGLE_JWKS_URL, settings.GOOGLE_OAUTH_CLIENT_ID, GOOGLE_ISSUERS
    )
    email = _clean_email(claims.get("email"))
    if not email:
        raise SocialError("Google shared no address", "social_no_email")
    if not claims.get("email_verified"):
        # An unverified address is not proof of anything, and this flow uses
        # the address to find an existing account. Accepting one would let
        # somebody claim an account by signing up to Google with its address.
        raise SocialError("Google has not verified that address", "social_unverified")

    # Not str(claims.get("name")): a missing claim is None, and str(None) is
    # the four-character string "None" -- which became the customer's name and
    # greeted them as "Welcome back, None".
    name = claims.get("name")
    return SocialIdentity(
        provider=GOOGLE,
        subject=str(claims["sub"]),
        email=email,
        can_link_by_email=True,
        full_name=name.strip() or None if isinstance(name, str) else None,
    )


def apple_client_secret() -> str:
    """Apple's client secret, which we sign ourselves and Apple then verifies
    against the public half of the key. Minutes, not the six months it allows:
    one is minted per sign-in, so nothing long-lived is ever on the wire."""
    now = datetime.now(timezone.utc)
    # An environment file holds the .p8 on one line with literal \n in it.
    private_key = settings.APPLE_OAUTH_PRIVATE_KEY.replace("\\n", "\n")
    return jwt.encode(
        {
            "iss": settings.APPLE_OAUTH_TEAM_ID,
            "sub": settings.APPLE_OAUTH_CLIENT_ID,
            "aud": APPLE_ISSUER,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        private_key,
        algorithm="ES256",
        headers={"kid": settings.APPLE_OAUTH_KEY_ID},
    )


def _exchange_apple(code: str, apple_user: str | None) -> SocialIdentity:
    tokens = _post_for_token(
        APPLE,
        APPLE_TOKEN_URL,
        {
            "code": code,
            "client_id": settings.APPLE_OAUTH_CLIENT_ID,
            "client_secret": apple_client_secret(),
            "redirect_uri": redirect_uri(APPLE),
            "grant_type": "authorization_code",
        },
    )
    return verify_apple_id_token(_id_token_from(APPLE, tokens), apple_user)


def verify_apple_id_token(id_token: str, apple_user: str | None = None) -> SocialIdentity:
    claims = _verified_claims(
        APPLE, id_token, APPLE_JWKS_URL, settings.APPLE_OAUTH_CLIENT_ID, APPLE_ISSUER
    )
    # Apple sends this as the string "true" as readily as the boolean.
    verified = str(claims.get("email_verified")).lower() == "true"
    return SocialIdentity(
        provider=APPLE,
        subject=str(claims["sub"]),
        email=_clean_email(claims.get("email")) if verified else None,
        can_link_by_email=True,
        full_name=_apple_name(apple_user),
    )


def _apple_name(apple_user: str | None) -> str | None:
    """The name Apple posts alongside the code, once, the first time.

    Unsigned, and treated accordingly: it fills in a display name and nothing
    decides anything by it.
    """
    if not apple_user:
        return None
    try:
        name = json.loads(apple_user).get("name") or {}
        parts = [name.get("firstName"), name.get("lastName")]
    except (ValueError, AttributeError):
        return None
    return " ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())[:160] or None


def _exchange_facebook(code: str) -> SocialIdentity:
    secret = settings.FACEBOOK_OAUTH_APP_SECRET
    tokens = _post_for_token(
        FACEBOOK,
        f"{FACEBOOK_GRAPH_URL}/oauth/access_token",
        {
            "code": code,
            "client_id": settings.FACEBOOK_OAUTH_APP_ID,
            "client_secret": secret,
            "redirect_uri": redirect_uri(FACEBOOK),
        },
    )
    access_token = tokens.get("access_token")
    if not access_token:
        log.warning("facebook token response carried no access_token")
        raise SocialError("facebook sent no access_token")

    try:
        with httpx.Client(timeout=10.0) as client:
            answer = client.get(
                f"{FACEBOOK_GRAPH_URL}/me",
                params={
                    "fields": "id,name,email",
                    "access_token": access_token,
                    # Proof this call comes from the holder of the app secret.
                    # Required when "Require App Secret" is on, harmless when off.
                    "appsecret_proof": hmac.new(
                        secret.encode(), access_token.encode(), hashlib.sha256
                    ).hexdigest(),
                },
            )
    except httpx.HTTPError as exc:
        # Never exc_info here: the exception's URL carries the access token.
        log.warning("could not reach facebook to read the profile: %s", type(exc).__name__)
        raise SocialError("could not reach facebook") from exc

    if answer.status_code != 200:
        log.warning("facebook refused the profile read: %s", answer.status_code)
        raise SocialError("facebook refused the profile read")
    return facebook_identity(answer.json())


def facebook_identity(profile: dict) -> SocialIdentity:
    subject = str(profile.get("id") or "")
    if not subject:
        raise SocialError("facebook sent no id")
    name = profile.get("name")
    return SocialIdentity(
        provider=FACEBOOK,
        subject=subject,
        # Some Facebook accounts have none: signed up with a phone number.
        email=_clean_email(profile.get("email")),
        # Facebook does not promise this address was verified.
        can_link_by_email=False,
        full_name=name.strip()[:160] or None if isinstance(name, str) else None,
    )
