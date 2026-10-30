"""Signing in with Google, Apple or Facebook, and being the account afterwards.

The rule these defend: one person, one account, however they arrive -- and an
account stays an account. Everything was once possible in the wrong direction:
a customer who registered and then pressed Google got an error page, one who
signed in and then signed out became a guest from last month.

The providers themselves are stubbed at social_auth.exchange, the one seam
where a real network call happens. Everything either side of it is real.
"""

import json
import uuid
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select

from app.config import settings
from app.core import customer_auth, social_auth
from app.db.session import system_session
from app.models import User
from tests.test_customer_accounts import (  # noqa: F401  (fixtures)
    _address,
    _client,
    fresh_rate_limit_budget,
    storefront,
    sweep_test_customers,
)

SESSION = "/api/v1/orders/session"
PASSWORD = "a-perfectly-long-password"


@pytest.fixture(autouse=True)
def all_providers_configured(monkeypatch):
    for name in (
        "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET",
        "APPLE_OAUTH_CLIENT_ID", "APPLE_OAUTH_TEAM_ID", "APPLE_OAUTH_KEY_ID",
        "APPLE_OAUTH_PRIVATE_KEY",
        "FACEBOOK_OAUTH_APP_ID", "FACEBOOK_OAUTH_APP_SECRET",
    ):
        monkeypatch.setattr(settings, name, "configured-for-a-test")
    from app.core.ratelimit import runtime_redis

    # This file presses more buttons than the limit allows from one address. The
    # limiter fails open without Redis, so a suite with none has nothing to clear.
    try:
        for key in runtime_redis().scan_iter(match="rl:social_start:*"):
            runtime_redis().delete(key)
    except Exception:  # noqa: BLE001, S110
        pass


def _identity(provider="google", email=None, subject=None, name=None):
    return social_auth.SocialIdentity(
        provider=provider,
        subject=subject or uuid.uuid4().hex,
        email=email,
        can_link_by_email=provider != social_auth.FACEBOOK,
        full_name=name,
    )


def _sign_in_with(client, monkeypatch, identity, *, agreed=False, next_path="/cart"):
    """The whole flow but the provider: start, then come back with a code."""
    monkeypatch.setattr(
        social_auth, "exchange", lambda provider, code, apple_user=None: identity
    )
    started = client.get(
        f"/api/v1/customer/{identity.provider}/start",
        params={"next": next_path, "agreed": agreed},
        follow_redirects=False,
    )
    assert started.status_code == 302, started.text
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    # Apple's state cookie is Secure, which httpx withholds on this plain-HTTP
    # test host where a browser on HTTPS would send it. Handed over explicitly.
    nonce = started.cookies.get(social_auth.STATE_COOKIE)
    return client.get(
        f"/api/v1/customer/{identity.provider}/callback",
        params={"state": state, "code": "one-time"},
        headers={"cookie": f"{social_auth.STATE_COOKIE}={nonce}"},
        follow_redirects=False,
    )


def _row(email: str) -> User | None:
    with system_session() as session:
        user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if user is not None:
            session.expunge(user)
        return user


def _register(client, email: str, **extra):
    res = client.post(
        "/api/v1/customer/register", json={"email": email, "password": PASSWORD, **extra}
    )
    assert res.status_code == 201, res.text
    return res


# ------------------------------------------------- "everything is a guest" ---

def test_a_new_social_sign_in_is_an_account_not_a_guest(monkeypatch):
    client = _client()
    email = _address()

    back = _sign_in_with(client, monkeypatch, _identity(email=email, name="Sam Rivera"))

    assert back.status_code == 303
    assert back.headers["location"].endswith("/cart")
    session = client.get(SESSION).json()
    assert session["is_guest"] is False
    assert session["email"] == email
    assert session["full_name"] == "Sam Rivera"


def test_a_guest_who_signs_in_with_a_provider_stops_being_a_guest(monkeypatch):
    """The reported bug. A guest cookie from earlier sits in the browser, and
    the sign-in on top of it has to win on every page, not just at checkout."""
    client = _client()
    client.post("/api/v1/orders/guest-session", json={"email": "earlier@example.com"})
    assert client.get(SESSION).json()["is_guest"] is True

    email = _address()
    _sign_in_with(client, monkeypatch, _identity(email=email))

    session = client.get(SESSION).json()
    assert session["is_guest"] is False
    assert session["email"] == email


