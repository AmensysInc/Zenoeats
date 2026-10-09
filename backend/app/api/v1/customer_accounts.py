"""Customer credentials: register, sign in, sign out, reset, change email.

Split out of customer.py, which had grown to hold four unrelated jobs -- a
customer's profile, their order history, their favourites, and this. They
change for different reasons and are read by different people: the ones above
are a signed-in customer's own data, and these are how someone becomes that
customer in the first place.

Everything here is a cookie this API signs and clears (core/customer_auth.py).
There is no identity provider in the request path, which is what lets customer
sign-in work on a deployment with nothing configured but a database.

One rule runs through all of it: an unknown address and a wrong password are
indistinguishable to the caller -- same status, same wording, same Argon2 time
burned. A sign-in form that answers faster for addresses that exist is a
directory of who our customers are.
"""

import logging
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import get_current_user
from app.config import settings
from app.core import customer_auth, errors, google_oauth, ratelimit
from app.core.logsafe import email_for_log
from app.core.ratelimit import per_ip, per_user
from app.core.tenant import host_of, host_on_allowed_origin
from app.db.base import utcnow
from app.db.session import system_session
from app.models import User, UserKind
from app.schemas.api import CustomerSessionOut
from app.services import account_emails
from app.services.customer_view import session_out as _session_out

log = logging.getLogger(__name__)
router = APIRouter(prefix="/customer", tags=["customer"])


# ------------------------------------------------- accounts (no Clerk) ---
#
# Register, sign in, sign out. The session is an httpOnly cookie this API
# signs and reads, exactly as the staff, admin and guest sessions are -- which
# is what lets customer sign-in work with no identity provider configured.
#
# Every refusal below is "Email or password is incorrect", whatever actually
# went wrong, and an unknown address burns the same Argon2 time as a known one
# with the wrong password. A login form that answers faster for addresses that
# exist is a directory of who has an account.


class RegisterIn(BaseModel):
    # A bounded string rather than EmailStr, matching StaffLoginIn: the
    # project carries no email-validator dependency, and an address is proved
    # by a message arriving at it, never by a regex.
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)
    full_name: str | None = Field(default=None, max_length=160)


class CustomerLoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


def _cookie_domain() -> str | None:
    """The parent domain, so one sign-in covers every storefront.

    A customer account has always been one account for every restaurant on the
    platform -- the same person ordering from two of them is one row. A
    host-only cookie contradicted that: signing in at one storefront left you
    anonymous at the next, for no gain.

    It is also what makes Google sign-in possible at all. Google matches
    redirect URIs exactly, so there is one callback for the whole platform, on
    the root domain; a host-only cookie set there would not be sent to the
    restaurant subdomain the customer came from.

    Every host under this domain is this same API, so widening the cookie
    hands it to nobody new. Guest cookies stay host-only on purpose: a guest
    session genuinely belongs to the one restaurant that minted it.

    None in development, where the browser is on a bare hostname and Chrome
    refuses a Domain attribute that is not a registrable parent.
    """
    root = settings.ROOT_DOMAIN.strip().lower()
    return f".{root}" if root.count(".") >= 1 and not root.endswith(".local") else None


def _set_session_cookie(response: Response, user_id: UUID) -> None:
    response.set_cookie(
        key=customer_auth.SESSION_COOKIE,
        value=customer_auth.issue_session(user_id),
        max_age=settings.CUSTOMER_SESSION_TTL_MINUTES * 60,
        domain=_cookie_domain(),
        # httponly: unreadable to JavaScript, so an XSS bug anywhere on the
        # storefront cannot lift a customer's session out of the page.
        httponly=True,
        # lax: the cookie rides a top-level navigation back from an email
        # link, but not a cross-site form post, which is what stops a forged
        # order being placed in someone's name.
        samesite="lax",
        # Development is plain HTTP on .local; live must never send this in
        # the clear, so it follows the environment rather than being hardcoded.
        secure=settings.ENV == "production",
        path="/",
    )


