import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid_pk


class StaffRole(str, enum.Enum):
    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    KITCHEN = "KITCHEN"
    CASHIER = "CASHIER"
    # Runs deliveries. Sees the orders assigned to them and nothing else --
    # not the board, the menu, stock, reports or the team.
    DRIVER = "DRIVER"
    # Keeps the restaurant's setup working. Reads the board, stock and the
    # menu to diagnose what a customer is seeing, and owns the technical
    # configuration: the storefront's presentation, the restaurant's record
    # and its delivery area.
    #
    # Deliberately cannot change what is sold or who is paid. No menu or
    # price edit, no action on a live order, no reports and no staff
    # management -- the three places where a support login would become a
    # way to move money or take over the team.
    IT_SUPPORT = "IT_SUPPORT"


class StaffStatus(str, enum.Enum):
    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class UserKind(str, enum.Enum):
    """Which of the three identity systems a users row belongs to.

    The populations never overlap. A person who orders lunch and also works a
    restaurant's counter has two rows: one reached through Clerk, one through
    the credentials the restaurant issued. Keeping them apart is what stops a
    customer sign-in from ever opening a staff portal, and an email match from
    ever merging the two.

    GUEST is the one kind that is not a person we can name. It is created for
    a checkout with no account behind it, holds only what the customer typed
    for their receipt, and is reachable exclusively by the cookie minted with
    it (core/guest_auth.py). One row per guest checkout session, never looked
    up by email: an address nobody verified must not find another guest's
    orders.
    """

    CUSTOMER = "CUSTOMER"
    GUEST = "GUEST"
    STAFF = "STAFF"
    PLATFORM_ADMIN = "PLATFORM_ADMIN"


class User(Base, TimestampMixin):
    """Global platform identity. No restaurant_id, no tenant RLS policy.

    For customers, Clerk owns credentials, sessions, verification and Google
    sign-in, and this row mirrors the parts we need: a real foreign key for
    orders, and an email for receipts. Every permission is still resolved from
    our own tables, never from a claim in a token.
    """

    __tablename__ = "users"
    __table_args__ = (
        # An agreement is a moment and a wording together. Either both are
        # recorded or neither is; a half of one is not evidence.
        CheckConstraint(
            "(terms_accepted_at IS NULL) = (terms_version IS NULL)",
            name="ck_users_terms_recorded_together",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # Customers only. Platform administrators and restaurant staff have no
    # Clerk identity, but still need a row here: platform_audit_logs and
    # restaurant_users both carry NOT NULL foreign keys to users.id.
    clerk_user_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )
    # The `sub` of a Google ID token, for an account that signs in with
    # Google. Never reissued and never changed, unlike the address on it --
    # see migration 0046 for why the address is not the identity.
    google_subject: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    full_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    # Customers and guests: the details checkout last saved, offered again
    # next time. Null until the first order; checkout is what requires them.
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Restaurant staff only. Customers authenticate through Clerk and platform
    # administrators against ADMIN_USERS, so both are null for them.
    password_hash: Mapped[str | None] = mapped_column(String(512), nullable=True)
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Admin and staff tokens issued before this moment are refused. Set by
    # admin sign-out, a staff password change and a super-admin reset, so a
    # copied token stops working then rather than when it would have expired.
    sessions_valid_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # When this customer agreed to the terms, and to which version of them.
    #
    # The checkbox on the sign-up page is what a customer sees; this is the
    # part that can still be answered a year later, when the question is not
    # "does the form have a checkbox" but "did this person agree, and to
    # what". The version is stored rather than derived, because the wording
    # changes and the old agreement was to the old wording.
    #
    # Null for staff and platform admins, who agree to nothing here, and for
    # customers who predate this column.
    terms_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    terms_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    def session_revoked(self, issued_at: float) -> bool:
        """Whether a token issued at `issued_at` (epoch seconds) predates the
        last time this account's sessions were ended."""
        return (
            self.sessions_valid_after is not None
            and issued_at < self.sessions_valid_after.timestamp()
        )


class RestaurantUser(Base, TimestampMixin):
    """Staff membership. Tenant owned, RLS enforced.

    Rule 27: a membership becomes ACTIVE only after the target account
    explicitly accepts. An email match alone never grants tenant access. The
    invitee signs in to this restaurant's portal with their staff credentials
    and accepts there; this row is the authoritative record we authorize
    against.
    """

    __tablename__ = "restaurant_users"
    __table_args__ = (
        UniqueConstraint("restaurant_id", "user_id", name="uq_restaurant_user"),
        CheckConstraint(
            "invitation_email_status IS NULL OR "
            "invitation_email_status IN ('SENT', 'FAILED', 'NOT_CONFIGURED')",
            name="ck_restaurant_users_invitation_email_status",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    role_code: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=StaffStatus.INVITED.value)
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # What happened to the last invitation email, written back by the worker:
    # SENT, FAILED or NOT_CONFIGURED; null while one is still queued. Sending
    # is asynchronous, so without this the portal could only ever say "we're
    # emailing them" -- including for emails the provider went on to refuse.
    invitation_email_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    invitation_email_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Written for the restaurant admin, never the provider's raw reply: that
    # can name the platform's own accounts. The raw reply is in the worker log.
    invitation_email_problem: Mapped[str | None] = mapped_column(String(200), nullable=True)
