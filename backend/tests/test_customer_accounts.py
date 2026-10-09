"""Customer accounts with credentials we hold, rather than Clerk's.

What is worth testing here is not that a correct password works -- it is the
set of things that must be indistinguishable or impossible:

  * an unknown address and a wrong password, in wording and status code
  * a customer cookie reaching a staff or admin endpoint
  * two accounts on one address
  * a guest's users row being caught by the one-account-per-address rule,
    which would break checkout for everybody who orders without an account
"""

import uuid

import pytest
from sqlalchemy import text

from app.core import customer_auth, guest_auth, staff_auth
from app.db.session import system_session
from app.models import UserKind

# The restaurant these tests order from, created by the fixture below.
#
# It used to be the seeded "spicehouse", which works on a developer's machine
# and nowhere else: CI migrates an empty database and never seeds it, so every
# endpoint that resolves a tenant answered RESTAURANT_NOT_FOUND. A test that
# depends on seed data is a test that only passes where somebody has already
# run `make seed`.
_host: list[str] = []


def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app, base_url=f"http://{_host[0]}")


@pytest.fixture(autouse=True)
def storefront():
    """An active restaurant for the Host header to resolve to.

    Autouse, because every test in this file either orders from a restaurant
    or signs in on one's storefront. Its own slug per run, so a leftover row
    from a previous run cannot be picked up instead.
    """
    import uuid as _uuid

    from app.db.session import system_session, tenant_session
    from app.models import Restaurant, RestaurantStatus

    slug = f"accounts-{_uuid.uuid4().hex[:8]}"
    with system_session() as session:
        restaurant = Restaurant(
            slug=slug, name="Accounts Test", status=RestaurantStatus.ACTIVE.value,
            timezone="UTC", currency="USD",
        )
        session.add(restaurant)
        session.flush()
        rid = restaurant.id

    _host.append(f"{slug}.zenoeats.local")
    yield slug
    _host.pop()

    with tenant_session(rid) as session:
        session.execute(text("DELETE FROM restaurants WHERE id = :r"), {"r": rid})


def _address() -> str:
    """A fresh address per test: registration is permanent within a run."""
    return f"customer-{uuid.uuid4().hex[:12]}@example.com"


@pytest.fixture(autouse=True)
def fresh_rate_limit_budget():
    """Give each test the full per-address budget.

    The limiter is a fixed window in Redis keyed by client address, and every
    test here arrives from the same one. Ten registrations per five minutes is
    the right number for the internet and far too few for a file that
    registers an account in most of its tests, so the buckets these endpoints
    use are cleared between them. The limit itself is not touched -- there is
    a test below that relies on it still being enforced.
    """
    from app.core.ratelimit import runtime_redis

    def clear():
        try:
            redis = runtime_redis()
            # Every bucket an endpoint in this file consumes. A missing one
            # does not fail immediately -- it passes on a fresh Redis and then
            # fails once a few runs have accumulated, which is the worst way
            # for a test to be wrong. The reset tests were added without
            # these two and did exactly that.
            for bucket in (
                "customer_register",
                "customer_login",
                "customer_forgot_password",
                "customer_reset_password",
                "customer_change_email",
            ):
                keys = list(redis.scan_iter(match=f"rl:{bucket}:*"))
                if keys:
                    redis.delete(*keys)
        except Exception:  # noqa: BLE001, S110 -- deliberate, see below
            # Broad and silent on purpose. The limiter itself fails open when
            # Redis is unreachable, so a suite that cannot reach it will never
            # hit a limit either and has nothing to clean up. Raising here
            # would turn "no Redis" into a dozen failures that say nothing
            # about the code under test.
            pass

    clear()
    yield
    clear()


@pytest.fixture(autouse=True)
def sweep_test_customers():
    """Remove the customer rows a test minted.

    Registration has no undo in the API -- closing an account soft-deletes it
    and keeps the row, which is correct for a product and unhelpful for a
    suite that registers a new address every run.
    """
    from app.db.base import utcnow
    from app.services import retention

    started = utcnow()
    yield
    # The app role, not the system one: system is narrow cross-tenant
    # discovery and holds no DELETE on users. retention's own sweep goes the
    # same way.
    with retention._platform_transaction() as session:
        session.execute(
            text(
                "DELETE FROM users WHERE kind = 'CUSTOMER' "
                "AND password_hash IS NOT NULL AND created_at >= :t"
            ),
            {"t": started},
        )


