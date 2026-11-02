import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index,
    Integer, String, Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid_pk


class PaymentStatus(str, enum.Enum):
    """Frozen PaymentStatus values (Appendix A.3)."""
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    PAID = "PAID"
    FAILED = "FAILED"
    REFUND_PENDING = "REFUND_PENDING"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
    REFUNDED = "REFUNDED"


class PaymentMethod(str, enum.Enum):
    STRIPE = "STRIPE"


class StripeEventStatus(str, enum.Enum):
    RECEIVED = "RECEIVED"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"
    IGNORED = "IGNORED"


class RestaurantPaymentAccount(Base, TimestampMixin):
    """Stripe Connect mapping. Tenant owned.

    Activation requires charges_enabled. Rule 25: a connected-account event
    may mutate tenant state only after its account matches this row.
    """

    __tablename__ = "restaurant_payment_accounts"

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, unique=True
    )
    provider: Mapped[str] = mapped_column(String(24), nullable=False, default="STRIPE")
    stripe_account_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    charges_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payouts_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    details_submitted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    onboarding_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")


class Payment(Base, TimestampMixin):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount_minor >= 0", name="ck_payment_amount"),
        # Rule 29: at most one payment attempt per order may ever reach
        # successful capture. Enforced by a partial unique index created in
        # the migration, not here, because SQLAlchemy Index() with a
        # postgresql_where clause is easier to read in the migration file.
        Index("ix_payments_intent", "stripe_payment_intent_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False, index=True
    )
    method: Mapped[str] = mapped_column(String(32), nullable=False, default=PaymentMethod.STRIPE.value)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PaymentStatus.PENDING.value, index=True
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    stripe_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stripe_client_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Set exactly once when the provider first confirms success. Never
    # cleared by a later refund, which is what makes the partial unique
    # index a durable guarantee.
    succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Cumulative, as Stripe reports it on the charge, and never more than the
    # payment. The status says whether a refund happened; this says how much.
    refunded_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class StripeEvent(Base):
    """Durable webhook inbox. Platform owned, no tenant RLS policy.

    Section 8.2: verify signature, persist with a UNIQUE stripe_event_id,
    return 200 for duplicates, enqueue Celery by row id, return quickly.
    """

    __tablename__ = "stripe_events"
    __table_args__ = (
        Index("ix_stripe_events_status", "status", "received_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    stripe_event_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=StripeEventStatus.RECEIVED.value
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # NULL only for platform-level (non-Connect) events.
    stripe_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
