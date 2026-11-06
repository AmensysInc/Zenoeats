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

WHAT IT WILL NOT DO AT ALL: refunds, staff changes, and anything in bulk.
Money going back to a customer stays on the order, where the amount is in
front of whoever sends it; "delete everything" is refused before the
sentence is even read.

Cancellations and deletions it does do, and they are marked destructive the
whole way through: the proposal says what will be lost -- the order total,
the meals a dish is on, whether a heading is empty -- and the state is
re-checked at the moment of doing it rather than only when it was proposed.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import (
    StaffDb, TenantDb, current_restaurant, current_restaurant_staff,
    optional_current_user, require_platform_admin,
)
from app.api.v1.restaurant import MANAGE
from app.core import errors, ratelimit
from app.core.ratelimit import per_ip, per_staff_user
from app.db.session import system_session
from app.models import Restaurant, RestaurantUser, User
from app.services import restaurant_profile
from app.services.assistant import (
    actions, agent, customer_tools, language, platform_tools, provider, restaurant_tools,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/restaurant/assistant", tags=["assistant"])
# The same assistant for the platform admin and for customers. Read-only on
# both: neither has a tool that writes anything.
platform_router = APIRouter(prefix="/admin/assistant", tags=["assistant"])
customer_router = APIRouter(prefix="/assistant", tags=["assistant"])

# A public box on every storefront is a bill anyone can run up. Per address
# above, and per restaurant per day here, so one busy -- or hostile -- day
# stops at a number somebody chose.
CUSTOMER_DAILY_CAP = 1500


class HistoryTurn(BaseModel):
    """An earlier line of the conversation, as the browser kept it. Only
    ever sent back to the model as plain text in one of these two roles."""

    role: Literal["user", "assistant"]
    text: str = Field(max_length=4000)


class AskIn(BaseModel):
    text: str = Field(min_length=1, max_length=400)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=20)


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=20)


class ChatOut(BaseModel):
    text: str


class StatusOut(BaseModel):
    enabled: bool


def _unavailable(exc: provider.AssistantUnavailable) -> errors.ApiError:
    return errors.ApiError(503, "ASSISTANT_UNAVAILABLE", str(exc))


def _history(body: AskIn | ChatIn) -> list[dict]:
    return [turn.model_dump() for turn in body.history]


class AskOut(BaseModel):
    """What to show. `proposal` set means "and here is a button"."""

    text: str
    proposal: dict | None = None
    options: list[str] = []
    understood: bool = True
    # Cannot be taken back. The screen makes the button red and the wording
    # blunt; it is honesty about which kind of change this is, not a second
    # gate -- the gate is that nothing happens without the button at all.
    destructive: bool = False


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
    """Read a sentence. Answers a question, or describes a change.

    The rules first: they are free, instant and cannot invent a price. A
    sentence they do not recognise goes to the model when one is configured,
    with tools that are the same intents -- so whatever it decides still
    arrives as a proposal behind the same Yes button.
    """
    reading = language.read(body.text)
    if reading is None and provider.configured():
        try:
            said = agent.run(
                system=restaurant_tools.system_prompt(restaurant),
                history=_history(body),
                text=body.text,
                tools=restaurant_tools.TOOLS,
                call=lambda name, args: restaurant_tools.call(db, restaurant, name, args),
            )
        except provider.AssistantUnavailable as exc:
            raise _unavailable(exc) from None
        return AskOut(
            text=said.text, proposal=said.proposal,
            options=said.options, destructive=said.destructive,
        )
    if reading is None:
        # Said plainly, with what it does understand. A guess here is how a
        # price gets changed that nobody asked about.
        return AskOut(
            text="I did not understand that.\n\n" + actions.HELP, understood=False
        )
    answer = actions.respond(db, restaurant, reading)
    return AskOut(
        text=answer.text,
        proposal=answer.proposal,
        options=answer.options,
        destructive=answer.destructive,
    )


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


# ------------------------------------------------------------ platform admin


@platform_router.get("/status", response_model=StatusOut)
def platform_status(admin: User = Depends(require_platform_admin)):
    """Whether to show the chat at all. It needs a model; there are no rules
    on this side to fall back on."""
    return StatusOut(enabled=provider.configured())


@platform_router.post(
    "/chat",
    response_model=ChatOut,
    dependencies=[Depends(per_ip("admin_assistant", limit=60, window_seconds=300))],
)
def platform_chat(body: ChatIn, admin: User = Depends(require_platform_admin)):
    """Questions about every restaurant at once. Reads only."""
    if not provider.configured():
        raise _unavailable(provider.AssistantUnavailable("The assistant is not switched on."))
    used: list[str] = []
    with system_session() as session:
        def call(name: str, args: dict):
            used.append(name)
            return platform_tools.call(session, name, args)

        try:
            said = agent.run(
                system=platform_tools.system_prompt(), history=_history(body),
                text=body.text, tools=platform_tools.TOOLS, call=call,
            )
        except provider.AssistantUnavailable as exc:
            raise _unavailable(exc) from None
        # Which reads it made, as the admin screens record theirs. Not what
        # was asked: that is the admin's own words, and not ours to keep.
        from app.api.v1.admin import _audit
        _audit(session, admin, "SUPER_ADMIN_ASSISTANT", {"tools": used})
    return ChatOut(text=said.text)


# ----------------------------------------------------------------- customers


@customer_router.get("/status", response_model=StatusOut)
def customer_status(restaurant: Restaurant = Depends(current_restaurant)):
    return StatusOut(enabled=provider.configured())


@customer_router.post(
    "/chat",
    response_model=ChatOut,
    dependencies=[Depends(per_ip("customer_assistant", limit=20, window_seconds=300))],
)
def customer_chat(
    body: ChatIn,
    restaurant: Restaurant = Depends(current_restaurant),
    user: User | None = Depends(optional_current_user),
    db: Session = TenantDb,
):
    """The storefront's helper. Answers from the public menu and the
    customer's own orders; changes nothing."""
    if not provider.configured():
        raise _unavailable(provider.AssistantUnavailable("The assistant is not switched on."))
    ratelimit.consume(f"customer_assistant:restaurant:{restaurant.id}", CUSTOMER_DAILY_CAP, 86_400)
    try:
        said = agent.run(
            system=customer_tools.system_prompt(restaurant), history=_history(body),
            text=body.text, tools=customer_tools.TOOLS,
            call=lambda name, args: customer_tools.call(db, restaurant, user, name, args),
        )
    except provider.AssistantUnavailable as exc:
        raise _unavailable(exc) from None
    return ChatOut(text=said.text)
