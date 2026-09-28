import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer,
    SmallInteger, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base, TimestampMixin, uuid_pk


class OrderStatus(str, enum.Enum):
    """Frozen OrderStatus values (Appendix A.5), pickup plus the two a
    restaurant's own driver needs.

    A customer chooses delivery at checkout, or a manager hands a paid
    collection to one of the restaurant's drivers. Either way that is where
    READY_FOR_DELIVERY and OUT_FOR_DELIVERY come in -- the food is ready but
    not at a counter, and then it is with the driver.

    DRIVER_ASSIGNED, DRIVER_ACCEPTED and DELIVERY_FAILED from the baseline
    stay absent. The first two are not progress of the food but of the
    paperwork -- a manager may assign or reassign at any point, which as
    states would mean an edge from everywhere to everywhere -- so who is
    delivering is a column on the order. The third belongs with the retry and
    refund handling the delivery release brings.
    """
    PENDING_PAYMENT = "PENDING_PAYMENT"
    AUTO_ACCEPTED = "AUTO_ACCEPTED"
    PREPARING = "PREPARING"
    READY_FOR_PICKUP = "READY_FOR_PICKUP"
    READY_FOR_DELIVERY = "READY_FOR_DELIVERY"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class FulfillmentType(str, enum.Enum):
    """How the customer gets the food. Chosen at checkout; a manager
    assigning a driver to a collection also makes it a DELIVERY."""

    PICKUP = "PICKUP"
    DELIVERY = "DELIVERY"


# Normative transition matrix (Appendix A.6), pickup subset.
# Any transition not listed here is rejected with ORDER_STATE_CONFLICT.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    OrderStatus.PENDING_PAYMENT.value: {
        OrderStatus.AUTO_ACCEPTED.value,
        OrderStatus.EXPIRED.value,
        OrderStatus.CANCELLED.value,
    },
    OrderStatus.AUTO_ACCEPTED.value: {
        OrderStatus.PREPARING.value,
        OrderStatus.CANCELLED.value,
    },
    OrderStatus.PREPARING.value: {
        OrderStatus.READY_FOR_PICKUP.value,
        # Which of the two "ready" states an order reaches is decided by its
        # fulfillment_type, not by the person pressing the button.
        OrderStatus.READY_FOR_DELIVERY.value,
        OrderStatus.CANCELLED.value,
    },
    OrderStatus.READY_FOR_PICKUP.value: {
        OrderStatus.COMPLETED.value,
        OrderStatus.CANCELLED.value,
    },
    OrderStatus.READY_FOR_DELIVERY.value: {
        OrderStatus.OUT_FOR_DELIVERY.value,
        # Back to the counter: the customer rang to say they will collect
        # after all, and the food has not left the kitchen.
        OrderStatus.READY_FOR_PICKUP.value,
        OrderStatus.CANCELLED.value,
    },
    OrderStatus.OUT_FOR_DELIVERY.value: {
        # Delivered. There is no PIN at a doorstep, so the driver saying so is
        # what completes it -- recorded against them in order_events.
        OrderStatus.COMPLETED.value,
        OrderStatus.CANCELLED.value,
    },
    OrderStatus.COMPLETED.value: set(),
    OrderStatus.CANCELLED.value: set(),
    OrderStatus.EXPIRED.value: set(),
}


