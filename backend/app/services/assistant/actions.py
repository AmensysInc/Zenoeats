"""Turning a reading into something that happened, or into a question.

Three jobs, and the middle one is the point of the file.

  RESOLVE NAMES AGAINST THE REAL MENU. "fries" means the row called "Jr's
  French Fries" because that row exists. Nothing here invents a dish, a
  heading or a price: an unknown name comes back as a question, and an
  ambiguous one comes back as a list of the candidates rather than a choice
  made on the admin's behalf.

  PROPOSE A WRITE, DO NOT PERFORM IT. A read runs on the spot -- it changes
  nothing and the answer is the point. A write is described back in plain
  words and waits for a person to press the button. That is the single
  control that makes this safe to put in front of somebody: the assistant
  can be wrong, and being wrong costs a sentence rather than a price
  customers pay.

  RUN AS THE ADMIN, NEVER AROUND THEM. Every write goes through the same
  session and the same role gate as the screen it replaces, so the
  assistant can do exactly what the person driving it could do by clicking,
  and nothing else. There is no elevated path.

WHAT IS DELIBERATELY ABSENT: refunds, cancellations, deletions and staff
changes. They are the actions whose mistakes cannot be taken back, and
typing a sentence is a thinner interface than the screens that guard them.
They stay on those screens.
"""

import logging
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    ALLOWED_TRANSITIONS, Coupon, Item, ItemType, MealItem, Order, OrderStatus,
    Restaurant,
)
from app.db.base import utcnow
from app.services.assistant.language import DESTRUCTIVE, Intent, Reading, WRITES

log = logging.getLogger(__name__)


@dataclass
class Answer:
    """What to show the admin.

    `proposal` is set when something would change and needs confirming; the
    text beside it says what, in the words somebody would use out loud.
    """

    text: str
    # Filled for a write: the intent and its resolved slots, handed straight
    # back to perform() if the admin agrees. Resolved rather than re-read,
    # so the thing confirmed is the thing done.
    proposal: dict | None = None
    # Candidates, when a name matched more than one dish.
    options: list[str] = field(default_factory=list)
    # Cannot be taken back. The screen reads this to make the button red and
    # the wording blunt; it is not a second gate, it is honesty about which
    # kind of change the admin is about to agree to.
    destructive: bool = False


def _items(db: Session) -> list[Item]:
    return list(
        db.execute(
            select(Item).where(Item.deleted_at.is_(None)).order_by(Item.name)
        ).scalars()
    )


def find_item(db: Session, name: str) -> tuple[Item | None, list[Item]]:
    """The dish this name means, or the candidates it could mean.

    Exact match first, then "starts with", then "contains" -- so "fries"
    finds "Jr's French Fries" without "Jr's" also matching every other dish
    on a menu where everything starts with Jr's. The narrowest rule that
    matches anything wins, which is what makes a one-word nickname usable
    without making it dangerous.
    """
    wanted = " ".join(name.split()).casefold()
    if not wanted:
        return None, []

    live = _items(db)
    for rule in (
        lambda item: item.name.casefold() == wanted,
        lambda item: item.name.casefold().startswith(wanted),
        lambda item: wanted in item.name.casefold(),
    ):
        hits = [item for item in live if rule(item)]
        if len(hits) == 1:
            return hits[0], []
        if hits:
            return None, hits
    return None, []


def find_heading(db: Session, name: str) -> tuple[ItemType | None, list[ItemType]]:
    """The menu heading this name means, by the same rules."""
    wanted = " ".join(name.split()).casefold()
    live = list(
        db.execute(
            select(ItemType).where(ItemType.deleted_at.is_(None)).order_by(ItemType.sort_order)
        ).scalars()
    )
    if not wanted:
        return None, live
    for rule in (
        lambda row: row.name.casefold() == wanted,
        lambda row: row.name.casefold().startswith(wanted),
        lambda row: wanted in row.name.casefold(),
    ):
        hits = [row for row in live if rule(row)]
        if len(hits) == 1:
            return hits[0], []
        if hits:
            return None, hits
    return None, live


def money(minor: int, currency: str) -> str:
    return f"{currency} {minor // 100}.{minor % 100:02d}"


