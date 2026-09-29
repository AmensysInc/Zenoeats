"""Server-authoritative cart repricing.

Rule 3: all prices, modifiers and availability are recalculated and validated
server-side at checkout. The browser cart is an input, never an authority.
The client sends ids and quantities. It never sends prices, and any price it
does send is ignored.
"""

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.core import errors
from app.core.money import apply_rate_bps
from app.models import (
    Combo, ComboSlot, ComboSlotItem, DiscountKind, Item, ItemModifierGroup,
    ModifierGroup, ModifierOption, Restaurant, SelectionType,
)
from app.services.tax import TaxLine, TaxService


@dataclass
class PricedModifier:
    option_id: UUID
    group_name: str
    option_name: str
    unit_price_delta_minor: int
    quantity: int


@dataclass
class PricedLine:
    menu_item_id: UUID
    name: str
    unit_price_minor: int      # base + modifier deltas, for one unit
    quantity: int
    line_total_minor: int
    note: str | None = None
    modifiers: list[PricedModifier] = field(default_factory=list)
    # From the item, read at pricing time like its price. Not stored on the
    # order: the tax the order was charged is what it keeps.
    taxable: bool = True
    # Set on the lines a combo produced. A combo is not a line of its own: it
    # becomes one line per slot at the item's own price, and the saving is
    # taken off the cart. combo_group numbers the combos within one cart, so
    # two identical meal deals stay two deals rather than merging.
    combo_id: UUID | None = None
    combo_name: str | None = None
    combo_group: int | None = None


@dataclass
class PricedCart:
    currency: str
    subtotal_minor: int
    discount_minor: int
    tax_minor: int
    total_minor: int
    lines: list[PricedLine]
    # Charged on top of the food, never part of the subtotal: a subtotal that
    # quietly included delivery would make every item's share of a refund
    # wrong, and would read as a menu price rise on the reports.
    delivery_fee_minor: int = 0
    # The Stripe Tax calculation behind tax_minor, for restaurants on Stripe
    # Tax; None under a flat rate. Carried onto the order so the sale can be
    # recorded against exactly the numbers the customer was charged.
    tax_calculation_id: str | None = None


