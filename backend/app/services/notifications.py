"""The emails Zenoeats itself sends, and what they share.

Order confirmations and staff invitations are composed here; the emails
that follow an order afterwards are in order_emails.py, built on the helpers
below: where an order's emails go, the link to its page, and send_once.

Content rules, both from how the rest of the platform treats the same data:

  * The pickup PIN is never in an email. It is what releases the food at the
    counter, and it stays behind sign-in on the order page (core/crypto.py:
    "never put in a notification body"). The email links there instead.

  * A staff invitation carries the temporary password it issued, so the new
    member can sign in without the admin passing it on by hand. That was a
    decision, not an oversight: it means whoever reads the email can sign in
    first. What bounds it is that the password must be replaced at first
    sign-in and stops working then -- so it is only ever sent while that is
    still pending, and never for a login that already has its own password.

  * Everything a restaurant or customer typed -- item names, the restaurant's
    name, notes -- is HTML-escaped before it goes into a message. The
    templates do that themselves (services/email_templates.py).

The words and layout of each email live in app/templates/email/, one folder
per email. This module decides what goes into them.
"""

import logging
from dataclasses import dataclass
from datetime import timezone
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.base import utcnow
from app.db.session import system_session, tenant_session
from app.core import guest_auth
from app.models import (
    Order, OrderItem, Restaurant, RestaurantUser, SentEmail, StaffStatus, User, UserKind,
)
from app.services import clerk_customers, email, email_templates

log = logging.getLogger(__name__)

ROLE_WORDS = {
    "ADMIN": "an admin",
    "MANAGER": "a manager",
    "KITCHEN": "kitchen staff",
    "CASHIER": "a cashier",
    "DRIVER": "a driver",
    "IT_SUPPORT": "IT support",
}

def storefront_url(slug: str, path: str = "/") -> str:
    base = settings.STOREFRONT_URL_TEMPLATE.format(slug=slug, root_domain=settings.ROOT_DOMAIN)
    return base.rstrip("/") + path


def money(amount_minor: int, currency: str) -> str:
    sign = "-" if amount_minor < 0 else ""
    whole, cents = divmod(abs(amount_minor), 100)
    if currency.upper() == "USD":
        return f"{sign}${whole:,}.{cents:02d}"
    return f"{sign}{whole:,}.{cents:02d} {currency.upper()}"


def order_url(slug: str, order_id: UUID, *, for_guest: bool) -> str:
    """The customer's order page.

    A guest's link carries a view token. Their session is a cookie in one
    browser, so without it an email opened on a laptop, or after clearing
    the browser, would lead to "we can't find that order" -- and the pickup
    PIN it points at would be unreachable.
    """
    path = f"/orders/{order_id}"
    if for_guest:
        # In the fragment, never the query. A browser does not send the part
        # after "#" to any server, so the token stays out of every access log
        # and Referer on the way; the page reads it from there and hands it to
        # the API in a header.
        path += f"#t={guest_auth.issue_order_token(order_id)}"
    return storefront_url(slug, path)


@dataclass(frozen=True)
class Customer:
    """Who an order's emails go to."""

    to: str
    first_name: str | None
    for_guest: bool


def customer_contact(order: Order) -> Customer | None:
    """Where this order's emails go, or None for nowhere.

    The address given at checkout first, then the account's. None when
    neither is a real address -- a Clerk customer still awaiting
    verification has a placeholder, and a receipt sent there reaches nobody.
    """
    with system_session() as session:
        customer = session.get(User, order.customer_user_id)
        if customer is None:
            return None
        to = order.contact_email or clerk_customers.receipt_address(customer)
        if not to:
            return None
        return Customer(
            to=to,
            first_name=(order.contact_name or customer.full_name or "").split(" ")[0] or None,
            for_guest=customer.kind == UserKind.GUEST.value,
        )


def send_once(restaurant_id: UUID, key: str, message: "email.Email") -> "email.Outcome | None":
    """Send `message` unless an email under `key` already went. None if it had.

    The key is claimed before sending (sent_emails), so two attempts that
    race each other send one email between them. A failure worth retrying
    gives the claim back, so the retry can take it; any other outcome keeps
    it, because trying again would not change the answer.
    """
    with tenant_session(restaurant_id) as session:
        claimed = session.execute(
            pg_insert(SentEmail)
            .values(restaurant_id=restaurant_id, key=key, status="SENDING")
            .on_conflict_do_nothing()
            .returning(SentEmail.key)
        ).first()
    if claimed is None:
        log.info("email %s already sent; not sending it again", key)
        return None

    try:
        outcome = email.deliver(message)
    except email.RetryableEmailError:
        with tenant_session(restaurant_id) as session:
            session.execute(
                delete(SentEmail).where(
                    SentEmail.restaurant_id == restaurant_id, SentEmail.key == key
                )
            )
        raise

    with tenant_session(restaurant_id) as session:
        row = session.get(SentEmail, (restaurant_id, key))
        if row is not None:
            row.status = outcome.status
            row.updated_at = utcnow()
    return outcome


def mark_covered(restaurant_id: UUID, key: str) -> None:
    """Record that another email already said what `key` would say, so it is
    not sent on its own. Nothing happens if it was sent already."""
    with tenant_session(restaurant_id) as session:
        session.execute(
            pg_insert(SentEmail)
            .values(restaurant_id=restaurant_id, key=key, status="COVERED")
            .on_conflict_do_nothing()
        )


def order_lines(order: Order) -> list[dict]:
    """The order's lines, as every order email lists them."""
    return [
        {
            "quantity": item.quantity,
            "name": item.name_snapshot,
            "options": ", ".join(m.option_name_snapshot for m in item.modifiers),
            "total": money(item.line_total_minor, order.currency),
        }
        for item in order.items
    ]