# --------------------------------------------------------------- sessions

def test_registering_signs_the_customer_in():
    client = _client()
    email = _address()

    created = client.post(
        "/api/v1/customer/register",
        json={"email": email, "password": "burgers-and-fries", "full_name": "Sam Taylor"},
    )

    assert created.status_code == 201, created.text
    assert created.json()["is_guest"] is False
    assert customer_auth.SESSION_COOKIE in created.cookies

    # The cookie alone reaches an endpoint that requires an identity: no
    # bearer token, and so no identity provider in the request path at all.
    mine = client.get("/api/v1/customer/orders")
    assert mine.status_code == 200, mine.text


def test_the_session_cookie_is_httponly_and_not_sent_cross_site():
    created = _client().post(
        "/api/v1/customer/register",
        json={"email": _address(), "password": "burgers-and-fries"},
    )

    header = created.headers["set-cookie"]
    lowered = header.lower()
    assert "httponly" in lowered, "a readable session cookie is an XSS away from stolen"
    assert "samesite=lax" in lowered
    assert "path=/" in lowered


def test_signing_in_is_case_insensitive_about_the_address():
    client = _client()
    email = _address()
    client.post(
        "/api/v1/customer/register",
        json={"email": email, "password": "burgers-and-fries"},
    )

    back = client.post(
        "/api/v1/customer/login",
        json={"email": email.upper(), "password": "burgers-and-fries"},
    )

    assert back.status_code == 200, back.text


def test_logging_out_ends_the_session():
    client = _client()
    email = _address()
    client.post(
        "/api/v1/customer/register",
        json={"email": email, "password": "burgers-and-fries"},
    )

    assert client.post("/api/v1/customer/logout").status_code == 204
    assert client.get("/api/v1/customer/orders").status_code == 401


# ------------------------------------------------------------- refusals

def test_an_unknown_address_is_indistinguishable_from_a_wrong_password():
    """The login form must not double as a directory of who has an account."""
    client = _client()
    email = _address()
    client.post(
        "/api/v1/customer/register",
        json={"email": email, "password": "burgers-and-fries"},
    )
    client.post("/api/v1/customer/logout")

    wrong = client.post(
        "/api/v1/customer/login",
        json={"email": email, "password": "not-the-right-one"},
    )
    unknown = client.post(
        "/api/v1/customer/login",
        json={"email": _address(), "password": "not-the-right-one"},
    )

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_one_account_per_address():
    client = _client()
    email = _address()
    client.post(
        "/api/v1/customer/register",
        json={"email": email, "password": "burgers-and-fries"},
    )

    again = client.post(
        "/api/v1/customer/register",
        json={"email": email.upper(), "password": "a-different-password"},
    )

    # 409, not a silent sign-in: no password was checked, so this request has
    # not shown it belongs to the account that already holds the address.
    assert again.status_code == 409, again.text
    assert again.json()["detail"]["code"] == "EMAIL_IN_USE"


def test_a_short_password_is_refused_before_an_account_exists():
    email = _address()
    refused = _client().post(
        "/api/v1/customer/register", json={"email": email, "password": "short"}
    )

    assert refused.status_code == 400
    assert refused.json()["detail"]["code"] == "WEAK_PASSWORD"

    with system_session() as session:
        rows = session.execute(
            text("SELECT count(*) FROM users WHERE lower(email) = :e"), {"e": email}
        ).scalar_one()
    assert rows == 0, "a refused registration must not leave an account behind"


# ------------------------------------------- the boundary between systems

def test_a_customer_cookie_does_not_open_a_staff_or_admin_session():
    """All four session types are signed with SESSION_SECRET. The `typ` claim
    is the only thing keeping them apart, so it is the thing to test."""
    client = _client()
    client.post(
        "/api/v1/customer/register",
        json={"email": _address(), "password": "burgers-and-fries"},
    )
    token = client.cookies[customer_auth.SESSION_COOKIE]

    assert staff_auth.verify_session(token) is None
    assert guest_auth.verify_session(token) is None

    # And through the API, not only the verifier.
    staff = _client()
    # The host the fixture made, or the cookie is never sent and this
    # passes for the wrong reason.
    staff.cookies.set(staff_auth.SESSION_COOKIE, token, domain=_host[0])
    assert staff.get("/api/v1/restaurant/me").status_code == 401


