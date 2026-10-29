"""The welcome offer: one code, for one new customer, once.

A restaurant sets up a welcome offer; a customer creates an account, proves
a mobile number, and is given a code only they can use.

IT IS A COUPON GROUP (0056), which is why so little of this file is code. A
numbered group mints all its codes at once; this mints one at a time, as
customers arrive. Everything after minting -- pricing, the window, the
minimum spend, the heading scope, counting redemptions from orders, the row
lock at checkout -- is what a coupon already does.

WHAT IS ACTUALLY NEW is two sentences long:

  the code is bound to one customer, so nobody they tell can spend it, and

  it may expire a fixed number of days after issue rather than on a date,
  because every customer gets theirs on a different day. "30 days to use
  it" is the offer; "until March" is a different one that gets meaner the
  later you sign up.

ONE PER CUSTOMER, EVER, enforced by a unique index rather than by this code
remembering to check: a customer who verified a number, changed it and
verified again would otherwise collect a second free discount for the same
account.
"""

import logging
import secrets
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import Coupon, CouponGroup, User
from app.services import coupon_groups as group_service

log = logging.getLogger(__name__)

# No I, O, 0 or 1. A welcome code gets read off a screen and typed back, and
# those four are the pairs people get wrong.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
SUFFIX_LENGTH = 6


def offer(db: Session) -> CouponGroup | None:
    """This restaurant's live welcome offer, or None if it has none.

    Scoped by row-level security to the current tenant, like everything else
    a customer's request touches.
    """
    return db.execute(
        select(CouponGroup).where(
            CouponGroup.kind == "SIGNUP",
            CouponGroup.deleted_at.is_(None),
            CouponGroup.is_active.is_(True),
        )
    ).scalar_one_or_none()


def held_by(db: Session, user_id: UUID) -> list[Coupon]:
    """Every personal code this customer holds, newest first.

    What the account screen lists. Expired and spent codes are left out by
    the caller rather than here: whether to show a used code is a question
    about a screen, and this is the set.
    """
    return list(
        db.execute(
            select(Coupon)
            .where(
                Coupon.customer_user_id == user_id,
                Coupon.deleted_at.is_(None),
            )
            .order_by(Coupon.created_at.desc())
        ).scalars()
    )


def already_issued(db: Session, group_id: UUID, user_id: UUID) -> Coupon | None:
    """The code this customer was already given for this offer, if any."""
    return db.execute(
        select(Coupon).where(
            Coupon.group_id == group_id,
            Coupon.customer_user_id == user_id,
            Coupon.deleted_at.is_(None),
        )
    ).scalar_one_or_none()


def _unused_code(db: Session, prefix: str) -> str:
    """A code nobody holds yet.

    Random rather than sequential: a welcome code is personal, and WELCOME7
    would tell its holder they were the seventh customer and let them guess
    at WELCOME8. Retried on collision, which at 32^6 is rare enough that the
    loop is a formality and short enough that a formality is worth having.
    """
    for _attempt in range(10):
        suffix = "".join(secrets.choice(ALPHABET) for _ in range(SUFFIX_LENGTH))
        code = f"{prefix}{suffix}"
        taken = db.execute(
            select(Coupon.id).where(Coupon.code == code, Coupon.deleted_at.is_(None))
        ).scalar_one_or_none()
        if taken is None:
            return code
    raise RuntimeError("Could not find an unused welcome code.")


def issue(db: Session, group: CouponGroup, user: User) -> Coupon:
    """Mint this customer's welcome code, or hand back the one they have.

    Idempotent on purpose. It is called when a number is verified, and a
    customer who verifies twice has not earned two codes -- the unique index
    says so, and returning the existing row means the second call reads as a
    success rather than as a crash.
    """
    existing = already_issued(db, group.id, user.id)
    if existing is not None:
        return existing

    ends_at = group.ends_at
    if group.valid_days is not None:
        # Counted from now, and never past the offer's own end: a code good
        # for thirty days issued a week before the promotion closes is good
        # for a week.
        personal_end = utcnow() + timedelta(days=group.valid_days)
        ends_at = personal_end if ends_at is None else min(ends_at, personal_end)

    coupon = Coupon(
        restaurant_id=group.restaurant_id,
        code=_unused_code(db, group.code_prefix),
        description=group.description,
        discount_kind=group.discount_kind,
        discount_value=group.discount_value,
        max_discount_minor=group.max_discount_minor,
        min_subtotal_minor=group.min_subtotal_minor,
        starts_at=group.starts_at,
        ends_at=ends_at,
        # One code, one order. A welcome offer is a welcome, not a tariff.
        max_redemptions=1,
        max_per_customer=1,
        is_active=True,
        group_id=group.id,
        group_number=None,
        customer_user_id=user.id,
    )
    db.add(coupon)
    db.flush()

    # The headings the offer applies to, copied onto the code: the checkout
    # reads scope off the coupon in front of it, not off the group.
    group_service.copy_scope(
        db, group, [coupon], group_service.scope_of(db, group.id)
    )

    log.info("welcome code issued to user %s", user.id)
    return coupon
