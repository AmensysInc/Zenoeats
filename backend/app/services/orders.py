"""Order creation and state transitions.

Rule 16: an order row is always created before a charge is attempted.
Rule 5: critical mutations are idempotent and concurrency safe.
Rule 6: no external network call inside a database transaction.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core import errors
from app.core.crypto import encrypt_field, generate_pickup_pin
from app.db.base import utcnow
from app.models import (
    ALLOWED_TRANSITIONS, Order, OrderItem, OrderItemModifier, OrderStatus,
    Payment, PaymentMethod, PaymentStatus, Restaurant, RestaurantOrderCounter,
)
from app.services.pricing import PricedCart

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliveryDetails:
    """Where an order is going, and how far that turned out to be.

    Built from a priced delivery quote by the caller. The fee is not here: it
    travels on the cart, because it is part of what the customer is charged
    and has to have gone through the same pricing as everything else they are
    about to pay for.
    """

    address: str
    miles: float


def allocate_order_number(session: Session, restaurant_id: UUID) -> int:
    """Concurrency-safe per-restaurant order number.

    Locks the counter row FOR UPDATE inside the caller's transaction. Two
    simultaneous checkouts serialize here rather than colliding on the
    UNIQUE(restaurant_id, order_number) constraint.
    """
    counter = session.execute(
        select(RestaurantOrderCounter)
        .where(RestaurantOrderCounter.restaurant_id == restaurant_id)
        .with_for_update()
    ).scalar_one_or_none()

    if counter is None:
        counter = RestaurantOrderCounter(
            restaurant_id=restaurant_id, next_order_number=1001, updated_at=utcnow()
        )
        session.add(counter)
        session.flush()
        session.refresh(counter, with_for_update=True)

    number = counter.next_order_number
    counter.next_order_number = number + 1
    counter.updated_at = utcnow()
    session.flush()
    return number


def create_pending_order(
    session: Session,
    *,
    restaurant: Restaurant,
    customer_user_id: UUID,
    cart: PricedCart,
    customer_note: str | None,
    delivery: "DeliveryDetails | None" = None,
    contact_name: str | None = None,
    contact_phone: str | None = None,
) -> Order:
    """Create the order and its immutable snapshots. Commits nothing.

    The order is PENDING_PAYMENT. No money has moved and no Stripe call has
    been made. That happens in a separate request, after this transaction
    commits.

    delivery is where it is going and how far, priced already: the fee rides
    on the cart, because it is part of what the customer is about to be
    charged and must have gone through the same pricing the total did.
    """
    if not restaurant.is_orderable:
        raise errors.restaurant_not_orderable()

    # A fee with nowhere to deliver to is a bug rather than a cheap order, and
    # the database refuses it too. Caught here so it names itself.
    if cart.delivery_fee_minor and delivery is None:
        raise errors.validation_error("A delivery fee needs a delivery address.")

    now = utcnow()
    order = Order(
        restaurant_id=restaurant.id,
        order_number=allocate_order_number(session, restaurant.id),
        customer_user_id=customer_user_id,
        fulfillment_type="DELIVERY" if delivery else "PICKUP",
        delivery_address=delivery.address if delivery else None,
        delivery_fee_minor=cart.delivery_fee_minor if delivery else 0,
        delivery_miles=delivery.miles if delivery else None,
        status=OrderStatus.PENDING_PAYMENT.value,
        payment_method=PaymentMethod.STRIPE.value,
        currency=cart.currency,
        subtotal_minor=cart.subtotal_minor,
        discount_minor=cart.discount_minor,
        tax_minor=cart.tax_minor,
        total_minor=cart.total_minor,
        tax_calculation_id=cart.tax_calculation_id,
        customer_note=customer_note,
        contact_name=contact_name,
        contact_phone=contact_phone,
        pickup_pin_encrypted=encrypt_field(generate_pickup_pin()),
        expires_at=now + timedelta(minutes=settings.PENDING_PAYMENT_TTL_MINUTES),
    )
    session.add(order)
    session.flush()

    # Every line and its modifiers, written together by the one flush below.
    # The modifiers hang off their line through the relationship, which fills
    # in order_item_id when the rows go in, so nothing needs a line's id in
    # advance. This used to flush once per line to get that id: a ten-line
    # order was ten round trips, all made while the order counter above is
    # locked and every other checkout at this restaurant waits for it.
    session.add_all([
        OrderItem(
            restaurant_id=restaurant.id,
            order_id=order.id,
            menu_item_id=line.menu_item_id,
            name_snapshot=line.name,
            unit_price_minor=line.unit_price_minor,
            quantity=line.quantity,
            line_total_minor=line.line_total_minor,
            item_note=line.note,
            # Null on an ordinary line. Set on the lines a combo produced, so
            # the kitchen plates a meal deal as one thing and a receipt can
            # show what the deal was even after it is withdrawn.
            combo_id=line.combo_id,
            combo_name_snapshot=line.combo_name,
            combo_group=line.combo_group,
            modifiers=[
                OrderItemModifier(
                    restaurant_id=restaurant.id,
                    modifier_option_id=mod.option_id,
                    group_name_snapshot=mod.group_name,
                    option_name_snapshot=mod.option_name,
                    unit_price_delta_minor=mod.unit_price_delta_minor,
                    quantity=mod.quantity,
                )
                for mod in line.modifiers
            ],
        )
        for line in cart.lines
    ])

    session.add(
        Payment(
            restaurant_id=restaurant.id,
            order_id=order.id,
            method=PaymentMethod.STRIPE.value,
            status=PaymentStatus.PENDING.value,
            amount_minor=order.total_minor,
            currency=order.currency,
        )
    )
    session.flush()
    return order


def refund_minor(payment: Payment | None) -> int | None:
    """The amount going back to the customer, once a refund has been issued.

    A refund Stripe has only accepted has no amount recorded yet -- the
    webhook writes that -- but a restaurant refund is always the whole
    charge, so the charge is the figure to show until the webhook lands.
    None while nothing has been refunded.
    """
    if payment is None:
        return None
    if payment.refunded_minor > 0:
        return payment.refunded_minor
    if payment.status in (PaymentStatus.REFUND_PENDING.value, PaymentStatus.REFUNDED.value):
        return payment.amount_minor
    return None


def transition(order: Order, to_status: str, *, reason: str | None = None) -> None:
    """Apply a state change against the normative transition matrix."""
    allowed = ALLOWED_TRANSITIONS.get(order.status, set())
    if to_status not in allowed:
        raise errors.order_state_conflict(
            f"Cannot move an order from {order.status} to {to_status}."
        )

    order.status = to_status
    now = utcnow()

    if to_status == OrderStatus.AUTO_ACCEPTED.value:
        order.paid_at = order.paid_at or now
        order.expires_at = None
    elif to_status == OrderStatus.COMPLETED.value:
        order.completed_at = now
    elif to_status == OrderStatus.CANCELLED.value:
        order.cancelled_reason = reason

    order.updated_at = now