class RestaurantOrderCounter(Base):
    """Per-restaurant order number allocation.

    Rule: never SELECT MAX(order_number)+1 and never one global sequence.
    The row is locked FOR UPDATE inside the order-creation transaction so two
    concurrent checkouts cannot receive the same number.
    """

    __tablename__ = "restaurant_order_counters"
    __table_args__ = (
        CheckConstraint("next_order_number >= 1", name="ck_counter_min"),
    )

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), primary_key=True
    )
    next_order_number: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1001)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Order(Base, TimestampMixin):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("restaurant_id", "order_number", name="uq_order_number_per_restaurant"),
        CheckConstraint("subtotal_minor >= 0", name="ck_order_subtotal"),
        CheckConstraint("discount_minor >= 0", name="ck_order_discount"),
        CheckConstraint("tax_minor >= 0", name="ck_order_tax"),
        CheckConstraint("total_minor >= 0", name="ck_order_total"),
        CheckConstraint("delivery_fee_minor >= 0", name="ck_order_delivery_fee"),
        # A collection cannot have been charged for delivery.
        CheckConstraint(
            "delivery_fee_minor = 0 OR fulfillment_type = 'DELIVERY'",
            name="ck_order_delivery_fee_needs_delivery",
        ),
        CheckConstraint(
            "pickup_pin_failed_attempts >= 0 AND pickup_pin_failed_attempts <= 5",
            name="ck_order_pin_attempts",
        ),
        Index("ix_orders_board", "restaurant_id", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    order_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    customer_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )

    fulfillment_type: Mapped[str] = mapped_column(String(16), nullable=False, default="PICKUP")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payment_method: Mapped[str] = mapped_column(String(24), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    # Immutable commercial snapshots. Rule 4: later menu edits never rewrite
    # order history.
    subtotal_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    discount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    tax_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Stripe Tax, for restaurants on tax_mode STRIPE_TAX; all null or zero
    # under a flat rate. The calculation is what tax_minor and total_minor
    # came from; the transaction records the sale in the restaurant's tax
    # reports once paid; the reversed amount is how much of any refund has
    # already been recorded, so a redelivered refund never reverses twice.
    tax_calculation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tax_transaction_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tax_reversed_amount_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )

    customer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Who to call and whose name to call out, as given at checkout. Snapshots,
    # not a join to users: a customer changing their number next month must
    # not change what this order said. Null only on orders from before
    # checkout asked.
    contact_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(320))
    contact_address: Mapped[str | None] = mapped_column(String(300))
    contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Delivery. Null on a pickup order. Set at checkout when the customer
    # chooses delivery, or by a manager assigning one of the restaurant's own
    # drivers to an order agreed on the phone.
    driver_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    delivery_address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # What the delivery was charged, and the distance that chose it. Both are
    # facts about this order rather than pointers at a rule: a restaurant's
    # rings are replaced as a set whenever it edits them, so the ring that
    # applied here may not exist by next week, while "3.2 miles, $4" stays
    # true and stays explainable to whoever asks a year from now.
    delivery_fee_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    delivery_miles: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Encrypted, not hashed: the authenticated customer must be able to read
    # it back. Never logged, never in a URL, never in a push body.
    pickup_pin_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    pickup_pin_failed_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0
    )

    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set once the "order confirmed" email is accepted by the provider, so a
    # redelivered payment webhook never sends it twice.
    confirmation_email_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("unit_price_minor >= 0", name="ck_order_item_price"),
        CheckConstraint("quantity > 0", name="ck_order_item_qty"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False, index=True
    )
    menu_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("menu_items.id"), nullable=True
    )
    name_snapshot: Mapped[str] = mapped_column(String(180), nullable=False)
    unit_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    line_total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    item_note: Mapped[str | None] = mapped_column(String(280), nullable=True)

    # A combo is not a line of its own. It becomes one line per slot, priced
    # at what the item costs, and the saving lands in Order.discount_minor --
    # which is what that column was reserved for. These three columns are how
    # those lines are known to belong together: the kitchen has to plate a
    # meal deal as one thing, not as a burger and an unrelated drink.
    #
    # combo_group numbers the combos within one order, so ordering two
    # identical meal deals stays two meal deals rather than one with doubled
    # quantities. The name is snapshotted for the same reason every other
    # name here is: a receipt has to still read correctly after the combo is
    # renamed or withdrawn.
    combo_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("combos.id"), nullable=True
    )
    combo_name_snapshot: Mapped[str | None] = mapped_column(String(180), nullable=True)
    combo_group: Mapped[int | None] = mapped_column(Integer, nullable=True)

    order: Mapped["Order"] = relationship(back_populates="items")
    modifiers: Mapped[list["OrderItemModifier"]] = relationship(
        back_populates="order_item", cascade="all, delete-orphan"
    )


class OrderEventAction(str, enum.Enum):
    MARKED_READY = "MARKED_READY"
    COMPLETED_WITH_PIN = "COMPLETED_WITH_PIN"
    # Handed over without the customer's PIN, on a manager's say-so.
    COMPLETED_BY_OVERRIDE = "COMPLETED_BY_OVERRIDE"
    CANCELLED = "CANCELLED"
    # Delivery: who a manager gave it to, and the driver's two steps.
    ASSIGNED_DRIVER = "ASSIGNED_DRIVER"
    UNASSIGNED_DRIVER = "UNASSIGNED_DRIVER"
    PICKED_UP = "PICKED_UP"
    DELIVERED = "DELIVERED"


class OrderEvent(Base):
    """One staff action on an order: who, what, when, and for the exceptional
    ones, why. Written alongside the transition it records, never updated."""

    __tablename__ = "order_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OrderItemModifier(Base):
    __tablename__ = "order_item_modifiers"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_modifier_qty"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    order_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("order_items.id"), nullable=False, index=True
    )
    modifier_option_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    group_name_snapshot: Mapped[str] = mapped_column(String(180), nullable=False)
    option_name_snapshot: Mapped[str] = mapped_column(String(180), nullable=False)
    # Documented exception: may be negative. No non-negative CHECK here.
    unit_price_delta_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    order_item: Mapped["OrderItem"] = relationship(back_populates="modifiers")


class IdempotencyKey(Base):
    """Durable replay contract for critical mutations (section 17.1).

    Same key + same request hash returns the stored response.
    Same key + different request hash returns 409.
    Platform-owned. No tenant RLS policy.
    """

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("key", "actor_id", "endpoint", name="uq_idempotency"),
        Index("ix_idempotency_expiry", "expires_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SentEmail(Base):
    """An email that has been sent, or is being sent, once and only once.

    The key names what the email was about -- "order-cancelled/<order id>",
    "refund/<order id>/<total refunded>" -- and is claimed before the email
    goes out. A second attempt at the same key finds it taken and sends
    nothing: a task Celery runs twice, a Stripe webhook delivered again, or
    a refund both issued from the board and reported back by Stripe.

    The email provider cannot do this for us: SendGrid has no idempotency
    key. Nothing about the message itself is kept -- not the address, not
    the words -- only that it was sent and how that went.
    """

    __tablename__ = "sent_emails"

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    # SENDING while the provider is being asked; then SENT, FAILED or
    # NOT_CONFIGURED as email.Outcome says. COVERED when another email said
    # the same thing -- a cancellation that already mentioned its refund.
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
