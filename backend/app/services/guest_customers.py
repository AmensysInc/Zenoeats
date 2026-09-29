"""Guest customers: the users row behind an order with no account.

Deliberately the same shape as a signed-in customer, because everything
downstream -- the orders foreign key, idempotency's actor, the per-user rate
limit, the pickup PIN, the order emails -- already
works in terms of a users row and should not learn a second case.

What differs is only how the row is reached: by the cookie minted alongside
it rather than by a verified Clerk session. See core/guest_auth.py for why
that makes it a capability rather than an identity.
"""

import logging

from sqlalchemy import select

from app.core import errors
from app.db.session import system_session
from app.models import User, UserKind

log = logging.getLogger(__name__)


def create_guest(email: str, full_name: str | None) -> User:
    """A fresh guest row for a checkout about to begin.

    Always new. Reusing a row that matched on email would hand whoever typed
    an address the order history of everybody else who typed it.
    """
    with system_session() as session:
        user = User(
            kind=UserKind.GUEST.value,
            clerk_user_id=None,
            email=email.strip().lower()[:320],
            full_name=(full_name or "").strip()[:160] or None,
        )
        session.add(user)
        session.flush()
        session.expunge(user)
        return user


def guest_for_session(user_id) -> User:
    """The guest behind a verified guest cookie.

    Refuses any row that is not a guest. The cookie carries a users id, and
    all four identity kinds live in that table, so a token whose subject
    somehow named a staff or admin row must not open it -- this is the same
    refusal clerk_customers makes from the other direction.
    """
    with system_session() as session:
        user = session.get(User, user_id)
        if user is None or user.kind != UserKind.GUEST.value:
            # Also the ordinary case of a cookie that outlived its row.
            raise errors.ApiError(401, "UNAUTHENTICATED", "Start your order again.")
        if not user.is_active or user.deleted_at is not None:
            raise errors.ApiError(403, "ACCOUNT_INACTIVE", "This session is no longer active.")
        session.expunge(user)
        return user