def price_cart(
    session: Session,
    restaurant: Restaurant,
    raw_lines: list[dict],
    raw_combos: list[dict] | None = None,
    delivery_fee_minor: int = 0,
) -> PricedCart:
    """Validate and price a cart. Raises ApiError on any invalid input.

    raw_lines: [{"menu_item_id": uuid, "quantity": int, "note": str|None,
                 "modifiers": [{"option_id": uuid, "quantity": int}]}]
    raw_combos: [{"combo_id": uuid, "quantity": int, "note": str|None,
                  "selections": [{"slot_id": uuid, "menu_item_id": uuid,
                                  "modifiers": [...]}]}]

    A combo does not price itself. Each slot is priced as the item it holds,
    exactly as if ordered alone, and the saving comes off the cart as a
    discount -- so the subtotal is still what the food costs, and what the
    combo took off is a number a receipt can show and a manager can check.
    """
    raw_combos = raw_combos or []
    if not raw_lines and not raw_combos:
        raise errors.validation_error("Cart is empty.")

    # A combo counts once per combo, not once per slot. Someone ordering ten
    # meal deals has ordered ten things, whatever a meal deal is made of.
    counted = sum(int(l.get("quantity", 0)) for l in raw_lines)
    counted += sum(int(c.get("quantity", 0)) for c in raw_combos)
    if counted > settings.MAX_ITEMS_PER_ORDER:
        raise errors.validation_error("Too many items in one order.")

    item_ids = {UUID(str(l["menu_item_id"])) for l in raw_lines}
    for combo in raw_combos:
        for selection in combo.get("selections") or []:
            item_ids.add(UUID(str(selection["menu_item_id"])))

    # RLS already scopes this to the current tenant. The explicit
    # restaurant_id predicate is belt and braces, and it keeps the index in
    # play.
    items = session.execute(
        select(Item)
        .where(
            Item.id.in_(item_ids),
            Item.restaurant_id == restaurant.id,
            Item.deleted_at.is_(None),
        )
        .options(
            selectinload(Item.modifier_links)
            .selectinload(ItemModifierGroup.group)
            .selectinload(ModifierGroup.options),
            # What each item comes with. Read here rather than per line, so a
            # cart of ten burgers still costs one query to answer.
            selectinload(Item.included_links),
            # Which meal periods serve it -- whether it is on the menu at all,
            # and whether a combo's period may still offer it.
            selectinload(Item.meal_links),
        )
    ).scalars().all()
    items_by_id = {i.id: i for i in items}

    missing = item_ids - set(items_by_id)
    if missing:
        raise errors.item_unavailable("An item in your cart no longer exists.")

    priced_lines: list[PricedLine] = []
    subtotal = 0
    discount = 0

    for raw in raw_lines:
        item = items_by_id[UUID(str(raw["menu_item_id"]))]
        quantity = int(raw.get("quantity", 0))

        if quantity < 1:
            raise errors.validation_error(f"Quantity for {item.name} must be at least 1.")
        if not item.is_available:
            raise errors.item_unavailable(f"{item.name} is sold out.")
        # The storefront only shows what a meal period serves, but a cart
        # outlives a menu edit and the API can be called directly. An item
        # taken off every period is off the menu, however it was reached.
        if not item.meal_links:
            raise errors.item_unavailable(f"{item.name} is no longer on the menu.")
        if item.currency != restaurant.currency:
            raise errors.validation_error("Mixed currencies in one cart.")

        selected = _validate_modifiers(item, raw.get("modifiers") or [])

        unit_price = item.base_price_minor + sum(
            m.unit_price_delta_minor * m.quantity for m in selected
        )
        if unit_price < 0:
            # Modifier deltas may be negative, but a line can never be.
            unit_price = 0

        line_total = unit_price * quantity
        subtotal += line_total

        note = (raw.get("note") or None)
        if note and len(note) > 280:
            raise errors.validation_error("Item note is too long.")

        priced_lines.append(
            PricedLine(
                menu_item_id=item.id,
                name=item.name,
                unit_price_minor=unit_price,
                quantity=quantity,
                line_total_minor=line_total,
                note=note,
                modifiers=selected,
                taxable=not item.tax_exempt,
            )
        )

    # Combos, after the loose items, so a cart reads in the order it was
    # built. Each returns its lines and what it took off. All of them are
    # read in one query first, as the items were: a cart of five meal deals
    # used to ask the database five times, on the quote and again at checkout.
    combos_by_id = _load_combos(session, restaurant, raw_combos)
    for group_number, raw in enumerate(raw_combos, start=1):
        combo_lines, combo_discount = _price_combo(
            combos_by_id, restaurant, raw, items_by_id, group_number
        )
        priced_lines.extend(combo_lines)
        subtotal += sum(line.line_total_minor for line in combo_lines)
        discount += combo_discount

    # Never more than the food. A percentage cannot exceed the total it is
    # taken from, but a flat amount can be set above what a cheap combination
    # comes to, and a negative subtotal is not a thing that can be charged.
    discount = min(discount, subtotal)

    taxable_base = max(subtotal - discount, 0)
    # The fee is priced by services/delivery.py from the restaurant's own
    # rings. It never reaches here from the browser: what a customer sends is
    # an address, and what comes back is a number they cannot choose.
    fee = max(delivery_fee_minor, 0)
    tax_result = TaxService.calculate(
        session,
        restaurant,
        [
            TaxLine(
                amount_minor=line.line_total_minor, quantity=line.quantity, taxable=line.taxable
            )
            for line in priced_lines
        ],
        discount,
        shipping_minor=fee,
    )
    tax = tax_result.tax_minor
    total = taxable_base + fee + tax

    return PricedCart(
        currency=restaurant.currency,
        subtotal_minor=subtotal,
        discount_minor=discount,
        tax_minor=tax,
        total_minor=total,
        delivery_fee_minor=fee,
        lines=priced_lines,
        tax_calculation_id=tax_result.calculation_id,
    )