# ------------------------------------------------------------------ reads ----


def _menu(db: Session, restaurant: Restaurant) -> Answer:
    headings = list(
        db.execute(
            select(ItemType).where(ItemType.deleted_at.is_(None))
            .order_by(ItemType.sort_order, ItemType.name)
        ).scalars()
    )
    items = _items(db)
    by_heading: dict[UUID, list[Item]] = {}
    for item in items:
        by_heading.setdefault(item.item_type_id, []).append(item)

    if not items:
        return Answer(text="There is nothing on the menu yet.")

    lines = []
    for heading in headings:
        under = by_heading.get(heading.id, [])
        if not under:
            continue
        lines.append(f"{heading.name}")
        for item in under:
            mark = "" if item.is_available else "  (unavailable)"
            lines.append(f"  {item.name} — {money(item.base_price_minor, restaurant.currency)}{mark}")
    return Answer(text="\n".join(lines))


def _unavailable(db: Session, restaurant: Restaurant) -> Answer:
    off = [item for item in _items(db) if not item.is_available]
    if not off:
        return Answer(text="Everything on the menu is available.")
    listed = "\n".join(f"  {item.name}" for item in off)
    return Answer(text=f"{len(off)} off the menu right now:\n{listed}")


def _orders(db: Session, restaurant: Restaurant) -> Answer:
    """Counts by state, and no customer details.

    A dashboard question deserves a dashboard answer: who ordered what is on
    the orders screen, which is where somebody looking at one order should
    be. Nothing a customer typed is put in front of the assistant.
    """
    rows = db.execute(
        select(Order.status, func.count())
        .where(Order.status.notin_((OrderStatus.EXPIRED.value,)))
        .group_by(Order.status)
    ).all()
    if not rows:
        return Answer(text="No orders yet.")
    listed = "\n".join(f"  {status.replace('_', ' ').lower()}: {count}" for status, count in rows)
    return Answer(text=f"Orders by state:\n{listed}")


def _coupons(db: Session, restaurant: Restaurant) -> Answer:
    rows = list(
        db.execute(
            select(Coupon).where(Coupon.deleted_at.is_(None), Coupon.group_id.is_(None))
            .order_by(Coupon.created_at.desc())
        ).scalars()
    )
    if not rows:
        return Answer(text="No coupons yet.")
    lines = []
    for coupon in rows:
        worth = (
            f"{coupon.discount_value / 100:g}% off"
            if coupon.discount_kind == "PERCENT"
            else f"{money(coupon.discount_value, restaurant.currency)} off"
        )
        state = "" if coupon.is_active else "  (paused)"
        lines.append(f"  {coupon.code} — {worth}{state}")
    return Answer(text="\n".join(lines))


HELP = """I can do these, and I'll always show you a change before making it:

  add a cheeseburger to Burgers for 5.99
  change the price of fries to 2.50
  we're out of fries          (or: 86 the fries)
  fries are back
  check inventory
  show me the menu
  how many orders today
  list coupons

Refunds, cancellations and deleting things stay on their own screens — those
are the mistakes that cannot be taken back."""


# ----------------------------------------------------------------- writes ----
#
# Each returns a proposal rather than doing anything. perform() below is the
# only thing in this file that writes, and it runs when a person says so.


def _propose_add(db: Session, restaurant: Restaurant, slots: dict) -> Answer:
    name = slots["name"]
    if "price_minor" not in slots:
        return Answer(text=f'What should "{name}" cost? Say it like: add {name} for 5.99')

    clash, _ = find_item(db, name)
    if clash is not None and clash.name.casefold() == name.casefold():
        return Answer(text=f'"{clash.name}" is already on the menu.')

    heading = None
    if "heading" in slots:
        heading, options = find_heading(db, slots["heading"])
        if heading is None:
            names = ", ".join(row.name for row in options) or "none yet"
            return Answer(
                text=f'I could not find a heading called "{slots["heading"]}". '
                     f"There is: {names}.",
                options=[row.name for row in options],
            )
    else:
        found, options = find_heading(db, "")
        if len(options) == 1:
            heading = options[0]
        else:
            names = ", ".join(row.name for row in options) or "none yet"
            return Answer(
                text=f'Which heading should "{name}" go under? There is: {names}.',
                options=[row.name for row in options],
            )

    price = money(slots["price_minor"], restaurant.currency)
    return Answer(
        text=f'Add "{name}" to {heading.name} at {price}?',
        proposal={
            "intent": Intent.ADD_ITEM.value,
            "name": name,
            "item_type_id": str(heading.id),
            "heading_name": heading.name,
            "price_minor": slots["price_minor"],
        },
    )


