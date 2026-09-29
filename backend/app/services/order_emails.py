"""The emails that follow an order after its confirmation.

    order_ready        a pick-up order is ready to collect
    order_on_the_way   the driver has picked it up
    order_delivered    the driver has handed it over
    order_cancelled    the restaurant cancelled it, with the refund if one was
                       issued
    refund_issued      money went back later: a refund from the board after
                       a cancellation without one, or from Stripe's dashboard

Each is sent at most once (notifications.send_once), keyed on what it is
about. A refund's key is the order and the total refunded so far, and a
cancellation that already told the customer about its refund claims that key
too -- so the refund Stripe reports back a moment later sends nothing more.

Queued by the endpoint or webhook that changed the order, after its
transaction commits, and sent by the worker: an email provider that is slow
or down must never hold up the kitchen, a driver, or a payment.
"""

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.session import system_session, tenant_session
from app.models import FulfillmentType, Order, OrderItem, Payment, Restaurant, User
from app.services import email, email_templates, notifications
from app.services.orders import refund_minor

log = logging.getLogger(__name__)

KINDS = ("order_ready", "order_on_the_way", "order_delivered", "order_cancelled", "refund_issued")


def queue(kind: str, restaurant_id: UUID, order_id: UUID, **extra) -> None:
    """Hand one of these emails to the worker.

    Called as a background task, after the response is sent -- by which point
    the change it is about has committed and the worker can read it. Best
    effort: the order has already moved on, and a broker outage costs the
    email, not the change.
    """
    from app.workers.tasks import send_order_email

    try:
        send_order_email.delay(kind, str(restaurant_id), str(order_id), extra)
    except Exception:
        log.warning("could not queue the %s email", kind, exc_info=True)


# ---------------------------------------------------------------- compose ---

def compose(
    kind: str,
    *,
    restaurant_name: str,
    slug: str,
    order: Order,
    customer_name: str | None,
    for_guest: bool,
    pickup_address: str | None = None,
    driver_name: str | None = None,
    refund: int | None = None,
    restaurant_phone: str | None = None,
) -> tuple[str, str, str]:
    """(subject, html, text) for one of these emails. `refund` is in minor
    units; everything else is as the customer should read it."""
    if kind not in KINDS:
        raise ValueError(f"no such order email: {kind}")
    out = email_templates.render(
        kind,
        restaurant_name=restaurant_name,
        restaurant_phone=restaurant_phone,
        customer_name=customer_name,
        order_number=order.order_number,
        delivering=order.fulfillment_type == FulfillmentType.DELIVERY.value,
        pickup_address=pickup_address,
        driver_name=driver_name,
        refund=notifications.money(refund, order.currency) if refund else None,
        order_url=notifications.order_url(slug, order.id, for_guest=for_guest),
        menu_url=notifications.storefront_url(slug, "/"),
    )
    return out.subject, out.html, out.text


# ------------------------------------------------------------------- send ---

def send(kind: str, restaurant_id: UUID, order_id: UUID, extra: dict | None = None) -> bool:
    """Send one of these emails about one order, once. True when sent now.

    `extra` carries what only the caller knew. For refund_issued, the total
    refunded before and after this refund, so the email can say how much
    this one was and its key can tell one refund from the next.
    """
    extra = extra or {}
    with tenant_session(restaurant_id) as session:
        order = session.execute(
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.items).selectinload(OrderItem.modifiers))
        ).scalar_one_or_none()
        restaurant = session.get(Restaurant, restaurant_id)
        if order is None or restaurant is None or order.paid_at is None:
            return False
        payment = session.execute(
            select(Payment).where(Payment.order_id == order.id)
        ).scalar_one_or_none()
        refunded = refund_minor(payment)
        charged = payment.amount_minor if payment else order.total_minor
        restaurant_name, slug, phone = restaurant.name, restaurant.slug, restaurant.phone
        pickup_address = restaurant.pickup_address_line or None
        driver_id = order.driver_user_id
        session.expunge_all()

    covers = None
    refund = None
    if kind == "order_cancelled":
        key = f"order-cancelled/{order_id}"
        # The refund, if the cancellation came with one. Its own key is
        # claimed alongside, so Stripe reporting it back sends nothing more.
        if refunded:
            refund = refunded
            covers = f"refund/{order_id}/{refunded}"
    elif kind == "refund_issued":
        before = int(extra.get("refunded_before") or 0)
        after = int(extra.get("refunded_after") or refunded or charged)
        if after <= before:
            return False
        refund = after - before
        key = f"refund/{order_id}/{after}"
    else:
        key = f"{kind.replace('_', '-')}/{order_id}"

    customer = notifications.customer_contact(order)
    if customer is None:
        return False

    driver_name = None
    if kind == "order_on_the_way" and driver_id:
        with system_session() as session:
            driver = session.get(User, driver_id)
            if driver is not None and driver.full_name:
                # The first name only, as on the tracking page: someone to
                # look out for, not someone to look up.
                driver_name = driver.full_name.split(" ")[0]

    subject, body_html, body_text = compose(
        kind,
        restaurant_name=restaurant_name, slug=slug, order=order,
        customer_name=customer.first_name, for_guest=customer.for_guest,
        pickup_address=pickup_address, driver_name=driver_name, refund=refund,
        restaurant_phone=phone,
    )
    if covers:
        # Before sending, not after: Stripe's report of the same refund can
        # arrive while this email is on its way, and should find it covered.
        notifications.mark_covered(restaurant_id, covers)
    outcome = notifications.send_once(restaurant_id, key, email.Email(
        to=customer.to, subject=subject, html=body_html, text=body_text, idempotency_key=key,
    ))
    return bool(outcome and outcome.sent)