def _validate_modifiers(item: Item, raw_modifiers: list[dict]) -> list[PricedModifier]:
    """Enforce the group rules the restaurant configured, and price the result.

    Checks that every selected option belongs to a group actually attached to
    this item, that required groups are satisfied, that SINGLE groups get
    exactly one selection, and that min/max are respected.

    An option the item includes is priced at nothing. That is the whole of
    what "comes with" means here: the lettuce on a burger is part of the
    burger, not a topping that happens to be free, and the same lettuce on an
    item that does not include it is charged normally.

    Inclusion changes the price, never the rules. An included option still
    counts towards the group's maximum and still satisfies a required group,
    because the customer did select it -- a burger that comes with two of
    three allowed toppings leaves room for one more, not for three.
    """
    included = {link.option_id for link in item.included_links}
    groups_by_id: dict[UUID, ModifierGroup] = {}
    option_lookup: dict[UUID, tuple[ModifierGroup, ModifierOption]] = {}

    for link in item.modifier_links:
        group = link.group
        if group.deleted_at is not None:
            continue
        groups_by_id[group.id] = group
        for option in group.options:
            if option.deleted_at is None:
                option_lookup[option.id] = (group, option)

    selected: list[PricedModifier] = []
    per_group_count: dict[UUID, int] = {gid: 0 for gid in groups_by_id}

    seen: set[UUID] = set()
    for raw in raw_modifiers:
        option_id = UUID(str(raw["option_id"]))
        quantity = int(raw.get("quantity", 1))

        if quantity < 1:
            raise errors.validation_error("Modifier quantity must be at least 1.")
        if option_id in seen:
            raise errors.validation_error("Duplicate modifier option in one line.")
        seen.add(option_id)

        found = option_lookup.get(option_id)
        if found is None:
            # Either the option does not exist, belongs to another tenant, or
            # is not attached to this item. All three are the same answer.
            raise errors.item_unavailable("That option is not available on this item.")

        group, option = found
        if not option.is_available:
            raise errors.item_unavailable(f"{option.name} is unavailable.")

        per_group_count[group.id] += quantity
        selected.append(
            PricedModifier(
                option_id=option.id,
                group_name=group.name,
                option_name=option.name,
                # Zero for what the item comes with. The snapshot an order
                # keeps is what was charged, so a receipt reads "Lettuce" with
                # nothing beside it rather than a price that was waived.
                unit_price_delta_minor=(
                    0 if option.id in included else option.price_delta_minor
                ),
                quantity=quantity,
            )
        )

    for group_id, group in groups_by_id.items():
        count = per_group_count[group_id]

        if group.is_required and count == 0:
            raise errors.validation_error(f"Choose an option for {group.name}.")
        if count == 0:
            continue
        if group.selection_type == SelectionType.SINGLE.value and count != 1:
            raise errors.validation_error(f"Choose exactly one option for {group.name}.")
        if count < group.min_select and group.is_required:
            raise errors.validation_error(
                f"Choose at least {group.min_select} for {group.name}."
            )
        if count > group.max_select:
            raise errors.validation_error(
                f"Choose at most {group.max_select} for {group.name}."
            )

    return selected


def combo_discount_minor(kind: str, value: int, items_subtotal_minor: int) -> int:
    """What one combo takes off the items it holds.

    Never more than the items came to. A flat amount set above the price of a
    cheap combination would otherwise make the line negative and hand money
    back, so it is capped here rather than trusted to the person who typed it.

    PERCENT goes through the same basis-point helper as tax, so a discount and
    a tax rate round the same way -- HALF_UP, on Decimal, never a float.
    """
    if items_subtotal_minor <= 0:
        return 0
    if kind == DiscountKind.PERCENT.value:
        return min(apply_rate_bps(items_subtotal_minor, value), items_subtotal_minor)
    if kind == DiscountKind.AMOUNT.value:
        return min(max(value, 0), items_subtotal_minor)
    return 0


def _load_combos(
    session: Session, restaurant: Restaurant, raw_combos: list[dict]
) -> dict[UUID, Combo]:
    """Every combo the cart names, with its slots and their choices, in one
    query however many combos there are. A combo that is gone or deleted is
    simply absent; _price_combo says so."""
    combo_ids = {UUID(str(raw["combo_id"])) for raw in raw_combos}
    if not combo_ids:
        return {}
    combos = session.execute(
        select(Combo)
        .where(
            Combo.id.in_(combo_ids),
            Combo.restaurant_id == restaurant.id,
            Combo.deleted_at.is_(None),
        )
        .options(
            selectinload(Combo.slots).selectinload(ComboSlot.choices),
            # The type is loaded for its name alone, which is what the
            # "choose a drink" message says. A message naming an id would be
            # no message at all.
            selectinload(Combo.slots).joinedload(ComboSlot.item_type),
        )
    ).scalars().all()
    return {combo.id: combo for combo in combos}