def _propose_price(db: Session, restaurant: Restaurant, slots: dict) -> Answer:
    item, options = find_item(db, slots["name"])
    if item is None:
        return _ask_which(slots["name"], options)

    was = money(item.base_price_minor, restaurant.currency)
    now = money(slots["price_minor"], restaurant.currency)
    if item.base_price_minor == slots["price_minor"]:
        return Answer(text=f'"{item.name}" is already {now}.')
    return Answer(
        text=f'Change "{item.name}" from {was} to {now}?',
        proposal={
            "intent": Intent.SET_PRICE.value,
            "item_id": str(item.id),
            "item_name": item.name,
            "price_minor": slots["price_minor"],
        },
    )


def _propose_availability(db: Session, slots: dict, available: bool) -> Answer:
    item, options = find_item(db, slots["name"])
    if item is None:
        return _ask_which(slots["name"], options)

    if item.is_available == available:
        state = "already available" if available else "already off the menu"
        return Answer(text=f'"{item.name}" is {state}.')
    verb = "Put back on the menu" if available else "Take off the menu"
    return Answer(
        text=f'{verb}: "{item.name}"?',
        proposal={
            "intent": (Intent.SET_AVAILABLE if available else Intent.SET_UNAVAILABLE).value,
            "item_id": str(item.id),
            "item_name": item.name,
            "available": available,
        },
    )


def _propose_cancel(db: Session, restaurant: Restaurant, slots: dict) -> Answer:
    """Cancelling one order, with what it was and who it was for.

    The order is named back by its number and total rather than by the digits
    that were typed, because a mistyped number is the obvious way to cancel
    somebody else's dinner.
    """
    number = slots["order_number"]
    order = db.execute(
        select(Order).where(Order.order_number == number)
    ).scalar_one_or_none()
    if order is None:
        return Answer(text=f"There is no order #{number} here.")

    if order.status == OrderStatus.PENDING_PAYMENT.value:
        return Answer(
            text=f"Order #{number} has not been paid. It expires on its own if "
                 "payment never arrives."
        )
    # Asked of the state machine rather than of a list kept here, so this
    # stays true when the states change. Said before the button rather than
    # as a 409 after it.
    if OrderStatus.CANCELLED.value not in ALLOWED_TRANSITIONS.get(order.status, set()):
        return Answer(
            text=f"Order #{number} is {order.status.replace('_', ' ').lower()} "
                 "and cannot be cancelled from there."
        )

    total = money(order.total_minor, restaurant.currency)
    return Answer(
        text=f"Cancel order #{number} ({total})? The customer is emailed, and this "
             "cannot be undone. No refund is made here — that stays on the order.",
        destructive=True,
        proposal={
            "intent": Intent.CANCEL_ORDER.value,
            "order_id": str(order.id),
            "order_number": number,
        },
    )


def _propose_delete_item(db: Session, restaurant: Restaurant, slots: dict) -> Answer:
    """Removing one dish, with what goes with it."""
    item, options = find_item(db, slots["name"])
    if item is None:
        return _ask_which(slots["name"], options)

    # What else points at it, so the admin agrees to the whole thing rather
    # than to the part they were thinking about.
    meals = db.execute(
        select(func.count()).select_from(MealItem).where(MealItem.item_id == item.id)
    ).scalar_one()
    also = f" It is on {meals} meal{'s' if meals != 1 else ''}." if meals else ""
    return Answer(
        text=f'Delete "{item.name}" ({money(item.base_price_minor, restaurant.currency)}) '
             f"from the menu?{also} This cannot be undone. Orders that already "
             "included it still read correctly.",
        destructive=True,
        proposal={
            "intent": Intent.DELETE_ITEM.value,
            "item_id": str(item.id),
            "item_name": item.name,
        },
    )


