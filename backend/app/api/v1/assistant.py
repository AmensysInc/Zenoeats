"""The admin assistant: say what you want, confirm what it would do.

TWO ENDPOINTS AND ONE RULE BETWEEN THEM. /ask reads the sentence and either
answers a question or describes a change; /do carries out a change the admin
pressed the button on. Nothing is written by /ask, ever, and /do writes only
what /ask handed back.

WHY A PROPOSAL RATHER THAN AN ACTION. The assistant can misread a sentence.
On this surface a misreading is a price customers pay, so the cost of being
wrong is made a sentence on a screen instead. It also means the admin sees
the resolved dish -- "Jr's French Fries", not "fries" -- before agreeing,
which is where a wrong match gets caught.

THE PROPOSAL CARRIES IDS, NOT WORDS, and is re-checked on the way through:
the dish still has to exist. Re-reading the sentence at confirm time would
let a menu edited in between turn "the fries" into a different row.

WHO MAY. The gate the menu screens use, because that is what this drives.
Running as the signed-in admin on their own staff session means the
assistant can do exactly what that person could do by clicking, and there
is no path here that is wider than the screens it replaces.

WHAT IT WILL NOT DO AT ALL: refunds, cancellations, deletions, staff
changes. Those mistakes cannot be taken back, and a sentence is a thinner
interface than the screens that guard them.
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import StaffDb, current_restaurant_staff
from app.api.v1.restaurant import MANAGE
from app.core import errors
from app.core.ratelimit import per_staff_user
from app.models import Restaurant, RestaurantUser
from app.services import restaurant_profile
from app.services.assistant import actions, language

log = logging.getLogger(__name__)
router = APIRouter(prefix="/restaurant/assistant", tags=["assistant"])


class AskIn(BaseModel):
    text: str = Field(min_length=1, max_length=400)


class AskOut(BaseModel):
    """What to show. `proposal` set means "and here is a button"."""

    text: str
    proposal: dict | None = None
    options: list[str] = []
    understood: bool = True


class DoIn(BaseModel):
    # Handed back exactly as /ask produced it. Not trusted on arrival: the
    # intent is checked against the ones that may be performed at all, and
    # the rows are re-read before anything changes.
    proposal: dict


class DoOut(BaseModel):
    text: str


@router.post(
    "/ask",
    response_model=AskOut,
    # Per account. This is cheap -- no model, no network -- but a box that
    # queries the menu on every keystroke is still a box worth bounding.
    dependencies=[Depends(per_staff_user("assistant_ask", limit=120, window_seconds=300))],
)
def ask(
    body: AskIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(MANAGE),
):
    """Read a sentence. Answers a question, or describes a change."""
    reading = language.read(body.text)
    if reading is None:
        # Said plainly, with what it does understand. A guess here is how a
        # price gets changed that nobody asked about.
        return AskOut(
            text="I did not understand that.\n\n" + actions.HELP, understood=False
        )
    answer = actions.respond(db, restaurant, reading)
    return AskOut(text=answer.text, proposal=answer.proposal, options=answer.options)


@router.post(
    "/do",
    response_model=DoOut,
    dependencies=[Depends(per_staff_user("assistant_do", limit=60, window_seconds=300))],
)
def do(
    body: DoIn,
    restaurant: Restaurant = Depends(current_restaurant_staff),
    db: Session = StaffDb,
    membership: RestaurantUser = Depends(MANAGE),
):
    """Carry out a change the admin confirmed.

    Audited to the staff member who pressed the button, not to "the
    assistant": a person decided this, and the log should say which one.
    """
    try:
        said = actions.perform(db, restaurant, body.proposal)
    except (ValueError, KeyError, TypeError) as refused:
        raise errors.validation_error(str(refused) or "I cannot do that.") from None

    restaurant_profile.audit(
        db, membership.user_id, "RESTAURANT_ASSISTANT_ACTION",
        {
            "restaurant_id": str(restaurant.id),
            # What was agreed to, as it was agreed to.
            "proposal": {
                key: value for key, value in body.proposal.items()
                if key in ("intent", "name", "item_id", "item_name", "price_minor",
                           "available", "heading_name")
            },
        },
    )
    db.flush()
    log.info("assistant action on restaurant %s", restaurant.id)
    return DoOut(text=said)