def order_amounts(order: Order) -> dict:
    """The figures under an order, formatted. A figure that is zero and would
    only be noise -- no discount, no delivery fee -- is None."""
    return {
        "subtotal": money(order.subtotal_minor, order.currency),
        "discount": money(-order.discount_minor, order.currency) if order.discount_minor else None,
        # Without its fee a delivery's figures would not add up to its total.
        "delivery_fee": (
            money(order.delivery_fee_minor, order.currency) if order.delivery_fee_minor else None
        ),
        "tax": money(order.tax_minor, order.currency),
        "total": money(order.total_minor, order.currency),
    }


# ------------------------------------------------------ order confirmation ---

def compose_order_confirmation(
    *,
    restaurant_name: str,
    slug: str,
    order: Order,
    customer_name: str | None,
    for_guest: bool = False,
) -> tuple[str, str, str]:
    """(subject, html, text) for a paid order."""
    out = email_templates.render(
        "order_confirmation",
        restaurant_name=restaurant_name,
        customer_name=customer_name,
        order_number=order.order_number,
        items=order_lines(order),
        amounts=order_amounts(order),
        # A delivery has no PIN: nobody collects it at a counter. Promising
        # one sends the customer looking for something that does not exist.
        delivering=order.fulfillment_type == "DELIVERY",
        order_url=order_url(slug, order.id, for_guest=for_guest),
    )
    return out.subject, out.html, out.text


def send_order_confirmation(restaurant_id: UUID, order_id: UUID) -> bool:
    """Send the confirmation for a paid order, once. True when sent now."""
    with tenant_session(restaurant_id) as session:
        order = session.execute(
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.items).selectinload(OrderItem.modifiers))
        ).scalar_one_or_none()
        restaurant = session.get(Restaurant, restaurant_id)
        if order is None or restaurant is None:
            return False
        if order.confirmation_email_sent_at is not None or order.paid_at is None:
            return False
        restaurant_name, slug = restaurant.name, restaurant.slug
        session.expunge_all()

    customer = customer_contact(order)
    if customer is None:
        return False

    subject, body_html, body_text = compose_order_confirmation(
        restaurant_name=restaurant_name, slug=slug, order=order,
        customer_name=customer.first_name, for_guest=customer.for_guest,
    )
    sent = email.send(email.Email(
        to=customer.to, subject=subject, html=body_html, text=body_text,
        idempotency_key=f"order-confirmation/{order_id}",
    ))
    if sent:
        with tenant_session(restaurant_id) as session:
            row = session.get(Order, order_id)
            if row is not None and row.confirmation_email_sent_at is None:
                row.confirmation_email_sent_at = utcnow()
    return sent


# ------------------------------------------------------- staff invitation ---

def compose_staff_invitation(
    *, restaurant_name: str, slug: str, role_code: str, has_temporary_password: bool,
    temporary_password: str | None = None,
) -> tuple[str, str, str]:
    out = email_templates.render(
        "staff_invitation",
        restaurant_name=restaurant_name,
        role=ROLE_WORDS.get(role_code, role_code.lower()),
        temporary_password=temporary_password,
        has_temporary_password=has_temporary_password,
        sign_in_url=storefront_url(slug, "/manage/login"),
    )
    return out.subject, out.html, out.text


def send_staff_invitation(
    restaurant_id: UUID, membership_id: UUID, temporary_password: str | None = None
) -> bool:
    with tenant_session(restaurant_id) as session:
        membership = session.get(RestaurantUser, membership_id)
        restaurant = session.get(Restaurant, restaurant_id)
        if membership is None or restaurant is None:
            return False
        if membership.status != StaffStatus.INVITED.value:
            return False  # accepted or revoked since; nothing to invite to
        user_id, role_code = membership.user_id, membership.role_code
        invited_at = membership.invited_at
        restaurant_name, slug = restaurant.name, restaurant.slug

    with system_session() as session:
        user = session.get(User, user_id)
        if user is None:
            return False
        to, has_temporary_password = user.email, bool(user.must_change_password)

    # Only while it still works. A retry can run long after the invitation --
    # by then they may have signed in and chosen their own, and a stale
    # password in an inbox is a thing to leave out, not to resend.
    subject, body_html, body_text = compose_staff_invitation(
        restaurant_name=restaurant_name, slug=slug, role_code=role_code,
        has_temporary_password=has_temporary_password,
        temporary_password=temporary_password if has_temporary_password else None,
    )
    # Keyed on when the invitation was issued, so re-inviting someone later
    # -- or resending it -- sends a fresh email while a retried send of the
    # same one does not.
    stamp = invited_at.astimezone(timezone.utc).isoformat() if invited_at else "none"
    outcome = email.deliver(email.Email(
        to=to, subject=subject, html=body_html, text=body_text,
        idempotency_key=f"staff-invitation/{membership_id}/{stamp}",
    ))
    record_invitation_outcome(restaurant_id, membership_id, outcome, invited_at)
    return outcome.sent


def record_invitation_outcome(
    restaurant_id: UUID, membership_id: UUID, outcome: "email.Outcome", invited_at=None,
) -> None:
    """Note on the membership what became of its invitation email, for the
    team list. Only if the invitation is still the one this email was for: a
    resend issued while this one was in flight owns the status now, and an
    older attempt finishing late must not overwrite it."""
    with tenant_session(restaurant_id) as session:
        membership = session.get(RestaurantUser, membership_id)
        if membership is None:
            return
        if invited_at is not None and membership.invited_at != invited_at:
            return
        membership.invitation_email_status = outcome.status
        membership.invitation_email_at = utcnow()
        membership.invitation_email_problem = outcome.problem[:200] if outcome.problem else None