def _propose_delete_heading(db: Session, slots: dict) -> Answer:
    """Removing one heading -- refused, exactly as the Menu screen refuses
    it, while anything is still filed under it."""
    heading, options = find_heading(db, slots["name"])
    if heading is None:
        names = ", ".join(row.name for row in options) or "none"
        return Answer(
            text=f'I could not find a heading called "{slots["name"]}". '
                 f"There is: {names}.",
            options=[row.name for row in options],
        )

    under = db.execute(
        select(func.count()).select_from(Item).where(
            Item.item_type_id == heading.id, Item.deleted_at.is_(None)
        )
    ).scalar_one()
    if under:
        # The same answer the endpoint gives, and the same reason: deleting
        # it would take real dishes with it or orphan them.
        return Answer(
            text=f"{heading.name} still has {under} "
                 f"{'dish' if under == 1 else 'dishes'} under it. Move or delete "
                 "those first."
        )

    children = db.execute(
        select(func.count()).select_from(ItemType).where(
            ItemType.parent_id == heading.id, ItemType.deleted_at.is_(None)
        )
    ).scalar_one()
    if children:
        return Answer(
            text=f"{heading.name} still has {children} sub-heading"
                 f"{'s' if children != 1 else ''} under it. Remove those first."
        )

    return Answer(
        text=f'Delete the empty heading "{heading.name}"? This cannot be undone.',
        destructive=True,
        proposal={
            "intent": Intent.DELETE_HEADING.value,
            "heading_id": str(heading.id),
            "heading_name": heading.name,
        },
    )


def _ask_which(name: str, options: list[Item]) -> Answer:
    """An unknown or ambiguous dish, as a question rather than a guess."""
    if not options:
        return Answer(text=f'I could not find anything called "{name}" on the menu.')
    listed = ", ".join(f'"{item.name}"' for item in options[:6])
    return Answer(
        text=f'"{name}" matches more than one dish: {listed}. Which did you mean?',
        options=[item.name for item in options[:6]],
    )


# ------------------------------------------------------------------ entry ----


def respond(db: Session, restaurant: Restaurant, reading: Reading) -> Answer:
    """What to say back. Writes nothing; writes are perform()'s business."""
    intent, slots = reading.intent, reading.slots

    if intent is Intent.OUT_OF_SCOPE:
        # Understood, and deliberately not done here. Saying where it lives
        # is the useful half of refusing.
        return Answer(text=slots["says"])
    if intent is Intent.HELP:
        return Answer(text=HELP)
    if intent is Intent.SHOW_MENU:
        return _menu(db, restaurant)
    if intent is Intent.SHOW_UNAVAILABLE:
        return _unavailable(db, restaurant)
    if intent is Intent.SHOW_ORDERS:
        return _orders(db, restaurant)
    if intent is Intent.SHOW_COUPONS:
        return _coupons(db, restaurant)
    if intent is Intent.ADD_ITEM:
        return _propose_add(db, restaurant, slots)
    if intent is Intent.SET_PRICE:
        return _propose_price(db, restaurant, slots)
    if intent in (Intent.SET_AVAILABLE, Intent.SET_UNAVAILABLE):
        return _propose_availability(db, slots, intent is Intent.SET_AVAILABLE)
    if intent is Intent.CANCEL_ORDER:
        return _propose_cancel(db, restaurant, slots)
    if intent is Intent.DELETE_ITEM:
        return _propose_delete_item(db, restaurant, slots)
    if intent is Intent.DELETE_HEADING:
        return _propose_delete_heading(db, slots)

    return Answer(text="I did not understand that.")