def _price_combo(
    combos_by_id: dict[UUID, Combo],
    restaurant: Restaurant,
    raw: dict,
    items_by_id: dict[UUID, Item],
    group_number: int,
) -> tuple[list[PricedLine], int]:
    """One combo: validate the choices, price each slot, take the saving off.

    Every rule here is a rule the customer's browser also knows, and none of
    them is trusted from it. The slots that exist, which items may fill them,
    and what any of it costs are read from the database on every quote and
    again on every order (_load_combos, once for the whole cart).
    """
    combo_id = UUID(str(raw["combo_id"]))
    quantity = int(raw.get("quantity", 0))
    if quantity < 1:
        raise errors.validation_error("Combo quantity must be at least 1.")

    combo = combos_by_id.get(combo_id)
    if combo is None:
        raise errors.item_unavailable("That combo is no longer on the menu.")
    if not combo.is_available:
        raise errors.item_unavailable(f"{combo.name} is not available right now.")

    # One selection per slot, no more and no fewer. Both directions are
    # checked: a missing slot is an incomplete meal deal sold at meal-deal
    # price, and an extra one is an item smuggled in under the discount.
    chosen: dict[UUID, dict] = {}
    for selection in raw.get("selections") or []:
        slot_id = UUID(str(selection["slot_id"]))
        if slot_id in chosen:
            raise errors.validation_error(f"Two choices for one part of {combo.name}.")
        chosen[slot_id] = selection

    slots_by_id = {slot.id: slot for slot in combo.slots}
    unknown = set(chosen) - set(slots_by_id)
    if unknown:
        raise errors.validation_error(f"That is not part of {combo.name}.")

    note = raw.get("note") or None
    if note and len(note) > 280:
        raise errors.validation_error("Item note is too long.")

    lines: list[PricedLine] = []
    items_subtotal = 0

    for slot in combo.slots:
        selection = chosen.get(slot.id)
        if selection is None:
            # No article in front of the type name: it is the restaurant's own
            # word and may be plural, so "a Tiffins" is a sentence this cannot
            # write. The name goes after "from", where any name reads.
            label = slot.item_type.name if slot.item_type else "the missing part"
            raise errors.validation_error(
                f"{combo.name} needs a choice from {label}."
            )

        item_id = UUID(str(selection["menu_item_id"]))
        allowed = {choice.item_id for choice in slot.choices}
        item = items_by_id.get(item_id)
        # A choice counts only while the combo's own period serves the item.
        # The builder checks that when the combo is saved, but an item can be
        # taken off the period afterwards, and the combo kept selling it: food
        # the period no longer offers, at a meal-deal discount. Deleting the
        # period removes its links too, so a combo left on one sells nothing.
        served_here = item is not None and combo.meal_id in {
            link.meal_id for link in item.meal_links
        }
        if item_id not in allowed or (item is not None and not served_here):
            # Not on the list for this slot, or no longer served in this
            # period. The customer does not need to know which.
            raise errors.item_unavailable(
                f"That is not one of the choices for {combo.name}."
            )

        if item is None:
            raise errors.item_unavailable("An item in your cart no longer exists.")
        if not item.is_available:
            raise errors.item_unavailable(f"{item.name} is sold out.")
        if item.currency != restaurant.currency:
            raise errors.validation_error("Mixed currencies in one cart.")

        # The same modifier rules as an item ordered on its own. A combo does
        # not relax them: a required choice is still required inside one.
        selected = _validate_modifiers(item, selection.get("modifiers") or [])
        unit_price = item.base_price_minor + sum(
            m.unit_price_delta_minor * m.quantity for m in selected
        )
        if unit_price < 0:
            unit_price = 0

        items_subtotal += unit_price
        lines.append(
            PricedLine(
                menu_item_id=item.id,
                name=item.name,
                unit_price_minor=unit_price,
                quantity=quantity,
                line_total_minor=unit_price * quantity,
                # The note belongs to the combo, so it rides on the first line
                # rather than being repeated against every part of the meal.
                note=note if not lines else None,
                modifiers=selected,
                taxable=not item.tax_exempt,
                combo_id=combo.id,
                combo_name=combo.name,
                combo_group=group_number,
            )
        )

    if not lines:
        # A combo with no slots is a discount attached to nothing. It cannot
        # be built through the portal and must not be orderable if one exists.
        raise errors.item_unavailable(f"{combo.name} is not ready to order.")

    # Rounded once per combo and then multiplied, not the other way round.
    # Rounding the total of ten combos gives a different answer from ten
    # rounded combos, and the receipt shows the per-combo figure.
    discount_each = combo_discount_minor(
        combo.discount_kind, combo.discount_value, items_subtotal
    )
    return lines, discount_each * quantity