def test_a_staff_token_does_not_open_a_customer_session():
    token = staff_auth.issue_session(uuid.uuid4())
    assert customer_auth.verify_session(token) is None


# ------------------------------------------------------- guests are exempt

def test_several_guests_may_share_one_address():
    """The one-account-per-address index is partial, covering CUSTOMER only.

    A guest gets a row per checkout and is never looked up by email, so two
    people who type the same address are two guests. If the index caught them
    too, the second person to order without an account could not check out.
    """
    from app.services import guest_customers

    shared = _address()
    first = guest_customers.create_guest(email=shared, full_name="One")
    second = guest_customers.create_guest(email=shared, full_name="Two")

    assert first.id != second.id

    from app.services import retention

    with retention._platform_transaction() as session:
        session.execute(
            text("DELETE FROM users WHERE id IN (:a, :b)"),
            {"a": str(first.id), "b": str(second.id)},
        )


def test_a_guest_row_does_not_block_a_real_account_on_the_same_address():
    from app.services import guest_customers

    shared = _address()
    guest = guest_customers.create_guest(email=shared, full_name="Before the account")

    created = _client().post(
        "/api/v1/customer/register",
        json={"email": shared, "password": "burgers-and-fries"},
    )

    assert created.status_code == 201, created.text

    with system_session() as session:
        kinds = session.execute(
            text("SELECT kind FROM users WHERE lower(email) = :e ORDER BY kind"),
            {"e": shared},
        ).scalars().all()
    assert kinds == [UserKind.CUSTOMER.value, UserKind.GUEST.value]

    from app.services import retention

    with retention._platform_transaction() as session:
        session.execute(text("DELETE FROM users WHERE id = :i"), {"i": str(guest.id)})


# ------------------------------------------------------------- closing up

def test_closing_an_account_frees_the_address_to_register_again():
    """The data-deletion page promises the address goes. It has to actually go.

    close_account used to swap the address for a placeholder only when the row
    carried a Clerk id, which a password account does not. Left in place, the
    address stays taken by a closed row and uq_users_customer_email then stops
    that person ever coming back with the address they asked us to forget.
    """
    client = _client()
    email = _address()
    client.post(
        "/api/v1/customer/register",
        json={"email": email, "password": "burgers-and-fries"},
    )

    closed = client.delete("/api/v1/customer/account")
    assert closed.status_code == 204, closed.text

    # The credential is gone, so the old password opens nothing.
    assert (
        _client()
        .post("/api/v1/customer/login", json={"email": email, "password": "burgers-and-fries"})
        .status_code
        == 401
    )

    # And the address is free.
    again = _client().post(
        "/api/v1/customer/register",
        json={"email": email, "password": "a-brand-new-password"},
    )
    assert again.status_code == 201, again.text


# ------------------------------------------------------------ reset a password

def _reset_token_for(email: str) -> str:
    """The token the email would carry, read from the account directly.

    The endpoint hands it to a background task and answers 204 either way, so
    there is nothing in the response to read it out of -- which is the point
    of that design, not an oversight to work around in production code.
    """
    with system_session() as session:
        user = session.execute(
            text("SELECT id, password_hash FROM users WHERE lower(email) = :e AND kind = 'CUSTOMER'"),
            {"e": email},
        ).one()
    return customer_auth.issue_reset_token(user.id, user.password_hash)


def test_asking_to_reset_says_nothing_about_who_has_an_account():
    client = _client()
    known = _address()
    client.post("/api/v1/customer/register", json={"email": known, "password": "burgers-and-fries"})

    for address in (known, _address()):
        answer = client.post("/api/v1/customer/password/forgot", json={"email": address})
        assert answer.status_code == 204
        assert answer.content == b""