@router.post(
    "/register",
    response_model=CustomerSessionOut,
    status_code=201,
    # Unauthenticated and account-creating. Keyed by address because there is
    # no session yet, and tighter than a read because each call costs an
    # Argon2 hash.
    dependencies=[Depends(per_ip("customer_register", limit=10, window_seconds=300))],
)
def register(body: RegisterIn, response: Response):
    """Create a customer account and sign it in.

    Signing in immediately is the point: an account made at checkout exists to
    get that order placed, and a second form between the two loses people for
    no security gain -- they have just proved they hold the password by
    choosing it.
    """
    email = body.email.strip().lower()
    complaint = customer_auth.password_complaint(body.password)
    if complaint:
        raise errors.ApiError(400, "WEAK_PASSWORD", complaint)

    digest = customer_auth.hash_password(body.password)
    with system_session() as session:
        user = User(
            kind=UserKind.CUSTOMER.value,
            email=email,
            full_name=(body.full_name or "").strip() or None,
            password_hash=digest,
        )
        session.add(user)
        try:
            session.flush()
        except IntegrityError as exc:
            # uq_users_customer_email. Answered as a plain conflict rather
            # than silently signing them in: we have not checked a password,
            # so this request has not proved it is that account's owner.
            session.rollback()
            raise errors.ApiError(
                409,
                "EMAIL_IN_USE",
                "There is already an account with that email. Sign in instead.",
            ) from exc
        out = _session_out(user)
        user_id = user.id

    _set_session_cookie(response, user_id)
    log.info("customer account created for %s", email_for_log(email))
    return out


@router.post(
    "/login",
    response_model=CustomerSessionOut,
    dependencies=[Depends(per_ip("customer_login", limit=10, window_seconds=300))],
)
def customer_login(body: CustomerLoginIn, response: Response):
    """Sign in to a customer account."""
    email = body.email.strip().lower()
    # Before anything is looked up, so a spent account cannot keep guessing.
    if ratelimit.sign_in_blocked("customer_login", email):
        log.warning("customer sign-in throttled for %s", email_for_log(email))
        raise ratelimit.sign_in_throttled()

    with system_session() as session:
        user = session.execute(
            select(User).where(
                User.email == email,
                User.kind == UserKind.CUSTOMER.value,
                User.password_hash.isnot(None),
            )
        ).scalar_one_or_none()
        if user is None:
            # Same work, same wording, same counter as a wrong password: an
            # address that has no account must not answer sooner or differently
            # from one that does.
            customer_auth.dummy_verify(body.password)
            log.warning("failed customer sign-in for unknown %s", email_for_log(email))
            ratelimit.sign_in_failed("customer_login", email)
            raise errors.ApiError(401, "INVALID_CREDENTIALS", "Email or password is incorrect.")
        digest = user.password_hash
        user_id = user.id
        active = user.deleted_at is None
        out = _session_out(user)

    if not customer_auth.verify_password(digest, body.password):
        log.warning("failed customer sign-in for %s", email_for_log(email))
        ratelimit.sign_in_failed("customer_login", email)
        raise errors.ApiError(401, "INVALID_CREDENTIALS", "Email or password is incorrect.")
    if not active:
        # A closed account. Deliberately distinct from a wrong password: the
        # person holds the credentials, and "incorrect" would send them round
        # the password-reset loop for an account that no longer exists.
        raise errors.ApiError(403, "ACCOUNT_CLOSED", "This account has been closed.")

    _set_session_cookie(response, user_id)
    return out


@router.post("/logout", status_code=204)
def customer_logout(response: Response):
    """Sign out in this browser.

    Only this browser. Ending every session an account holds is what
    sessions_valid_after is for, and it belongs on a deliberate "sign out
    everywhere" rather than on the button someone presses at a shared laptop.
    """
    response.delete_cookie(
        key=customer_auth.SESSION_COOKIE,
        path="/",
        # Same domain it was set with: a delete that does not match the
        # original scope leaves the cookie exactly where it was.
        domain=_cookie_domain(),
        httponly=True,
        samesite="lax",
        secure=settings.ENV == "production",
    )


