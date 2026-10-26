"""The loyalty engine: a stamp card a restaurant configures.

A qualifying purchase adds a stamp. Reaching the required number issues a
reward and takes those stamps back off the card, the way a paper one works.
What counts as qualifying, how many are needed and what the reward may be
taken as are all rows rather than code, so "nine orders of ten dollars, then
a free meal" and "nine burgers, then a burger" are the same engine.

THE LEDGER IS THE TRUTH. loyalty_transactions holds every stamp ever earned
or taken back; customer_loyalty holds how many are on the current card. The
second is a cache of the first and can be rebuilt by summing it, which is
what keeps it from becoming a second opinion -- and summing a ledger on
every storefront page view is a query that gets slower forever, which is why
the cache exists at all.

See 0053, which explains why this replaced the threshold ladder of 0052.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer,
    String, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, uuid_pk


class PurchaseType(str, enum.Enum):
    """What earns a stamp.

    Each is a branch in services/loyalty.py rather than a scheme of its own,
    which is the point of the engine: what is won, how it expires and how it
    is redeemed are the same for all of them.

    Spend accumulation -- "spend a hundred dollars, get five back" -- is the
    obvious next one and is deliberately absent: it counts money rather than
    purchases, and pays in money rather than in a dish, so it needs a reward
    this model does not have.
    """

    COMPLETED_ORDERS = "COMPLETED_ORDERS"
    MENU_ITEM = "MENU_ITEM"
    # One stamp per friend who arrives on this customer's link and buys
    # something. What is won is the same reward a filled card wins, which is
    # why this is a purchase type and not a scheme of its own -- see 0054.
    REFERRAL = "REFERRAL"


class ReferralStatus(str, enum.Enum):
    """How far an invitation has got.

    SENT is a link shared with somebody who may never use it. JOINED is a
    customer who arrived on it. QUALIFIED is that customer having actually
    bought something, which is the only one that pays.
    """

    SENT = "SENT"
    JOINED = "JOINED"
    QUALIFIED = "QUALIFIED"


class StampReason(str, enum.Enum):
    EARN = "EARN"
    REVERSE = "REVERSE"


class RewardStatus(str, enum.Enum):
    ISSUED = "ISSUED"
    REDEEMED = "REDEEMED"
    EXPIRED = "EXPIRED"


class LoyaltyProgram(Base, TimestampMixin):
    """One rule: what counts, how many, and what is won.

    A restaurant may run several at once -- a meal card and a burger card --
    and a customer holds a separate card for each.
    """

    __tablename__ = "loyalty_programs"
    __table_args__ = (
        CheckConstraint(
            "purchase_type IN ('COMPLETED_ORDERS','MENU_ITEM','REFERRAL')",
            name="ck_loyalty_purchase_type",
        ),
        CheckConstraint("required_count > 0", name="ck_loyalty_required_positive"),
        CheckConstraint("minimum_spend_minor >= 0", name="ck_loyalty_minimum_spend"),
        CheckConstraint(
            "reward_expires_days IS NULL OR reward_expires_days > 0",
            name="ck_loyalty_expiry_positive",
        ),
        CheckConstraint(
            "(purchase_type = 'MENU_ITEM'"
            " AND (qualifying_item_id IS NULL) <> (qualifying_item_type_id IS NULL))"
            " OR (purchase_type IN ('COMPLETED_ORDERS','REFERRAL')"
            " AND qualifying_item_id IS NULL AND qualifying_item_type_id IS NULL)",
            name="ck_loyalty_qualifier_matches_type",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    purchase_type: Mapped[str] = mapped_column(String(24), nullable=False)
    required_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # COMPLETED_ORDERS only. Zero means every completed order qualifies.
    minimum_spend_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    # MENU_ITEM only, and exactly one of the two. A named dish counts only
    # itself; a named heading counts everything filed under it, which is what
    # "nine burgers" usually means and sometimes does not.
    qualifying_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("menu_items.id"), nullable=True
    )
    qualifying_item_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("item_types.id"), nullable=True
    )
    # Null means a won reward never expires, which is the kinder default.
    reward_expires_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    choices: Mapped[list["LoyaltyProgramItem"]] = relationship(
        back_populates="program",
        cascade="all, delete-orphan",
        order_by="LoyaltyProgramItem.sort_order, LoyaltyProgramItem.id",
    )


class LoyaltyProgramItem(Base):
    """One dish this program's reward may be taken as.

    A list rather than a single column, because the reward is a choice: a
    customer who has filled a card picks the fries or the drink or the
    burger. No price cap anywhere -- the reward is these dishes, free,
    whatever they cost this month.
    """

    __tablename__ = "loyalty_program_items"
    __table_args__ = (
        UniqueConstraint("program_id", "item_id", name="uq_loyalty_program_item"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("loyalty_programs.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("menu_items.id"), nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    program: Mapped["LoyaltyProgram"] = relationship(back_populates="choices")


class LoyaltyTransaction(Base):
    """One stamp movement: earned when an order completed, taken back when
    it was refunded.

    Append-only. A refund writes a REVERSE row rather than deleting the EARN,
    so what happened stays legible and the count falls out of the sum either
    way.

    The unique constraint is the idempotency: one EARN per program per order,
    enforced by the database rather than by a check in Python that a retried
    webhook could race.
    """

    __tablename__ = "loyalty_transactions"
    __table_args__ = (
        CheckConstraint("reason IN ('EARN','REVERSE')", name="ck_loyalty_txn_reason"),
        CheckConstraint("stamps <> 0", name="ck_loyalty_txn_stamps_nonzero"),
        UniqueConstraint(
            "program_id", "order_id", "reason", name="uq_loyalty_txn_once_per_order"
        ),
        Index("ix_loyalty_txn_customer", "customer_user_id", "program_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False
    )
    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("loyalty_programs.id"), nullable=False
    )
    customer_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False
    )
    # Positive to earn, negative to take back. Several at once for an item
    # program: three burgers in one order is three stamps.
    stamps: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class CustomerLoyalty(Base):
    """How many stamps are on this customer's current card for this program.

    A cache of loyalty_transactions, kept because every storefront page asks
    for it. Rebuildable by summing the ledger, which is what stops it being a
    second truth -- and the reason it is safe to read it everywhere.
    """

    __tablename__ = "customer_loyalty"
    __table_args__ = (
        CheckConstraint("current_count >= 0", name="ck_customer_loyalty_non_negative"),
    )

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("loyalty_programs.id"), primary_key=True
    )
    customer_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )
    current_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class LoyaltyReward(Base, TimestampMixin):
    """A reward that has been won: issued, maybe redeemed, maybe expired.

    A row per reward rather than a flag on the customer, because two
    completed cards is two free dishes and a boolean cannot hold that. Which
    dish it was taken as is recorded on redemption -- the program offers a
    choice, so the answer is not knowable until it is spent.
    """

    __tablename__ = "loyalty_rewards"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ISSUED','REDEEMED','EXPIRED')", name="ck_loyalty_reward_status"
        ),
        CheckConstraint(
            "(status = 'REDEEMED' AND redeemed_order_id IS NOT NULL"
            " AND redeemed_item_id IS NOT NULL AND redeemed_at IS NOT NULL)"
            " OR (status <> 'REDEEMED' AND redeemed_order_id IS NULL"
            " AND redeemed_item_id IS NULL AND redeemed_at IS NULL)",
            name="ck_loyalty_reward_redemption_complete",
        ),
        Index("ix_loyalty_rewards_holder", "customer_user_id", "status"),
        Index(
            "uq_loyalty_reward_per_order", "redeemed_order_id",
            unique=True, postgresql_where=text("redeemed_order_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("loyalty_programs.id"), nullable=False
    )
    customer_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RewardStatus.ISSUED.value
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    redeemed_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id"), nullable=True
    )
    redeemed_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("menu_items.id"), nullable=True
    )
    redeemed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def is_spendable(self, now: datetime) -> bool:
        """Whether this reward can be taken on an order right now.

        Expiry is read rather than swept: a reward that lapsed overnight is
        not spendable the moment it lapses, with or without a job having run
        to say so. The sweep that writes EXPIRED is housekeeping for the
        reports, not the gate.
        """
        if self.status != RewardStatus.ISSUED.value:
            return False
        return self.expires_at is None or now < self.expires_at


class Referral(Base, TimestampMixin):
    """One invitation: who shared a link, who used it, and whether it paid.

    The invitee's name and number are optional and are the customer's own
    note to themselves -- the server sends nothing to either. There is no SMS
    in this stack and sending business SMS in the US needs a vendor, a cost
    per message and A2P registration with documented opt-in; a link forwarded
    by a friend from their own phone arrives from a number the recipient
    knows, which is both likelier to be read and not a cold message to a
    stranger. See 0054.
    """

    __tablename__ = "referrals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('SENT','JOINED','QUALIFIED')", name="ck_referral_status"
        ),
        CheckConstraint(
            "invitee_user_id IS NULL OR invitee_user_id <> referrer_user_id",
            name="ck_referral_not_self",
        ),
        CheckConstraint(
            "(status = 'QUALIFIED' AND qualifying_order_id IS NOT NULL"
            " AND qualified_at IS NOT NULL)"
            " OR (status <> 'QUALIFIED' AND qualifying_order_id IS NULL"
            " AND qualified_at IS NULL)",
            name="ck_referral_qualified_complete",
        ),
        Index(
            "uq_referral_invitee", "restaurant_id", "invitee_user_id",
            unique=True, postgresql_where=text("invitee_user_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    referrer_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    invitee_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    invitee_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    invitee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ReferralStatus.SENT.value
    )
    qualifying_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id"), nullable=True
    )
    qualified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ReferralCode(Base):
    """The link one customer shares at one restaurant.

    A table of its own because a code identifies a person while an invitation
    is an event: carrying the code on every invitation made a customer's
    second invitation collide with their first, which is a thing the model
    should not have been able to express.

    One row per customer per restaurant, and unique across the restaurant so
    a claim resolves to exactly one person.
    """

    __tablename__ = "referral_codes"

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