def test_a_guest_who_signs_in_with_a_password_stops_being_a_guest():
    client = _client()
    email = _address()
    _register(client, email)
    client.post("/api/v1/customer/logout")

    client.post("/api/v1/orders/guest-session", json={"email": "earlier@example.com"})
    assert client.post(
        "/api/v1/customer/login", json={"email": email, "password": PASSWORD}
    ).status_code == 200

    assert client.get(SESSION).json()["is_guest"] is False


def test_signing_out_does_not_turn_an_account_back_into_a_guest(monkeypatch):
    """The guest cookie underneath used to take over as soon as the account's
    was gone, so "sign out" left the customer ordering as last month's guest."""
    client = _client()
    client.post("/api/v1/orders/guest-session", json={"email": "earlier@example.com"})
    _sign_in_with(client, monkeypatch, _identity(email=_address()))

    assert client.post("/api/v1/customer/logout").status_code == 204

    assert client.get(SESSION).status_code == 401


def test_sign_out_is_final_for_a_social_account(monkeypatch):
    """Nothing but our cookie stands behind the session any more, so there is no
    second session left somewhere to bring them straight back."""
    client = _client()
    _sign_in_with(client, monkeypatch, _identity(email=_address()))
    client.post("/api/v1/customer/logout")
    assert client.get(SESSION).status_code == 401


def test_a_bearer_token_is_no_way_in_any_more():
    res = _client().get(SESSION, headers={"Authorization": "Bearer user_someone"})
    assert res.status_code == 401


# ----------------------------------------------------- one person, one row ---

def test_coming_back_is_the_same_account(monkeypatch):
    client = _client()
    identity = _identity(email=_address())
    _sign_in_with(client, monkeypatch, identity)
    first = _row(identity.email)
    client.post("/api/v1/customer/logout")

    _sign_in_with(client, monkeypatch, identity)

    assert _row(identity.email).id == first.id


def test_the_subject_finds_them_after_their_address_changed(monkeypatch):
    client = _client()
    subject, old = uuid.uuid4().hex, _address()
    _sign_in_with(client, monkeypatch, _identity(email=old, subject=subject))
    first = _row(old)

    new = _address()
    client = _client()
    _sign_in_with(client, monkeypatch, _identity(email=new, subject=subject))

    # Their own email stays theirs to change; the provider's does not overrule it.
    assert client.get(SESSION).json()["email"] == old
    assert _row(new) is None
    assert _row(old).id == first.id


@pytest.mark.parametrize("provider", [social_auth.GOOGLE, social_auth.APPLE])
def test_registering_then_pressing_a_provider_is_one_account(monkeypatch, provider):
    """This crashed. The insert hit the unique address, the recovery looked for
    an id that did not exist, and the customer got a 500 -- and then "can't
    reach sign-in" at checkout, even with the right password."""
    client = _client()
    email = _address()
    _register(client, email)
    registered = _row(email)

    back = _sign_in_with(_client(), monkeypatch, _identity(provider, email=email))

    assert back.status_code == 303
    assert "error=" not in back.headers["location"]
    assert _row(email).id == registered.id


def test_an_address_someone_else_registered_cannot_keep_the_account(monkeypatch):
    """Registering verifies nothing. An attacker who registered the victim's
    address first loses the password and their session the moment the victim
    proves the address through a provider."""
    attacker, email = _client(), _address()
    _register(attacker, email)
    assert attacker.get(SESSION).status_code == 200

    victim = _client()
    _sign_in_with(victim, monkeypatch, _identity(email=email))

    assert _row(email).password_hash is None
    assert attacker.get(SESSION).status_code == 401
    assert _client().post(
        "/api/v1/customer/login", json={"email": email, "password": PASSWORD}
    ).status_code == 401
    assert victim.get(SESSION).status_code == 200


def test_google_then_apple_with_the_same_address_is_one_account(monkeypatch):
    email = _address()
    _sign_in_with(_client(), monkeypatch, _identity("google", email=email))
    first = _row(email)

    _sign_in_with(_client(), monkeypatch, _identity("apple", email=email))

    after = _row(email)
    assert after.id == first.id
    assert after.google_subject and after.apple_subject


def test_facebook_cannot_open_an_existing_account_by_address(monkeypatch):
    """Facebook does not promise the address is verified, so it does not get to
    claim anyone's account -- and the customer is told what to do instead."""
    email = _address()
    _register(_client(), email)

    client = _client()
    back = _sign_in_with(client, monkeypatch, _identity("facebook", email=email))

    assert "error=social_email_in_use" in back.headers["location"]
    assert _row(email).facebook_subject is None
    assert _row(email).password_hash is not None
    assert client.get(SESSION).status_code == 401


