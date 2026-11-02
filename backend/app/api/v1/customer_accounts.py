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
from urllib.parse import parse_qs, quote
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import get_current_user
from app.config import settings
from app.core import customer_auth, errors, guest_auth, ratelimit, social_auth
from app.core.logsafe import email_for_log
from app.core.ratelimit import per_ip, per_user
from app.core.tenant import host_of, host_on_allowed_origin
from app.db.base import utcnow
from app.db.session import system_session
from app.models import User, UserKind
from app.schemas.api import CustomerSessionOut
from app.services import account_emails, terms
from app.services.customer_view import session_out as _session_out

log = logging.getLogger(__name__)
router = APIRouter(prefix="/customer", tags=["customer"])


# ----------------------------------------------------------- accounts ---
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
    # The sign-up page's checkbox, which must be ticked before it will submit.
    # Recorded here so a customer who ticked it is not asked again at checkout.
    terms_accepted: bool = False


class CustomerLoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


def _cookie_domain() -> str | None:
    """The parent domain, so one sign-in covers every storefront.

    A customer account has always been one account for every restaurant on the
    platform -- the same person ordering from two of them is one row. A
    host-only cookie contradicted that: signing in at one storefront left you
    anonymous at the next, for no gain.

    It is also what makes social sign-in possible at all. Providers match
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
        if body.terms_accepted:
            terms.stamp(user)
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
                "There is already an account with that email. Sign in with your "
                "password or the way you first signed up, or reset your password.",
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

    Any guest cookie goes too. It sits underneath the account's from before
    they signed in, and leaving it would turn "sign out" into "you are now the
    guest you were last month" -- signed out, and still ordering as somebody.
    Host-only, so it is cleared without the domain the account's carries.
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
    response.delete_cookie(
        key=guest_auth.SESSION_COOKIE,
        path="/",
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

    An account with no password gets a link as well, and that is how it chooses
    its first: someone who signed up with Google and later tries the password
    form would otherwise be told a link is coming and never receive one. Opening
    the mailbox is the same proof whichever kind of account it is.
    """
    email_address = body.email.strip().lower()
    with system_session() as session:
        user = session.execute(
            select(User).where(
                User.email == email_address,
                User.kind == UserKind.CUSTOMER.value,
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
    if user.kind != UserKind.CUSTOMER.value:
        raise errors.ApiError(
            403, "ACCOUNT_REQUIRED", "Only a signed-in account can change its email."
        )
    if not user.password_hash:
        raise errors.ApiError(
            403,
            "PASSWORD_REQUIRED",
            "Changing your email needs a password. Use \"Forgot password\" on the "
            "sign-in page to choose one.",
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


# ---------------------------------------- sign in with Google, Apple, Facebook
#
# Two endpoints per provider and a redirect between them; core/social_auth.py
# holds the protocols and explains why every callback is on the root domain
# rather than on each restaurant's subdomain.
#
# Neither is a JSON API: a browser arrives at them by navigation and leaves by
# redirect, which is why they answer 302 and never a body. That also keeps the
# client secrets and the code exchange entirely server-side -- the browser
# never holds anything it could replay.

# Which column of a users row holds each provider's identity.
_SUBJECT_COLUMN = {
    social_auth.GOOGLE: "google_subject",
    social_auth.APPLE: "apple_subject",
    social_auth.FACEBOOK: "facebook_subject",
}


def _known_provider(provider: str) -> str:
    if provider not in social_auth.PROVIDERS:
        raise errors.ApiError(404, "NOT_FOUND", "Not found.")
    return provider


class SignInOptionsOut(BaseModel):
    providers: list[str]


@router.get("/sign-in-options", response_model=SignInOptionsOut)
def sign_in_options():
    """Which provider buttons the sign-in pages should draw: those with
    credentials behind them, on any address, restaurant or not."""
    return SignInOptionsOut(providers=social_auth.available())


@router.get(
    "/{provider}/start",
    include_in_schema=False,
    dependencies=[Depends(per_ip("social_start", limit=30, window_seconds=300))],
)
def social_start(
    request: Request,
    provider: str = Depends(_known_provider),
    next_path: str = Query("/", alias="next"),
    agreed: bool = Query(False),
):
    """Send the browser to the provider, remembering where it came from."""
    if not social_auth.configured(provider):
        raise errors.ApiError(
            503, "SOCIAL_UNAVAILABLE", "That sign-in is not available here."
        )

    # Where to come back to. Taken from the Host this was called on rather
    # than from a parameter: a redirect target a caller can choose is an open
    # redirect, and this one is handed to the provider and back.
    host = host_of(request.headers.get("host")) or settings.ROOT_DOMAIN
    if not host_on_allowed_origin(host):
        raise errors.ApiError(400, "BAD_ORIGIN", "Unknown storefront.")

    # Likewise the path: a path, never a URL, so it cannot leave this host.
    where = next_path if next_path.startswith("/") and not next_path.startswith("//") else "/"

    state, nonce = social_auth.issue_state(
        provider=provider, origin=host, next_path=where, agreed=agreed
    )
    response = RedirectResponse(social_auth.authorize_url(provider, state), status_code=302)
    samesite = social_auth.state_cookie_samesite(provider)
    response.set_cookie(
        key=social_auth.STATE_COOKIE,
        value=nonce,
        max_age=social_auth.STATE_TTL_MINUTES * 60,
        # On the parent domain: this runs on the restaurant's address and the
        # callback runs on the root, and a host-only cookie set here would not
        # be sent there. Every sign-in would fail the nonce check.
        domain=_cookie_domain(),
        httponly=True,
        samesite=samesite,
        # SameSite=None is refused by browsers without Secure, wherever it runs.
        secure=settings.ENV == "production" or samesite == "none",
        path="/",
    )
    return response


async def _callback_params(request: Request) -> dict[str, str]:
    """What the provider sent: the query string, or for Apple a posted form.

    Parsed by hand rather than with FastAPI's Form, which would bring in a
    multipart library for one urlencoded body.
    """
    params = dict(request.query_params)
    if request.method == "POST":
        body = (await request.body()).decode("utf-8", "replace")
        params.update({key: values[0] for key, values in parse_qs(body).items()})
    return params


@router.api_route(
    "/{provider}/callback",
    methods=["GET", "POST"],
    include_in_schema=False,
)
def social_callback(
    request: Request,
    provider: str = Depends(_known_provider),
    params: dict[str, str] = Depends(_callback_params),
):
    """Where the provider sends the browser back.

    Always ends in a redirect, never an error page: somebody who cancelled at
    the provider, or whose sign-in expired, belongs back on the sign-in page
    with a reason, not on a JSON document.

    303, because Apple arrives by POST and must leave by GET.
    """
    def back(origin: str, path: str, problem: str | None = None) -> RedirectResponse:
        scheme_host = _origin_url(origin)
        target = f"{scheme_host}{path}"
        if problem:
            target = (
                f"{scheme_host}/account/sign-in?error={quote(problem)}"
                f"&next={quote(path, safe='')}"
            )
        out = RedirectResponse(target, status_code=303)
        out.delete_cookie(social_auth.STATE_COOKIE, path="/", domain=_cookie_domain())
        return out

    state = params.get("state")
    if not state:
        raise errors.ApiError(400, "BAD_STATE", "That sign-in didn't complete.")

    try:
        read = social_auth.read_state(
            provider, state, request.cookies.get(social_auth.STATE_COOKIE)
        )
    except social_auth.SocialError as exc:
        # No trustworthy origin to send them to, so the platform root it is.
        return back(settings.ROOT_DOMAIN, "/", exc.code)

    # The customer pressed cancel, or the provider refused.
    code = params.get("code")
    if params.get("error") or not code:
        return back(read.origin, read.next_path, "social_failed")

    try:
        identity = social_auth.exchange(provider, code, params.get("user"))
        user_id = _customer_for_identity(identity, agreed=read.agreed)
    except social_auth.SocialError as exc:
        return back(read.origin, read.next_path, exc.code)

    response = back(read.origin, read.next_path)
    _set_session_cookie(response, user_id)
    return response


def _origin_url(host: str) -> str:
    """The storefront's own base URL, built from the template the rest of the
    app uses so the development port is not lost."""
    template = settings.PLATFORM_URL_TEMPLATE
    scheme, _, rest = template.partition("://")
    port = rest.partition("{root_domain}")[2]
    return f"{scheme}://{host}{port}"


def _customer_for_identity(identity: social_auth.SocialIdentity, *, agreed: bool) -> UUID:
    """The account this identity signs into, creating one if needed."""
    try:
        return _find_or_create_customer(identity, agreed=agreed)
    except IntegrityError:
        # Two first sign-ins raced, a double press or two tabs, and the unique
        # indexes let exactly one win. The second finds what the first made.
        return _find_or_create_customer(identity, agreed=agreed)


def _find_or_create_customer(identity: social_auth.SocialIdentity, *, agreed: bool) -> UUID:
    """One person, one account, whichever way they arrive.

    Matched on the provider's subject first, because that is the identity and
    it never changes. Falling back to the address is what stops somebody who
    registered with a password, or signed in with Google, from getting a second
    account or a dead end when they next press Apple -- safe only for a provider
    that vouches for the address (SocialIdentity.can_link_by_email).

    Registering verifies nothing, so an address on a password account proves
    nothing: anybody could have typed someone else's and be sitting on the
    account waiting for its real owner. So the first time a provider proves who
    owns the address, the password is cleared and every session ended. The
    worst case for an honest customer is asking for a new password by email.
    """
    subject_column = getattr(User, _SUBJECT_COLUMN[identity.provider])
    with system_session() as session:
        user = session.execute(
            select(User).where(
                subject_column == identity.subject,
                User.kind == UserKind.CUSTOMER.value,
            )
        ).scalar_one_or_none()

        if user is None:
            if not identity.email:
                raise social_auth.SocialError(
                    f"{identity.provider} shared no address", "social_no_email"
                )
            user = session.execute(
                select(User).where(
                    func.lower(User.email) == identity.email,
                    User.kind == UserKind.CUSTOMER.value,
                    User.deleted_at.is_(None),
                )
            ).scalar_one_or_none()

            if user is None:
                user = User(kind=UserKind.CUSTOMER.value, email=identity.email)
                session.add(user)
                log.info("creating a customer from a %s sign-in", identity.provider)
            elif not identity.can_link_by_email:
                raise social_auth.SocialError(
                    f"{identity.provider} address is already an account", "social_email_in_use"
                )
            else:
                if user.password_hash:
                    user.password_hash = None
                    user.sessions_valid_after = utcnow()
                log.info("linked %s sign-in to customer %s", identity.provider, user.id)
            setattr(user, _SUBJECT_COLUMN[identity.provider], identity.subject)

        # `is False`: a row created above has no value here until it is flushed.
        if user.deleted_at is not None or user.is_active is False:
            raise social_auth.SocialError("that account is closed", "account_closed")

        if identity.full_name and not user.full_name:
            user.full_name = identity.full_name
        if agreed and terms.needs_recording(user):
            terms.stamp(user)

        session.flush()
        return user.id
