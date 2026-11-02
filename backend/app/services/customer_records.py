"""What is shared about a customer's row, whichever way they sign in.

The placeholder address an old account may still carry, where a customer's
order emails may be sent, and what closing an account does to the row.
"""

import logging

from sqlalchemy import delete

from app.db.base import utcnow
from app.models import User

log = logging.getLogger(__name__)

PENDING_EMAIL_DOMAIN = "pending.local"


def _placeholder_email(key: str) -> str:
    return f"{key}@{PENDING_EMAIL_DOMAIN}"


def has_placeholder_email(user: User) -> bool:
    return user.email.endswith("@" + PENDING_EMAIL_DOMAIN)


def receipt_address(user: User) -> str | None:
    """Where this customer's order emails may be sent, and the email given
    to Stripe with their payment, or None for nowhere.

    Also asked of guests: they typed their address at checkout, so it is real
    by construction and comes straight back. The placeholder case below simply
    never applies to them.

    A closed account, or one from before this codebase held its own
    credentials, can carry a ...@pending.local placeholder. That is not an
    address anyone reads, and emailing it would send every order email into the
    void. No email is better than a bounced one -- and far better than refusing
    the payment over it: the order page still shows the pickup PIN, which is
    what the customer actually needs.
    """
    if has_placeholder_email(user):
        log.warning("no receipt email for user %s: the address is a placeholder", user.id)
        return None
    return user.email


def contact_before_closing(user: User) -> tuple[str, str | None] | None:
    """Where to tell someone their account closed, read before closing erases
    it. None if it is already closed, or its address was never real."""
    if user.deleted_at is not None or has_placeholder_email(user):
        return None
    return user.email, (user.full_name or "").split(" ")[0] or None


def close_account(session, user: User) -> None:
    """Close a customer's account and take their details off it.

    What legal/data-deletion.html promises, and no more: the name, phone
    number, address and email go, the favourites go, and the account can no
    longer be signed in to. Orders stay, with the name and address that were
    on them when they were placed -- they are the restaurant's record of a
    sale, and the page says so.

    The row itself stays because orders, audit trails and tax records point
    at it with NOT NULL keys. Emptied of everything personal, it is an
    anchor rather than a profile.

    Idempotent: closing twice is the same operation twice, and the second
    must be harmless.
    """
    from app.models import CustomerFavourite

    user.is_active = False
    user.deleted_at = user.deleted_at or utcnow()
    user.full_name = None
    user.phone = None
    user.address = None
    # Every credential: a password, and the provider identities. Leaving one
    # would be an account that is closed and still signs in -- and a provider
    # identity left on this row would stop that person ever signing up again
    # with the Google, Apple or Facebook account they just asked us to forget.
    user.password_hash = None
    user.google_subject = None
    user.apple_subject = None
    user.facebook_subject = None
    # Email is NOT NULL and unique, so it is replaced rather than emptied.
    #
    # Without this the address stays on the closed row, and
    # uq_users_customer_email then stops that person ever registering again
    # with the address they just asked us to forget.
    if not has_placeholder_email(user):
        user.email = _placeholder_email(str(user.id))
    session.execute(delete(CustomerFavourite).where(CustomerFavourite.user_id == user.id))
    session.flush()
    log.info("closed customer account %s", user.id)