def _cancel(db: Session, restaurant: Restaurant, proposal: dict) -> str:
    """Cancel one order, through the same state machine the Orders screen
    uses.

    transition() is what decides whether this order may go to CANCELLED at
    all, so a state that changed between the proposal and the button is
    refused here rather than forced. No refund: money going back to a
    customer stays on the order, where the amount is in front of whoever
    sends it.
    """
    from app.services.orders import transition

    order = db.get(Order, UUID(proposal["order_id"]), with_for_update=True)
    if order is None:
        raise ValueError("That order is no longer here.")
    if order.status == OrderStatus.CANCELLED.value:
        raise ValueError(f"Order #{order.order_number} is already cancelled.")
    if order.status == OrderStatus.PENDING_PAYMENT.value:
        raise ValueError(f"Order #{order.order_number} has not been paid.")
    # transition() would refuse this anyway; saying it here makes the
    # message one an admin can read.
    if OrderStatus.CANCELLED.value not in ALLOWED_TRANSITIONS.get(order.status, set()):
        raise ValueError(
            f"Order #{order.order_number} is "
            f"{order.status.replace('_', ' ').lower()} and cannot be cancelled now."
        )

    transition(order, OrderStatus.CANCELLED.value, reason="CANCELLED_BY_RESTAURANT")
    db.flush()
    log.info("assistant cancelled order %s on restaurant %s", order.id, restaurant.id)
    return (
        f"Order #{order.order_number} is cancelled. No refund was made — "
        "that is on the order itself."
    )


def perform(db: Session, restaurant: Restaurant, proposal: dict) -> str:
    """Carry out a proposal the admin confirmed. Returns what to tell them.

    The proposal carries resolved ids rather than the words that were typed,
    so what is done is what was shown -- re-reading the sentence here would
    let a menu that changed in between turn "the fries" into a different
    dish.
    """
    intent = proposal.get("intent")
    if intent not in {i.value for i in WRITES}:
        raise ValueError("That is not something I can do.")

    if intent == Intent.ADD_ITEM.value:
        heading = db.get(ItemType, UUID(proposal["item_type_id"]))
        if heading is None or heading.deleted_at is not None:
            raise ValueError("That menu heading is no longer there.")
        item = Item(
            restaurant_id=restaurant.id,
            name=proposal["name"],
            item_type_id=heading.id,
            base_price_minor=int(proposal["price_minor"]),
            currency=restaurant.currency,
        )
        db.add(item)
        db.flush()
        log.info("assistant added item %s on restaurant %s", item.id, restaurant.id)
        return f'Added "{item.name}" to {heading.name}.'

    if intent == Intent.CANCEL_ORDER.value:
        return _cancel(db, restaurant, proposal)

    if intent == Intent.DELETE_HEADING.value:
        heading = db.get(ItemType, UUID(proposal["heading_id"]))
        if heading is None or heading.deleted_at is not None:
            raise ValueError("That heading is no longer there.")
        # Re-checked at the moment of doing it, not only when it was
        # proposed: a dish may have been filed under it in between, and the
        # Menu screen would refuse for the same reason.
        under = db.execute(
            select(func.count()).select_from(Item).where(
                Item.item_type_id == heading.id, Item.deleted_at.is_(None)
            )
        ).scalar_one()
        if under:
            raise ValueError(
                f"{heading.name} has {under} dish{'es' if under != 1 else ''} "
                "under it now. Nothing was deleted."
            )
        heading.deleted_at = utcnow()
        db.flush()
        log.info("assistant deleted heading %s on restaurant %s", heading.id, restaurant.id)
        return f'Deleted the heading "{heading.name}".'

    item = db.get(Item, UUID(proposal["item_id"]))
    if item is None or item.deleted_at is not None:
        raise ValueError("That dish is no longer on the menu.")

    if intent == Intent.DELETE_ITEM.value:
        # Soft, like the Menu screen: orders that included it keep their own
        # snapshot, so last month's receipt still reads correctly.
        item.deleted_at = utcnow()
        item.is_available = False
        db.flush()
        log.info("assistant deleted item %s on restaurant %s", item.id, restaurant.id)
        return f'Deleted "{item.name}" from the menu.'

    if intent == Intent.SET_PRICE.value:
        item.base_price_minor = int(proposal["price_minor"])
        db.flush()
        return (
            f'"{item.name}" is now '
            f"{money(item.base_price_minor, restaurant.currency)}."
        )

    item.is_available = bool(proposal["available"])
    db.flush()
    return (
        f'"{item.name}" is back on the menu.'
        if item.is_available
        else f'"{item.name}" is off the menu.'
    )
