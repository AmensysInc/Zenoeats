"""The emails about a customer's account itself.

    customer_welcome   a new account, the first time it is used at a
                       restaurant: "Welcome to Spice House"
    account_closed     the account has been closed and its details removed

A customer account is Zenoeats', not one restaurant's -- Clerk holds one
sign-in for every storefront, and says nothing about which one a person
signed up on. So the welcome is sent from the storefront itself: the first
time a brand-new account loads a restaurant's site, that restaurant welcomes
it. Accounts older than a day are never welcomed, so nobody who already
orders here is greeted as new the day this shipped.

Closing is told once, by whichever path closes the account first: the
customer's own "close account" on a storefront, or the user.deleted webhook
when the account is deleted in Clerk. The address is read before closing
erases it, and travels to the worker sealed with the field key -- the
broker keeps what it holds on disk, and the address is exactly what closing
promised to remove.
"""

import logging
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select

from app.core import crypto
from app.db.base import utcnow
from app.db.session import system_session
from app.models import Restaurant, User, UserKind
from app.services import clerk_customers, email, email_templates, notifications

log = logging.getLogger(__name__)

# How new an account must be to be welcomed.
WELCOME_WITHIN = timedelta(days=1)


def is_new(user: User) -> bool:
    """A real account, made within the last day, with a verified address."""
    return (
        user.kind == UserKind.CUSTOMER.value
        and user.created_at is not None
        and utcnow() - user.created_at <= WELCOME_WITHIN
        and not clerk_customers.has_placeholder_email(user)
    )


def queue_welcome(slug: str, user_id: UUID) -> None:
    """Queue the welcome for this account at this restaurant.

    The storefront asks who is signed in on every page, so this runs on
    every page a new customer opens in their first day. A marker in Redis
    lets only the first of those reach the queue; the send itself is once
    per restaurant regardless (sent_emails), so a Redis that is down costs
    a few extra tasks, not a second email.
    """
    from app.core.ratelimit import runtime_redis
    from app.workers.tasks import send_account_email

    try:
        if not runtime_redis().set(f"welcome-queued:{slug}:{user_id}", 1, nx=True,
                                   ex=int(WELCOME_WITHIN.total_seconds())):
            return
    except Exception:
        log.warning("welcome marker unavailable; queueing anyway", exc_info=True)
    try:
        send_account_email.delay("customer_welcome", {"slug": slug, "user_id": str(user_id)})
    except Exception:
        log.warning("could not queue the welcome email", exc_info=True)


def queue_closed(to: str, first_name: str | None, restaurant_id: UUID | None) -> None:
    """Queue the account-closed email. `restaurant_id` is the storefront it
    was closed from, or None when it was deleted in Clerk."""
    from app.workers.tasks import send_account_email

    try:
        send_account_email.delay("account_closed", {
            "sealed_to": crypto.encrypt_field(to),
            "first_name": first_name,
            "restaurant_id": str(restaurant_id) if restaurant_id else None,
        })
    except Exception:
        log.warning("could not queue the account-closed email", exc_info=True)


def compose(kind: str, **context) -> tuple[str, str, str]:
    out = email_templates.render(kind, **context)
    return out.subject, out.html, out.text


def send(kind: str, args: dict) -> bool:
    """Send one of these emails. True when it went now."""
    if kind == "customer_welcome":
        return _send_welcome(args["slug"], UUID(args["user_id"]))
    if kind == "account_closed":
        return _send_closed(args)
    raise ValueError(f"no such account email: {kind}")


def _send_welcome(slug: str, user_id: UUID) -> bool:
    with system_session() as session:
        restaurant = session.execute(
            select(Restaurant).where(Restaurant.slug == slug, Restaurant.deleted_at.is_(None))
        ).scalar_one_or_none()
        user = session.get(User, user_id)
        if restaurant is None or user is None or not is_new(user) or not user.is_active:
            return False
        restaurant_id, restaurant_name = restaurant.id, restaurant.name
        to, name = user.email, (user.full_name or "").split(" ")[0] or None

    subject, body_html, body_text = compose(
        "customer_welcome",
        restaurant_name=restaurant_name, name=name, email=to,
        menu_url=notifications.storefront_url(slug, "/"),
    )
    key = f"customer-welcome/{user_id}"
    outcome = notifications.send_once(restaurant_id, key, email.Email(
        to=to, subject=subject, html=body_html, text=body_text, idempotency_key=key,
    ))
    return bool(outcome and outcome.sent)


def _send_closed(args: dict) -> bool:
    restaurant_name = None
    if args.get("restaurant_id"):
        with system_session() as session:
            restaurant = session.get(Restaurant, UUID(args["restaurant_id"]))
            restaurant_name = restaurant.name if restaurant is not None else None

    subject, body_html, body_text = compose(
        "account_closed", name=args.get("first_name"), restaurant_name=restaurant_name,
    )
    # Not through send_once: there may be no restaurant to record it under,
    # and closing happens once -- the caller only queues this for the close
    # that actually changed the account.
    return email.deliver(email.Email(
        to=crypto.decrypt_field(args["sealed_to"]), subject=subject, html=body_html,
        text=body_text, idempotency_key="account-closed",
    )).sent
