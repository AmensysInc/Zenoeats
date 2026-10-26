import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer,
    SmallInteger, String, Text, UniqueConstraint, text,
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
        CheckConstraint("coupon_discount_minor >= 0", name="ck_order_coupon_discount"),
        # The coupon's share cannot exceed the discount it is part of.
        CheckConstraint(
            "coupon_discount_minor <= discount_minor", name="ck_order_coupon_within_discount"
        ),
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

    # The coupon this order used, if any, and what it took off.
    #
    # Three columns rather than one join, for the reason every other money
    # field here is a snapshot: coupon_code is what the customer typed and
    # what the receipt says, and it stays readable after the coupon is
    # retired, renamed or soft-deleted. coupon_discount_minor is the coupon's
    # own share of discount_minor -- the rest is what the combos took off --
    # so the two can be shown as separate lines without re-pricing a cart
    # that may no longer price the same way.
    #
    # coupon_id is kept as well, because it is what redemptions are counted
    # from: "how many people have used this" is a question about rows, and
    # counting them by code would conflate a retired coupon with a new one
    # created under the same code.
    coupon_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("coupons.id"), nullable=True, index=True
    )
    coupon_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # The dish given away under the rewards program, if one was claimed. One
    # column, so one free dish per order however many points the customer has
    # earned -- reaching a threshold unlocks it for good and deducts nothing,
    # so the limit has to live here rather than in the balance. Points at the
    # dish rather than at the reward row: this stays true after the reward is
    # withdrawn from the ladder, and what the line cost is on the order line,
    # where every other price on a receipt lives.
    reward_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("menu_items.id"), nullable=True
    )
    coupon_discount_minor: Mapped[int] = mapped_column(
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


class Coupon(Base, TimestampMixin):
    """A code a customer types at checkout to pay less.

    The restaurant's own promotion, not the platform's: every row belongs to
    one tenant, and two restaurants may both run WELCOME10 without either
    knowing. The code is stored as it will be compared -- upper case, no
    spaces -- because a customer typing "welcome10" on a phone keyboard has
    typed the same coupon, and a case-sensitive lookup would tell them
    otherwise.

    WHAT IT TAKES OFF. discount_kind and discount_value are read exactly as a
    combo's are (catalog.DiscountKind): PERCENT is basis points, AMOUNT is
    minor units. They are the same two words a manager already knows from the
    combo builder, and the same helper prices both.

    max_discount_minor is the one thing a combo has no use for. "20% off"
    with no ceiling is a different offer on a $10 order and on a $400 office
    lunch, and the ceiling is the only way to write the first without
    promising the second.

    WHAT LIMITS IT. Four independent limits, each nullable, because a real
    promotion uses some and not others:

      starts_at / ends_at    when it works at all
      min_subtotal_minor     the spend it needs
      max_redemptions        how many orders may ever use it
      max_per_customer       how many of those may be one person's

    Redemptions are counted from the orders that carry the coupon rather than
    kept as a number here. A counter would be a second truth about something
    the orders already know, and the two would part company the first time an
    order was cancelled -- see services/coupons.py, which counts them.

    IT IS NEVER HARD-DELETED while an order points at it. Taking a coupon off
    the dashboard is a soft delete: the orders it discounted keep their own
    snapshot of the code and the amount, and the row stays so a manager asking
    "what was WELCOME10?" six months later gets an answer.
    """

    __tablename__ = "coupons"
    __table_args__ = (
        # Per tenant, and only among the live ones: a code that was retired
        # may be created again, which is how a seasonal promotion comes back.
        Index(
            "uq_coupon_code_per_restaurant",
            "restaurant_id", "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint(
            "discount_kind IN ('PERCENT', 'AMOUNT')", name="ck_coupon_discount_kind"
        ),
        # NONE is a valid DiscountKind for a combo and meaningless here: a
        # coupon that takes nothing off is not a coupon, it is a code that
        # tells the customer their total is unchanged.
        CheckConstraint("discount_value > 0", name="ck_coupon_discount_positive"),
        CheckConstraint(
            "discount_kind <> 'PERCENT' OR discount_value <= 10000",
            name="ck_coupon_percent_within_range",
        ),
        CheckConstraint("min_subtotal_minor >= 0", name="ck_coupon_min_subtotal"),
        CheckConstraint(
            "max_discount_minor IS NULL OR max_discount_minor > 0",
            name="ck_coupon_max_discount",
        ),
        CheckConstraint(
            "max_redemptions IS NULL OR max_redemptions > 0",
            name="ck_coupon_max_redemptions",
        ),
        CheckConstraint(
            "max_per_customer IS NULL OR max_per_customer > 0",
            name="ck_coupon_max_per_customer",
        ),
        # A window that closes before it opens can never be used, and is
        # always a typo rather than an intention.
        CheckConstraint(
            "starts_at IS NULL OR ends_at IS NULL OR ends_at > starts_at",
            name="ck_coupon_window",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    # What the promotion is for, in the manager's words. Shown on the
    # dashboard and to the customer when the code is accepted, so "Student
    # Tuesdays" reads back instead of "STU10".
    description: Mapped[str | None] = mapped_column(String(160), nullable=True)

    discount_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    discount_value: Mapped[int] = mapped_column(Integer, nullable=False)
    max_discount_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    min_subtotal_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_redemptions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_per_customer: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Off without being deleted: a promotion paused at lunchtime and resumed
    # on Friday keeps its code, its limits and its history.
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CouponItemType(Base):
    """One heading a coupon applies to.

    No rows for a coupon means the whole cart, which is why this is a join
    table rather than a column: "everything" is the common case and is better
    said by absence than by a sentinel value.

    Headings rather than dishes on purpose -- see 0049. A scope of individual
    items goes stale the moment the menu is edited; a scope of "Burgers"
    keeps meaning what it meant.
    """

    __tablename__ = "coupon_item_types"
    __table_args__ = (
        UniqueConstraint("coupon_id", "item_type_id", name="uq_coupon_item_type"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    coupon_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("coupons.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    item_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("item_types.id", ondelete="CASCADE"),
        nullable=False,
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


class Offer(Base, TimestampMixin):
    """Something the storefront tells a customer, for a while.

    A title, a few words, a picture and a window. It carries no discount and
    changes no price: a promotion that actually takes money off is a coupon,
    which already exists and already prices itself. This is the poster in the
    window, and keeping the two apart means a poster can be written without
    the power to move a price.

    Both dates are optional and mean what their absence suggests: no start is
    "already running", no end is "until switched off". `is_active` is the
    switch that does not need a date typed to use it.
    """

    __tablename__ = "offers"
    __table_args__ = (
        # A window that closes before it opens can never show, and is always
        # a typo rather than an intention.
        CheckConstraint(
            "starts_at IS NULL OR ends_at IS NULL OR ends_at > starts_at",
            name="ck_offer_window",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # A key from POST /images, the same shape every other picture uses.
    image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def is_live(self, now: datetime) -> bool:
        """Whether a customer should see it at this moment.

        The one place the window is read, so the storefront, the admin list
        and any future digest agree about what "running" means rather than
        each writing the comparison again slightly differently.
        """
        if not self.is_active or self.deleted_at is not None:
            return False
        if self.starts_at is not None and now < self.starts_at:
            return False
        if self.ends_at is not None and now >= self.ends_at:
            return False
        return True