def test_a_reset_link_sets_the_password_and_ends_every_session():
    client = _client()
    email = _address()
    client.post("/api/v1/customer/register", json={"email": email, "password": "burgers-and-fries"})
    # Signed in from registering; the reset must cut this session loose too.
    assert client.get("/api/v1/customer/orders").status_code == 200

    token = _reset_token_for(email)
    done = client.post(
        "/api/v1/customer/password/reset",
        json={"token": token, "password": "a-whole-new-password"},
    )
    assert done.status_code == 204, done.text

    assert client.get("/api/v1/customer/orders").status_code == 401, "the old session must be gone"

    fresh = _client()
    assert fresh.post(
        "/api/v1/customer/login", json={"email": email, "password": "burgers-and-fries"}
    ).status_code == 401, "the old password must stop working"
    assert fresh.post(
        "/api/v1/customer/login", json={"email": email, "password": "a-whole-new-password"}
    ).status_code == 200


def test_a_reset_link_works_once():
    client = _client()
    email = _address()
    client.post("/api/v1/customer/register", json={"email": email, "password": "burgers-and-fries"})
    token = _reset_token_for(email)

    first = client.post(
        "/api/v1/customer/password/reset", json={"token": token, "password": "first-new-password"}
    )
    assert first.status_code == 204, first.text

    again = client.post(
        "/api/v1/customer/password/reset", json={"token": token, "password": "second-new-password"}
    )
    assert again.status_code == 400
    assert again.json()["detail"]["code"] == "RESET_LINK_INVALID"

    # And the second attempt changed nothing.
    assert _client().post(
        "/api/v1/customer/login", json={"email": email, "password": "first-new-password"}
    ).status_code == 200


def test_a_session_cookie_is_not_a_reset_token():
    """Both are signed with SESSION_SECRET; only `typ` separates them."""
    client = _client()
    email = _address()
    client.post("/api/v1/customer/register", json={"email": email, "password": "burgers-and-fries"})
    session_token = client.cookies[customer_auth.SESSION_COOKIE]

    assert customer_auth.verify_reset_token(session_token) is None

    refused = client.post(
        "/api/v1/customer/password/reset",
        json={"token": session_token, "password": "trying-it-on-here"},
    )
    assert refused.status_code == 400


def test_the_browser_and_the_api_agree_on_the_minimum_password():
    """The rule is stated twice and enforced once.

    The API decides; the login pages only repeat it so a form can say the rule
    before someone submits. Nothing in either language makes them agree, and
    disagreeing is a specific, miserable bug: a form that happily submits a
    ten-character password to an API that wants twelve, and a refusal whose
    wording contradicts the hint printed directly above the field.

    Read from the source rather than duplicated here a third time.
    """
    import re
    from pathlib import Path

    shared = Path(__file__).resolve().parents[2] / "web" / "login" / "customer-shared.ts"
    found = re.search(r"export const MIN_PASSWORD_LENGTH = (\d+);", shared.read_text())

    assert found, f"MIN_PASSWORD_LENGTH is no longer declared in {shared.name}"
    assert int(found.group(1)) == customer_auth.MIN_PASSWORD_LENGTH, (
        f"{shared.name} says {found.group(1)}, the API wants "
        f"{customer_auth.MIN_PASSWORD_LENGTH}"
    )


# ------------------------------------------------------ sign in with Google

def test_google_sign_in_is_refused_cleanly_when_it_is_not_configured():
    """No credentials is a 503 that says so, not a stack trace."""
    answer = _client().get("/api/v1/customer/google/start", follow_redirects=False)

    assert answer.status_code == 503
    assert answer.json()["detail"]["code"] == "GOOGLE_UNAVAILABLE"


def test_a_callback_without_state_is_refused():
    """`state` is the CSRF defence for the redirect. Missing it is not a flow
    we started, whatever else the URL carries."""
    answer = _client().get(
        "/api/v1/customer/google/callback?code=whatever", follow_redirects=False
    )

    assert answer.status_code == 400


def test_a_state_this_browser_did_not_start_is_refused():
    """A signed state is not enough on its own.

    Without the cookie check, an attacker who obtained any valid state could
    hand a victim a callback URL carrying their own code and have the
    victim's browser sign into the attacker's account.
    """
    from app.core import google_oauth

    state, _nonce = google_oauth.issue_state(
        origin="spicehouse.zenoeats.local", next_path="/"
    )

    with pytest.raises(google_oauth.GoogleError):
        google_oauth.read_state(state, cookie_nonce=None)
    with pytest.raises(google_oauth.GoogleError):
        google_oauth.read_state(state, cookie_nonce="not-the-nonce")

    # With the right nonce it reads back what it was issued for.
    state, nonce = google_oauth.issue_state(
        origin="spicehouse.zenoeats.local", next_path="/cart"
    )
    assert google_oauth.read_state(state, nonce) == ("spicehouse.zenoeats.local", "/cart")