def test_facebook_can_create_an_account_and_come_back_to_it(monkeypatch):
    client, email = _client(), _address()
    identity = _identity("facebook", email=email)
    _sign_in_with(client, monkeypatch, identity)
    assert client.get(SESSION).json()["is_guest"] is False
    client.post("/api/v1/customer/logout")

    _sign_in_with(client, monkeypatch, identity)
    assert client.get(SESSION).json()["email"] == email


def test_no_address_at_all_says_so(monkeypatch):
    back = _sign_in_with(_client(), monkeypatch, _identity("facebook", email=None))
    assert "error=social_no_email" in back.headers["location"]
    # The cart is still where they were going, once they try another way.
    assert "next=%2Fcart" in back.headers["location"]


def test_a_returning_customer_needs_no_address(monkeypatch):
    """Apple often omits it after the first time; the subject is enough."""
    subject, email = uuid.uuid4().hex, _address()
    _sign_in_with(_client(), monkeypatch, _identity("apple", email=email, subject=subject))

    client = _client()
    _sign_in_with(client, monkeypatch, _identity("apple", email=None, subject=subject))
    assert client.get(SESSION).json()["email"] == email


def test_a_closed_account_frees_the_provider_identity(monkeypatch):
    """Closing promises the sign-in goes. The same Google account coming back
    is a new customer, not a refusal or the old row."""
    from app.services import customer_records

    subject, email = uuid.uuid4().hex, _address()
    _sign_in_with(_client(), monkeypatch, _identity(email=email, subject=subject))
    closed_id = _row(email).id
    with system_session() as session:
        customer_records.close_account(session, session.get(User, closed_id))

    client = _client()
    _sign_in_with(client, monkeypatch, _identity(email=email, subject=subject))

    assert client.get(SESSION).json()["email"] == email
    assert _row(email).id != closed_id


# --------------------------------------------------------- password for all ---

def test_a_social_account_can_ask_for_a_password(monkeypatch, queued_emails):
    """It used to be told a link was coming, and then none did."""
    email = _address()
    _sign_in_with(_client(), monkeypatch, _identity(email=email))

    assert _client().post(
        "/api/v1/customer/password/forgot", json={"email": email}
    ).status_code == 204

    assert [kind for name, (kind, *_rest) in queued_emails if name == "send_account_email"
            and kind == "customer_password_reset"]

    user = _row(email)
    token = customer_auth.issue_reset_token(user.id, user.password_hash)
    client = _client()
    assert client.post(
        "/api/v1/customer/password/reset", json={"token": token, "password": PASSWORD}
    ).status_code == 204
    assert client.post(
        "/api/v1/customer/login", json={"email": email, "password": PASSWORD}
    ).status_code == 200


# ------------------------------------------------------------------- terms ---

def test_ticking_the_box_on_sign_up_is_not_asked_again():
    client = _client()
    _register(client, _address(), terms_accepted=True)
    assert client.get(SESSION).json()["terms_accepted"] is True


def test_a_provider_sign_up_carries_the_ticked_box(monkeypatch):
    client = _client()
    _sign_in_with(client, monkeypatch, _identity(email=_address()), agreed=True)
    assert client.get(SESSION).json()["terms_accepted"] is True


def test_a_provider_sign_in_with_no_box_is_asked_by_the_app(monkeypatch):
    client = _client()
    _sign_in_with(client, monkeypatch, _identity(email=_address()), agreed=False)
    assert client.get(SESSION).json()["terms_accepted"] is False


# ------------------------------------------------------------- the protocol ---

def test_only_configured_providers_are_offered(monkeypatch):
    monkeypatch.setattr(settings, "APPLE_OAUTH_KEY_ID", "")
    monkeypatch.setattr(settings, "FACEBOOK_OAUTH_APP_SECRET", "")
    res = _client().get("/api/v1/customer/sign-in-options")
    assert res.json() == {"providers": ["google"]}


def test_an_unconfigured_provider_is_refused_cleanly(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "")
    res = _client().get("/api/v1/customer/google/start", follow_redirects=False)
    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "SOCIAL_UNAVAILABLE"


def test_an_unknown_provider_is_not_found():
    assert _client().get("/api/v1/customer/myspace/start").status_code == 404


def test_a_callback_without_state_is_refused():
    res = _client().get("/api/v1/customer/google/callback?code=x", follow_redirects=False)
    assert res.status_code == 400


