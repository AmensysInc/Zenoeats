"""Recording that a customer agreed to the terms.

Agreement is asked for in two places, and this is what survives both: the
checkbox on the sign-up page, and the line above the button on checkout that
says continuing means agreeing. The second is what every order passes
through, account or guest, so that is where the record is written -- one
place rather than one per way of arriving.

What is stored is the moment and the wording it was given to, because the
wording changes and an old agreement was to the old wording. A timestamp
alone would say someone agreed to something nobody can now identify.

Deliberately not a consent ledger. There is one row per customer holding
their most recent agreement, which is the question actually asked of it
("has this person agreed to the terms in force?"). A per-order history of
agreements is a different product with different retention rules, and
guessing at it now would be building the wrong thing carefully.
"""

import logging

from app.db.base import utcnow
from app.db.session import system_session
from app.models import User

log = logging.getLogger(__name__)

# The wording in force. Bump this when web/legal/terms.html changes in a way
# people should be asked about again -- and only then, because every customer
# re-agrees on their next order when it moves.
CURRENT_VERSION = "2026-09-21"


def needs_recording(user: User) -> bool:
    """Whether this customer's agreement is missing or to older wording."""
    return user.terms_version != CURRENT_VERSION


def stamp(user: User) -> None:
    """Mark a row in the caller's own transaction as having agreed, for a row
    being created or already loaded at the moment the agreement was given."""
    user.terms_accepted_at = utcnow()
    user.terms_version = CURRENT_VERSION


def record(user: User) -> None:
    """Note that this customer has agreed to the terms in force.

    Called on the checkout path, so it does nothing at all when the record is
    already current -- which is every order after the first. Written with the
    system role because `users` carries no tenant policy, and in its own
    transaction so it neither joins nor delays the order's.

    Never raises. An order that a customer agreed to and paid for must not
    fail because the note about it could not be written; the failure is worth
    a log line and nothing more.
    """
    if not needs_recording(user):
        return
    try:
        with system_session() as session:
            row = session.get(User, user.id)
            if row is None:
                return
            row.terms_accepted_at = utcnow()
            row.terms_version = CURRENT_VERSION
        # Keep the in-memory copy in step, so a caller that checks again in
        # this request does not write twice.
        user.terms_accepted_at = utcnow()
        user.terms_version = CURRENT_VERSION
    except Exception:
        log.warning("could not record terms acceptance for a customer", exc_info=True)