def test_a_session_token_is_not_a_google_state():
    """Everything of ours is signed with SESSION_SECRET; `typ` separates them."""
    from app.core import google_oauth

    session_token = customer_auth.issue_session(uuid.uuid4())
    _state, nonce = google_oauth.issue_state(origin="x.zenoeats.local", next_path="/")

    with pytest.raises(google_oauth.GoogleError):
        google_oauth.read_state(session_token, nonce)


def test_an_unverified_google_address_cannot_claim_an_account(monkeypatch):
    """The email is what links a Google sign-in to an existing account, so an
    address Google has not verified must not be accepted -- otherwise anyone
    able to put that address on a Google account could claim the account here.

    Patched at the module's own seams: the real path fetches Google's signing
    keys and verifies a signature, neither of which this is about.
    """
    from app.core import google_oauth

    class _Key:
        key = "stub"

    monkeypatch.setattr(google_oauth, "_jwks", lambda: type("J", (), {
        "get_signing_key_from_jwt": staticmethod(lambda _t: _Key())
    })())
    monkeypatch.setattr(google_oauth.jwt, "decode", lambda *a, **k: {
        "sub": "google-subject-1",
        "email": "someone@example.com",
        "email_verified": False,
        "name": "Someone",
    })

    with pytest.raises(google_oauth.GoogleError) as refused:
        google_oauth.verify_id_token("pretend-token")
    assert "isn't verified" in str(refused.value)

    # Verified, and it reads back what Google said.
    monkeypatch.setattr(google_oauth.jwt, "decode", lambda *a, **k: {
        "sub": "google-subject-1",
        "email": "Someone@Example.com",
        "email_verified": True,
        "name": "Someone",
    })
    identity = google_oauth.verify_id_token("pretend-token")
    assert identity.subject == "google-subject-1"
    # Lower-cased, because it is matched against stored addresses.
    assert identity.email == "someone@example.com"


def _google_claims(monkeypatch, **claims):
    """Verify an ID token whose claims are these, without Google or a key."""
    from app.core import google_oauth

    class _Key:
        key = "stub"

    monkeypatch.setattr(google_oauth, "_jwks", lambda: type("J", (), {
        "get_signing_key_from_jwt": staticmethod(lambda _t: _Key())
    })())
    monkeypatch.setattr(google_oauth.jwt, "decode", lambda *a, **k: claims)
    return google_oauth.verify_id_token("pretend-token")


def test_a_google_account_with_no_name_has_no_name(monkeypatch):
    """Not the string "None".

    `str(claims.get("name"))` on a missing claim produces the four-character
    string "None", which went straight onto the account and greeted that
    customer as "Welcome back, None". Google omits `name` whenever the
    profile scope is not granted, which is every sign-in here -- this asks
    for `openid email` and nothing else -- so it was the common case, not
    the rare one.
    """
    identity = _google_claims(
        monkeypatch, sub="s1", email="noname@example.com", email_verified=True
    )
    assert identity.full_name is None


def test_a_blank_google_name_is_no_name_either(monkeypatch):
    identity = _google_claims(
        monkeypatch, sub="s2", email="blank@example.com", email_verified=True, name="   "
    )
    assert identity.full_name is None


def test_a_real_google_name_is_kept_and_trimmed(monkeypatch):
    identity = _google_claims(
        monkeypatch, sub="s3", email="sam@example.com", email_verified=True,
        name="  Sam Rivera  ",
    )
    assert identity.full_name == "Sam Rivera"


def test_the_google_state_cookie_survives_the_return_trip_from_google():
    """SameSite must not be None.

    The browser comes back from accounts.google.com by a top-level GET
    navigation, which Lax permits -- so Lax is both sufficient and stricter.
    SameSite=None additionally requires Secure, which would silently drop the
    cookie on any plain-HTTP development host and make the flow fail with no
    message.
    """
    import inspect

    from app.api.v1 import customer_accounts

    source = inspect.getsource(customer_accounts.google_start)
    assert 'samesite="lax"' in source
    assert '"none"' not in source
