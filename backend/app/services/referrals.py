"""Bring a friend, get something free.

A customer shares a link. Somebody who has never ordered here uses it and
buys something. The customer who shared it wins a free dish.

WHAT IS WON IS A LOYALTY REWARD, issued by the same engine a filled stamp
card uses. A referral program is a LoyaltyProgram whose purchase type is
REFERRAL, so everything downstream -- the ledger, the idempotency, the row
lock, the expiry, the free-dish pricing, the checkout picker -- is already
written and already tested. What is new here is only what counts as the
qualifying purchase, and it counts people.

THE INVITE IS SENT BY THE CUSTOMER. Nothing in this module messages anybody.
There is no SMS in this stack, and sending business SMS in the US means a
vendor, a per-message cost and A2P 10DLC registration with documented opt-in
before the first message -- none of which buys anything here, because a link
forwarded by a friend from their own phone arrives from a number the
recipient already knows. The storefront hands the customer a prefilled
message; their own phone sends it.

WHAT STOPS IT BEING FREE MONEY, in order of how obvious the attempt is:

  Nobody refers themselves. Refused by a check constraint, not a condition
  somebody could forget to call.

  A referral qualifies on the invitee's first completed order, never on the
  click. A program that paid for clicks would pay for a page refresh.

  The invitee must never have ordered here before, so a regular cannot
  "refer" a second account of their own and have the restaurant buy a burger
  for a customer it already had.

  One claim per invitee ever, by unique index. Two referrers cannot both be
  paid for the same person, and the same person cannot be claimed again next
  month.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import errors
from app.models import (
    LoyaltyProgram, Order, OrderStatus, PurchaseType, Referral, ReferralCode,
    ReferralStatus, User,
)
from app.models.identity import UserKind
from app.services import loyalty

# No vowels and no look-alikes: a code is read aloud, written down and typed
# back, and 0/O and 1/I/L are where that goes wrong.
_ALPHABET = "ACDEFGHJKMNPQRTUVWXY2346789"
_CODE_LENGTH = 7


@dataclass(frozen=True)
class Invitation:
    """One invitation as the customer sees it on their own page."""

    id: UUID
    invitee_name: str | None
    status: str
    qualified_at: datetime | None


def program(session: Session) -> LoyaltyProgram | None:
    """The restaurant's referral program, if it runs one.

    At most one: "bring a friend" is a single offer, and two of them running
    at once would pay twice for the same friend with no way to say which was
    meant.
    """
    return session.execute(
        select(LoyaltyProgram)
        .where(
            LoyaltyProgram.purchase_type == PurchaseType.REFERRAL.value,
            LoyaltyProgram.deleted_at.is_(None),
            LoyaltyProgram.is_active.is_(True),
        )
        .order_by(LoyaltyProgram.created_at, LoyaltyProgram.id)
        .limit(1)
    ).scalar_one_or_none()


def _new_code(session: Session) -> str:
    """A code nobody else at this restaurant holds.

    Retried rather than checked-then-inserted: the unique index is the
    guarantee and a check beforehand would still race. Seven characters out
    of this alphabet is enough that a collision is a surprise, which is why
    a handful of attempts is plenty.
    """
    for _ in range(10):
        code = "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))
        taken = session.execute(
            select(ReferralCode.code).where(ReferralCode.code == code).limit(1)
        ).scalar_one_or_none()
        if taken is None:
            return code
    raise errors.ApiError(
        503, "REFERRAL_CODE_UNAVAILABLE", "Could not make a referral link just now."
    )


def code_for(session: Session, restaurant_id: UUID, user: User) -> str:
    """This customer's referral code here, made on first use.

    One per customer per restaurant: the link they share is the link they
    share, and a second would split their own invitations between two codes
    with no way to merge them.
    """
    existing = session.execute(
        select(ReferralCode).where(ReferralCode.user_id == user.id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing.code

    row = ReferralCode(
        restaurant_id=restaurant_id, user_id=user.id, code=_new_code(session)
    )
    session.add(row)
    session.flush()
    return row.code


def invite(
    session: Session,
    restaurant_id: UUID,
    user: User,
    *,
    name: str | None = None,
    phone: str | None = None,
) -> Referral:
    """Record an invitation and return it, so the customer can send it.

    The row is the customer's note to themselves -- who they invited and
    whether it worked. Nothing is messaged from here.
    """
    if user.kind != UserKind.CUSTOMER.value:
        raise errors.ApiError(
            403, "REFERRAL_NEEDS_ACCOUNT",
            "Create an account to invite a friend, so we know who to thank.",
        )
    code_for(session, restaurant_id, user)  # made on first use
    row = Referral(
        restaurant_id=restaurant_id,
        referrer_user_id=user.id,
        invitee_name=(name or "").strip() or None,
        invitee_phone=(phone or "").strip() or None,
        status=ReferralStatus.SENT.value,
    )
    session.add(row)
    session.flush()
    return row


def claim(session: Session, restaurant_id: UUID, code: str, user: User) -> bool:
    """Attach an arriving customer to the invitation that brought them.

    Called when somebody lands on a referral link and is signed in. Returns
    whether the claim stuck, and says nothing about why it did not: a caller
    on a storefront page has nothing useful to do with "you have ordered
    here before", and spelling it out would describe the rules to the one
    person trying to get round them.

    Refused for a guest, for the referrer themselves, for anybody who has
    ordered here before, and for anybody already claimed.
    """
    cleaned = (code or "").strip().upper()
    if not cleaned or user.kind != UserKind.CUSTOMER.value:
        return False

    owner = session.execute(
        select(ReferralCode).where(ReferralCode.code == cleaned)
    ).scalar_one_or_none()
    if owner is None or owner.user_id == user.id:
        return False

    # Somebody who has ordered here is not a new customer, whatever link they
    # arrived on. This is the rule that stops a regular bringing themselves.
    ordered_before = session.execute(
        select(func.count())
        .select_from(Order)
        .where(Order.customer_user_id == user.id)
    ).scalar_one()
    if ordered_before:
        return False

    # An invitation this customer sent and nobody has claimed is the one to
    # fill in -- it is probably the one they meant. Otherwise record a fresh
    # row, so a customer who shared one link with several people ends with
    # one row each rather than one row overwritten repeatedly.
    target = session.execute(
        select(Referral)
        .where(
            Referral.referrer_user_id == owner.user_id,
            Referral.invitee_user_id.is_(None),
        )
        .order_by(Referral.created_at)
        .limit(1)
    ).scalar_one_or_none()
    # Everything that writes goes inside the savepoint, the row that may be
    # created included: rolling back has to remove it from the session as
    # well as from the database, or the next flush re-raises the same
    # violation somewhere that cannot answer for it.
    try:
        with session.begin_nested():
            if target is None:
                target = Referral(
                    restaurant_id=restaurant_id,
                    referrer_user_id=owner.user_id,
                    status=ReferralStatus.SENT.value,
                )
                session.add(target)
            target.invitee_user_id = user.id
            target.status = ReferralStatus.JOINED.value
            session.flush()
    except IntegrityError:
        # Already claimed by this person, or claimed for somebody else. The
        # unique index is the authority and this is simply a no.
        return False
    return True


def credit_referrer(session: Session, order: Order) -> None:
    """Pay the customer who brought whoever placed this order.

    Called where an order completes, beside the stamp cards. Does nothing
    unless this is the invitee's first completed order: a referral is worth
    one reward, not one per visit for ever.

    The reward itself is issued by the loyalty engine, so it is the same
    kind of thing a filled card wins and redeems the same way.
    """
    if order.status != OrderStatus.COMPLETED.value:
        return
    offer = program(session)
    if offer is None:
        return

    row = session.execute(
        select(Referral).where(
            Referral.invitee_user_id == order.customer_user_id,
            Referral.status == ReferralStatus.JOINED.value,
        )
    ).scalar_one_or_none()
    if row is None:
        return

    row.status = ReferralStatus.QUALIFIED.value
    row.qualifying_order_id = order.id
    row.qualified_at = datetime.now(timezone.utc)

    # Through the engine, against the invitee's order: the ledger's unique
    # index then makes this idempotent for free, because that order can only
    # ever credit this program once.
    loyalty.credit_program(
        session,
        offer,
        customer_user_id=row.referrer_user_id,
        order=order,
        stamps=1,
    )
    session.flush()


def invitations(session: Session, user: User | None) -> list[Invitation]:
    """What this customer has sent, newest first."""
    if user is None or user.kind != UserKind.CUSTOMER.value:
        return []
    rows = session.execute(
        select(Referral)
        .where(Referral.referrer_user_id == user.id)
        .order_by(Referral.created_at.desc(), Referral.id)
        .limit(100)
    ).scalars().all()
    return [
        Invitation(
            id=row.id,
            invitee_name=row.invitee_name,
            status=row.status,
            qualified_at=row.qualified_at,
        )
        for row in rows
    ]
