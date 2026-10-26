"""What a restaurant is advertising, and when.

An offer carries no discount and changes no price: a promotion that takes
money off is a coupon, which already prices itself. This is the poster in
the window, which is why it can be written by somebody who is not allowed to
move a price.

Split from the loyalty engine deliberately. The two arrived together and are
shown on the same admin page, but they share no rule and no table, and a
module that held both would suggest they did.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Offer


def live(session: Session, now: datetime | None = None) -> list[Offer]:
    """The offers a customer should see at this moment, in the restaurant's
    own order.

    The window is read by Offer.is_live so the storefront, the admin list and
    anything later all agree about what "running" means rather than each
    writing the comparison again slightly differently.
    """
    moment = now or datetime.now(timezone.utc)
    rows = session.execute(
        select(Offer)
        .where(Offer.deleted_at.is_(None))
        .order_by(Offer.sort_order, Offer.created_at, Offer.id)
    ).scalars().all()
    return [offer for offer in rows if offer.is_live(moment)]