def test_a_state_this_browser_did_not_start_is_refused():
    """Without the cookie check, an attacker could hand a victim a callback URL
    carrying their own code and sign the victim's browser into their account."""
    state, _nonce = social_auth.issue_state(provider="google", origin="a.example", next_path="/")
    with pytest.raises(social_auth.SocialError):
        social_auth.read_state("google", state, None)
    with pytest.raises(social_auth.SocialError):
        social_auth.read_state("google", state, "not-the-nonce")

    state, nonce = social_auth.issue_state(
        provider="google", origin="a.example", next_path="/cart", agreed=True
    )
    read = social_auth.read_state("google", state, nonce)
    assert (read.origin, read.next_path, read.agreed) == ("a.example", "/cart", True)


def test_one_providers_state_is_no_good_at_another():
    state, nonce = social_auth.issue_state(provider="google", origin="a.example", next_path="/")
    with pytest.raises(social_auth.SocialError):
        social_auth.read_state("apple", state, nonce)


def test_a_session_token_is_not_a_state():
    """Everything of ours is signed with SESSION_SECRET; `typ` separates them."""
    token = customer_auth.issue_session(uuid.uuid4())
    _state, nonce = social_auth.issue_state(provider="google", origin="a.example", next_path="/")
    with pytest.raises(social_auth.SocialError):
        social_auth.read_state("google", token, nonce)


def test_the_callback_comes_back_to_the_storefront_it_started_on(monkeypatch):
    client = _client()
    back = _sign_in_with(client, monkeypatch, _identity(email=_address()), next_path="/checkout")
    assert urlparse(back.headers["location"]).hostname == urlparse(str(client.base_url)).hostname
    assert urlparse(back.headers["location"]).path == "/checkout"


def test_a_next_that_is_a_url_stays_on_this_host(monkeypatch):
    client = _client()
    back = _sign_in_with(
        client, monkeypatch, _identity(email=_address()), next_path="https://evil.example/x"
    )
    assert urlparse(back.headers["location"]).hostname == urlparse(str(client.base_url)).hostname


def test_the_state_cookie_reaches_the_callback_on_the_root_domain(monkeypatch):
    """The flow starts on the restaurant's address and finishes on the root, so a
    host-only cookie is never sent back and every sign-in fails the nonce check.
    This is why direct Google could not have worked as it stood."""
    monkeypatch.setattr(settings, "ROOT_DOMAIN", "zenoeats.com")
    started = _client().get(
        "/api/v1/customer/google/start",
        headers={"host": "jrs.zenoeats.com"},
        follow_redirects=False,
    )
    cookie = started.headers["set-cookie"].lower()
    assert "domain=.zenoeats.com" in cookie
    assert "samesite=lax" in cookie


def test_apples_state_cookie_survives_its_cross_site_post():
    """Apple returns by POST. SameSite=Lax would leave the cookie behind."""
    assert social_auth.state_cookie_samesite(social_auth.APPLE) == "none"
    assert social_auth.state_cookie_samesite(social_auth.GOOGLE) == "lax"
    assert social_auth.state_cookie_samesite(social_auth.FACEBOOK) == "lax"

    started = _client().get("/api/v1/customer/apple/start", follow_redirects=False)
    cookie = started.headers["set-cookie"].lower()
    assert "samesite=none" in cookie and "secure" in cookie