# ------------------------------------------------------- forgotten password

class ForgotPasswordIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class ResetPasswordIn(BaseModel):
    token: str = Field(min_length=1, max_length=4096)
    password: str = Field(min_length=1, max_length=256)


@router.post(
    "/password/forgot",
    status_code=204,
    # Unauthenticated, sends mail, and the one endpoint here that will happily
    # tell a stranger which addresses exist if it is allowed to run freely.
    dependencies=[Depends(per_ip("customer_forgot_password", limit=5, window_seconds=900))],
)
def forgot_password(body: ForgotPasswordIn, background: BackgroundTasks):
    """Email a reset link, if there is an account to send one to.

    Always 204, always the same shape, whether or not the address is known.
    The page says "if there's an account, we've sent a link", which is the
    only answer that does not turn this into a way to ask us who our
    customers are.

    Nothing is written. The link carries a fingerprint of the password
    currently in force (core/customer_auth.py), so it is single-use without a
    table of spent tokens, and asking twice simply mints a second one.
    """
    email_address = body.email.strip().lower()
    with system_session() as session:
        user = session.execute(
            select(User).where(
                User.email == email_address,
                User.kind == UserKind.CUSTOMER.value,
                User.password_hash.isnot(None),
                User.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if user is None:
            log.info("password reset asked for an address with no account")
            return Response(status_code=204)
        token = customer_auth.issue_reset_token(user.id, user.password_hash)
        to = user.email
        first_name = (user.full_name or "").split(" ")[0] or None

    reset_url = (
        f"{settings.PLATFORM_URL_TEMPLATE.format(root_domain=settings.ROOT_DOMAIN)}"
        # The existing two-step page, which shows the new-password form when
        # the URL carries a token. Reused rather than given a route of its
        # own: /account/* paths are mapped by hand in both vite.config.ts and
        # web/nginx.conf, and a second mapping is a second thing to drift.
        f"/account/forgot-password?token={quote(token)}"
    )
    # After the response, like every other email here: a slow provider must
    # never hold up the page.
    background.add_task(account_emails.queue_password_reset, to, first_name, reset_url)
    return Response(status_code=204)


@router.post(
    "/password/reset",
    status_code=204,
    dependencies=[Depends(per_ip("customer_reset_password", limit=10, window_seconds=900))],
)
def reset_password(body: ResetPasswordIn, response: Response):
    """Set a new password from a reset link, and end every session.

    Signing the person in here would be convenient and wrong: whoever holds
    the link holds the account until the password changes, and the thing to
    do at that moment is cut every session loose -- the new one included --
    so a thief who already had a cookie loses it. They sign in with the
    password they just chose, which also proves they remember it.
    """
    complaint = customer_auth.password_complaint(body.password)
    if complaint:
        raise errors.ApiError(400, "WEAK_PASSWORD", complaint)

    request = customer_auth.verify_reset_token(body.token)
    if request is None:
        raise errors.ApiError(
            400, "RESET_LINK_INVALID", "That link has expired. Ask for a new one."
        )

    digest = customer_auth.hash_password(body.password)
    with system_session() as session:
        user = session.get(User, request.user_id)
        if (
            user is None
            or user.kind != UserKind.CUSTOMER.value
            or user.deleted_at is not None
            # The fingerprint is what makes the link single-use: it stops
            # matching the moment any password change lands, including the one
            # this request is a repeat of.
            or not customer_auth.reset_token_matches(request, user.password_hash)
        ):
            raise errors.ApiError(
                400, "RESET_LINK_INVALID", "That link has already been used, or has expired."
            )
        user.password_hash = digest
        # Everywhere, not just here. A reset is what someone does when they
        # think another person has their account.
        user.sessions_valid_after = utcnow()
        session.flush()
        log.info("customer password reset completed for user %s", user.id)

    # Including this browser: there is no session to keep, and leaving a
    # stale cookie behind would have the next request refused with no
    # explanation.
    response.delete_cookie(
        key=customer_auth.SESSION_COOKIE,
        path="/",
        # Same domain it was set with: a delete that does not match the
        # original scope leaves the cookie exactly where it was.
        domain=_cookie_domain(),
        httponly=True,
        samesite="lax",
        secure=settings.ENV == "production",
    )
    return Response(status_code=204)


class ChangeEmailIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    # Proof that the person at the keyboard is the account holder and not
    # someone who sat down at an unlocked laptop. A session alone is not
    # enough to move the address every receipt and every password reset will
    # be sent to from now on.
    password: str = Field(min_length=1, max_length=256)


@router.put(
    "/email",
    response_model=CustomerSessionOut,
    dependencies=[Depends(per_user("customer_change_email", limit=10))],
)
def change_email(body: ChangeEmailIn, user: User = Depends(get_current_user)):
    """Change the address on a customer account.

    Verification by password rather than by a link to the new address. The
    trade is deliberate and worth naming: a confirmation link proves the new
    address exists and is reachable, which this does not. What it does prove
    is that the person asking holds the account -- which is the attack that
    matters here, someone taking over an unattended session and redirecting
    every future receipt and reset link to themselves.

    A wrong address, meanwhile, is self-correcting: the next receipt does not
    arrive, and the owner still holds the password to change it back. A
    hijacked account is not.
    """
    if user.kind != UserKind.CUSTOMER.value or not user.password_hash:
        raise errors.ApiError(
            403, "ACCOUNT_REQUIRED", "Only a signed-in account can change its email."
        )

    address = body.email.strip().lower()
    if "@" not in address or address.startswith("@") or address.endswith("@"):
        raise errors.ApiError(400, "INVALID_EMAIL", "Enter a valid email address.")

    if not customer_auth.verify_password(user.password_hash, body.password):
        raise errors.ApiError(401, "INVALID_CREDENTIALS", "That password is incorrect.")

    if address == user.email.lower():
        raise errors.ApiError(400, "EMAIL_UNCHANGED", "That is already your email address.")

    with system_session() as session:
        current = session.get(User, user.id)
        if current is None or current.deleted_at is not None:
            raise errors.ApiError(403, "ACCOUNT_INACTIVE", "This account is not active.")
        current.email = address
        try:
            session.flush()
        except IntegrityError as exc:
            session.rollback()
            # uq_users_customer_email. Named plainly: the person has already
            # proved they hold this account, so telling them the other address
            # is taken reveals nothing they could not learn by trying to
            # register it.
            raise errors.ApiError(
                409, "EMAIL_IN_USE", "There is already an account with that email."
            ) from exc
        out = _session_out(current)

    user.email = address
    log.info("customer %s changed their email", user.id)
    return out


# ------------------------------------------------------- sign in with Google
#
# Two endpoints and a redirect between them; core/google_oauth.py holds the
# protocol and explains why the callback is on the root domain rather than on
# each restaurant's subdomain.
#
# Neither is a JSON API: a browser arrives at them by navigation and leaves by
# redirect, which is why they answer 302 and never a body. That also keeps the
# client secret and the code exchange entirely server-side -- the browser
# never holds anything it could replay.


@router.get("/google/start", include_in_schema=False)
def google_start(request: Request, next_path: str = Query("/", alias="next")):
    """Send the browser to Google, remembering where it came from."""
    if not google_oauth.configured():
        raise errors.ApiError(
            503, "GOOGLE_UNAVAILABLE", "Google sign-in is not available here."
        )

    # Where to come back to. Taken from the Host this was called on rather
    # than from a parameter: a redirect target a caller can choose is an open
    # redirect, and this one is handed to Google and back.
    host = host_of(request.headers.get("host")) or settings.ROOT_DOMAIN
    if not host_on_allowed_origin(host):
        raise errors.ApiError(400, "BAD_ORIGIN", "Unknown storefront.")

    # Likewise the path: a path, never a URL, so it cannot leave this host.
    where = next_path if next_path.startswith("/") and not next_path.startswith("//") else "/"

    state, nonce = google_oauth.issue_state(origin=host, next_path=where)
    response = RedirectResponse(google_oauth.authorize_url(state), status_code=302)
    response.set_cookie(
        key=google_oauth.STATE_COOKIE,
        value=nonce,
        max_age=google_oauth.STATE_TTL_MINUTES * 60,
        httponly=True,
        # Lax, and Lax is enough. The browser arrives back here from
        # accounts.google.com by a top-level GET navigation, which is exactly
        # the case Lax permits -- it withholds cookies from cross-site
        # subrequests and form posts, not from following a redirect into the
        # address bar. SameSite=None would be strictly weaker and would also
        # force Secure, breaking the flow on any plain-HTTP development host.
        samesite="lax",
        secure=settings.ENV == "production",
        path="/",
    )
    return response


@router.get("/google/callback", include_in_schema=False)
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """Where Google sends the browser back.

    Always ends in a redirect, never an error page: somebody who cancelled at
    Google, or whose sign-in expired, belongs back on the sign-in page with a
    reason, not on a JSON document.
    """
    def back(origin: str, path: str, problem: str | None = None) -> RedirectResponse:
        scheme_host = _origin_url(origin)
        target = f"{scheme_host}{path}"
        if problem:
            target = f"{scheme_host}/account/sign-in?error={quote(problem)}"
        out = RedirectResponse(target, status_code=302)
        out.delete_cookie(google_oauth.STATE_COOKIE, path="/")
        return out

    if not state:
        raise errors.ApiError(400, "BAD_STATE", "That sign-in didn't complete.")

    try:
        origin, where = google_oauth.read_state(
            state, request.cookies.get(google_oauth.STATE_COOKIE)
        )
    except google_oauth.GoogleError:
        # No trustworthy origin to send them to, so the platform root it is.
        return back(settings.ROOT_DOMAIN, "/", "social_failed")

    # The customer pressed cancel, or Google refused.
    if error or not code:
        return back(origin, where, "social_failed")

    try:
        identity = google_oauth.exchange(code)
    except google_oauth.GoogleError:
        return back(origin, where, "social_failed")

    user_id = _customer_for_google(identity)
    response = back(origin, where)
    _set_session_cookie(response, user_id)
    return response


def _origin_url(host: str) -> str:
    """The storefront's own base URL, built from the template the rest of the
    app uses so the development port is not lost."""
    template = settings.PLATFORM_URL_TEMPLATE
    scheme, _, rest = template.partition("://")
    port = rest.partition("{root_domain}")[2]
    return f"{scheme}://{host}{port}"


def _customer_for_google(identity: google_oauth.GoogleIdentity) -> UUID:
    """The account this Google identity signs into, creating one if needed.

    Matched on the Google subject first, because that is the identity and it
    never changes. Falling back to the verified email is what links a Google
    sign-in to an account someone already made with a password -- safe only
    because Google has verified the address, which is checked before we get
    here.
    """
    with system_session() as session:
        user = session.execute(
            select(User).where(User.google_subject == identity.subject)
        ).scalar_one_or_none()

        if user is None:
            user = session.execute(
                select(User).where(
                    User.email == identity.email,
                    User.kind == UserKind.CUSTOMER.value,
                    User.deleted_at.is_(None),
                )
            ).scalar_one_or_none()
            if user is not None:
                # Claim it for this Google account from now on.
                user.google_subject = identity.subject
                log.info("linked google sign-in to existing customer %s", user.id)

        if user is None:
            user = User(
                kind=UserKind.CUSTOMER.value,
                email=identity.email,
                full_name=identity.full_name,
                google_subject=identity.subject,
            )
            session.add(user)
            session.flush()
            log.info("created customer %s from a google sign-in", user.id)
        elif user.deleted_at is not None:
            raise errors.ApiError(403, "ACCOUNT_CLOSED", "This account has been closed.")
        else:
            # Keep the address in step with the one Google verifies.
            user.email = identity.email

        session.flush()
        return user.id