def test_apple_comes_back_by_post_with_a_name_it_sends_once(monkeypatch):
    client, email = _client(), _address()
    apple_user = json.dumps({"name": {"firstName": "Sam", "lastName": "Rivera"}})
    captured = {}

    def exchange(provider, code, user=None):
        captured.update(provider=provider, code=code, user=user)
        return _identity("apple", email=email, name=social_auth._apple_name(user))

    monkeypatch.setattr(social_auth, "exchange", exchange)
    started = client.get("/api/v1/customer/apple/start", follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    nonce = started.cookies.get(social_auth.STATE_COOKIE)
    assert nonce

    # httpx withholds the Secure cookie on this plain-HTTP test host; a browser
    # on HTTPS sends it. So it is handed over explicitly.
    back = client.post(
        "/api/v1/customer/apple/callback",
        data={"state": state, "code": "one-time", "user": apple_user},
        headers={"cookie": f"{social_auth.STATE_COOKIE}={nonce}"},
        follow_redirects=False,
    )

    # 303: a browser must not repeat the POST at the page it is sent to.
    assert back.status_code == 303, back.text
    assert captured == {"provider": "apple", "code": "one-time", "user": apple_user}
    assert _row(email).full_name == "Sam Rivera"


def test_cancelling_at_the_provider_lands_back_on_sign_in(monkeypatch):
    client = _client()
    started = client.get(
        "/api/v1/customer/google/start", params={"next": "/cart"}, follow_redirects=False
    )
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    back = client.get(
        "/api/v1/customer/google/callback",
        params={"state": state, "error": "access_denied"},
        follow_redirects=False,
    )
    assert "/account/sign-in?error=social_failed" in back.headers["location"]
    assert "next=%2Fcart" in back.headers["location"]


# ------------------------------------------------- what each provider claims ---

def _google(monkeypatch, **claims):
    class _Key:
        key = "stub"

    monkeypatch.setattr(social_auth, "_jwks", lambda _url: type("J", (), {
        "get_signing_key_from_jwt": staticmethod(lambda _t: _Key())
    })())
    monkeypatch.setattr(social_auth.jwt, "decode", lambda *a, **k: claims)
    return social_auth.verify_google_id_token("pretend-token")


def test_an_unverified_google_address_cannot_claim_an_account(monkeypatch):
    with pytest.raises(social_auth.SocialError) as refused:
        _google(monkeypatch, sub="s", email="someone@example.com", email_verified=False)
    assert refused.value.code == "social_unverified"

    identity = _google(monkeypatch, sub="s", email="Someone@Example.com", email_verified=True)
    # Lower-cased, because it is matched against stored addresses.
    assert identity.email == "someone@example.com"
    assert identity.can_link_by_email is True


def test_a_google_account_with_no_name_has_no_name(monkeypatch):
    """Not the string "None", which greeted a customer as "Welcome back, None"."""
    assert _google(
        monkeypatch, sub="s", email="a@example.com", email_verified=True
    ).full_name is None
    assert _google(
        monkeypatch, sub="s", email="a@example.com", email_verified=True, name="   "
    ).full_name is None
    assert _google(
        monkeypatch, sub="s", email="a@example.com", email_verified=True, name="  Sam Rivera  "
    ).full_name == "Sam Rivera"


def test_apple_sends_verified_as_a_string(monkeypatch):
    class _Key:
        key = "stub"

    monkeypatch.setattr(social_auth, "_jwks", lambda _url: type("J", (), {
        "get_signing_key_from_jwt": staticmethod(lambda _t: _Key())
    })())
    monkeypatch.setattr(social_auth.jwt, "decode", lambda *a, **k: {
        "sub": "apple-1", "email": "x@privaterelay.appleid.com", "email_verified": "true",
    })
    assert social_auth.verify_apple_id_token("t").email == "x@privaterelay.appleid.com"

    monkeypatch.setattr(social_auth.jwt, "decode", lambda *a, **k: {
        "sub": "apple-1", "email": "x@example.com", "email_verified": "false",
    })
    assert social_auth.verify_apple_id_token("t").email is None


@pytest.mark.parametrize(
    "posted, name",
    [
        (None, None),
        ("not json", None),
        ('["a list"]', None),
        ('{"name": {"firstName": "Sam"}}', "Sam"),
        ('{"name": {"firstName": " Sam ", "lastName": "Rivera"}}', "Sam Rivera"),
    ],
)
def test_apples_posted_name_is_read_defensively(posted, name):
    assert social_auth._apple_name(posted) == name


def test_apples_client_secret_is_signed_with_the_developer_key(monkeypatch):
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    # As it arrives from an environment file: one line, with literal \n in it.
    monkeypatch.setattr(settings, "APPLE_OAUTH_PRIVATE_KEY", pem.replace("\n", "\\n"))
    monkeypatch.setattr(settings, "APPLE_OAUTH_TEAM_ID", "TEAM123")
    monkeypatch.setattr(settings, "APPLE_OAUTH_KEY_ID", "KEY123")
    monkeypatch.setattr(settings, "APPLE_OAUTH_CLIENT_ID", "com.zenoeats.web")

    secret = social_auth.apple_client_secret()

    assert jwt.get_unverified_header(secret)["kid"] == "KEY123"
    claims = jwt.decode(
        secret, key.public_key(), algorithms=["ES256"], audience=social_auth.APPLE_ISSUER,
    )
    assert (claims["iss"], claims["sub"]) == ("TEAM123", "com.zenoeats.web")


def test_a_facebook_address_can_never_link():
    identity = social_auth.facebook_identity(
        {"id": "fb-1", "name": "Sam", "email": "Sam@Example.com"}
    )
    assert identity.email == "sam@example.com"
    assert identity.can_link_by_email is False
    assert social_auth.facebook_identity({"id": "fb-2"}).email is None
    with pytest.raises(social_auth.SocialError):
        social_auth.facebook_identity({"name": "No id"})
