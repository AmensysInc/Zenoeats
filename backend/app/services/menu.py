"""One serialisation of the menu tree, read two ways.

Customers get the sellable menu: a meal period serving nothing is noise on a
storefront and is dropped.

The builder in the restaurant portal needs precisely the opposite. A meal
period that was just created serves nothing yet, and it has to be visible or
there is nowhere to click "add an item" -- which made the portal look as
though nothing it saved was being kept.

Pruning is therefore the only difference between the two views, and both are
built here so they cannot drift apart.

Sections are derived, not stored. A meal period holds a flat list of items,
each carrying its own type, and the headings a customer reads are the types
actually present, in the order the restaurant put its types in. Grouping once
here rather than in each client is what keeps the storefront and the builder
showing the same menu.

A type may sit under another one, and then its items become a block inside
its parent's section rather than a section of their own. Food first, then
Burgers and Nuggets indented under it. Most menus use none of this and send
an empty `groups` on every section, which is the shape to read this code in.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models import (
    Combo, ComboSlot, ComboSlotItem, Item, ItemModifierGroup, ItemType, Meal,
    MealItem, ModifierGroup,
)
from app.schemas.api import (
    ComboOut, ComboSlotOut, ItemOut, MealOut, MenuOut, ModifierGroupOut,
    OptionOut, SectionOut, SubsectionOut,
)
from app.services.images import image_url

# The words a customer reads, and the order they read in, both come from the
# restaurant now. There is no dictionary here to fall out of step with what
# the menu builder shows.


def load_item_types(db: Session) -> list[ItemType]:
    """The restaurant's own types, in the order its menu reads.

    Flat, but in reading order rather than in table order: every top-level
    type is followed immediately by its own subcategories. Food, Burgers,
    Nuggets, Drinks, Hot Beverages. Callers that only want the headings can
    iterate it as they always did, and the two that care about the nesting
    read parent_id.

    sort_order means "among my siblings", so a child's 0 puts it first under
    its parent rather than first on the menu. The ordering is done here
    instead of in SQL because it is two passes over a list of a dozen rows,
    and a window function would be a harder thing to read for no saving.

    The tie is the same discipline the rest of the menu keeps: two types can
    share a sort_order and a name is not unique across deletions, so creation
    order and then id settle it. Without that the headings on a storefront
    could swap places between two reads of an unchanged menu.

    A child whose parent was deleted is dropped. Deleting a parent with
    children is refused by the API, so this is the belt to that braces: a
    subheading with no heading above it has nowhere to appear.
    """
    rows = list(
        db.execute(
            select(ItemType)
            .where(ItemType.deleted_at.is_(None))
            .order_by(ItemType.sort_order, ItemType.created_at, ItemType.id)
        ).scalars().all()
    )

    children: dict = {}
    for row in rows:
        if row.parent_id is not None:
            children.setdefault(row.parent_id, []).append(row)

    ordered: list[ItemType] = []
    for row in rows:
        if row.parent_id is not None:
            continue
        ordered.append(row)
        ordered.extend(children.get(row.id, ()))
    return ordered


def load_menu(db: Session, *, include_empty: bool) -> MenuOut:
    """The menu of the tenant this session is scoped to.

    `include_empty` keeps meal periods that serve nothing, which is what the
    menu builder wants and what a storefront does not.
    """
    types = load_item_types(db)

    meals = db.execute(
        select(Meal)
        .where(Meal.is_active.is_(True), Meal.deleted_at.is_(None))
        # Same tie as the relationships in models/catalog.py: sort_order is
        # always 0 and two meals can share a name, so creation order and then
        # id decide, and the list stops rearranging itself between reads.
        .order_by(Meal.sort_order, Meal.name, Meal.created_at, Meal.id)
        # selectinload issues one query per level, so the shape of this chain
        # is the round-trip count. The collection levels stay separate because
        # each multiplies the rows above it. The single-valued hops do not: a
        # link has exactly one item and one group, so joining those onto the
        # query above folds several statements into one.
        .options(
            selectinload(Meal.item_links)
            .joinedload(MealItem.item)
            .selectinload(Item.modifier_links)
            .joinedload(ItemModifierGroup.group)
            .joinedload(ModifierGroup.options),
            # What each item comes with, in one more query rather than one
            # per item.
            selectinload(Meal.item_links)
            .joinedload(MealItem.item)
            .selectinload(Item.included_links),
        )
    ).scalars().all()

    # Every combo in one query, then handed out by period. One query per
    # meal would be a round trip per heading on the page, which is the shape
    # of loading this menu carefully everywhere except the last step.
    combos_by_meal = _combos_by_meal(db, meals, include_empty=include_empty)

    out: list[MealOut] = []
    for meal in meals:
        # Bucketed rather than sorted by type, so the order the builder put
        # items in survives inside each heading. The relationship already
        # ordered the links, tiebreaker and all.
        buckets: dict = {}
        for link in meal.item_links:
            item = link.item
            if item is None or item.deleted_at is not None:
                continue
            buckets.setdefault(item.item_type_id, []).append(_item(item))

        sections = _sections(types, buckets)
        combos = combos_by_meal.get(meal.id, [])

        if sections or combos or include_empty:
            out.append(
                MealOut(
                    id=meal.id,
                    name=meal.name,
                    starts_at=meal.starts_at,
                    ends_at=meal.ends_at,
                    sections=sections,
                    combos=combos,
                )
            )

    return MenuOut(meals=out)


def _sections(types: list[ItemType], buckets: dict) -> list[SectionOut]:
    """The headings of one meal period, from its items and the type list.

    Driven by the restaurant's type list rather than by the items, so the
    headings read in its order. An item whose type was deleted falls out
    here rather than appearing under a heading nobody named.

    A subcategory becomes a block inside its parent's section, and it brings
    that section into being even when nothing is filed on the parent itself:
    a restaurant that put every food under Burgers or Nuggets still wants to
    read Food across the top. The parent is skipped only when neither it nor
    any of its children is serving anything in this period.

    `types` is already in reading order -- each parent immediately followed
    by its own children -- which is what lets this be a single pass with no
    lookups backwards.
    """
    sections: list[SectionOut] = []
    started: dict = {}  # top-level type id -> its section, built or not
    placed: set = set()  # the ones actually in `sections`

    for t in types:
        items = buckets.get(t.id) or []

        if t.parent_id is None:
            started[t.id] = SectionOut(item_type_id=t.id, label=t.name, items=items)
            # Held back until something lands in it. A parent with nothing of
            # its own is a real heading only once a child fills it.
            if items:
                sections.append(started[t.id])
                placed.add(t.id)
            continue

        if not items:
            continue
        parent = started.get(t.parent_id)
        if parent is None:
            # The parent is deleted or otherwise not on the list. Same rule
            # as an item whose own type is gone: no heading, so no item.
            continue
        if t.parent_id not in placed:
            sections.append(parent)
            placed.add(t.parent_id)
        parent.groups.append(
            SubsectionOut(item_type_id=t.id, label=t.name, items=items)
        )

    return sections


def _combos_by_meal(
    db: Session, meals: list, *, include_empty: bool
) -> dict:
    """Every period's combos, in one read, keyed by the period.

    A combo needs every one of its slots fillable to be orderable at all: a
    meal deal whose only drink sold out cannot be completed, and offering it
    would end in a customer being refused at checkout. So on the storefront a
    combo with an empty slot is dropped whole, while the builder keeps it --
    it is the screen where the missing choice gets fixed.

    A choice is offered only while the combo's period serves the item. Taking
    an item off a period used to leave it in that period's combos, still sold
    at a discount; checkout refuses it now too. Filtered here rather than
    deleted from the combo, so putting the item back on the period brings the
    choice back with it.
    """
    if not meals:
        return {}
    meal_ids = [meal.id for meal in meals]

    # What each period serves, read off the periods already loaded above
    # rather than asked for again.
    served = {
        (meal.id, link.item.id)
        for meal in meals
        for link in meal.item_links
        if link.item is not None
    }

    combos = db.execute(
        select(Combo)
        .where(
            Combo.meal_id.in_(meal_ids),
            Combo.deleted_at.is_(None),
            *([] if include_empty else [Combo.is_available.is_(True)]),
        )
        .order_by(Combo.sort_order, Combo.name, Combo.created_at, Combo.id)
        .options(
            selectinload(Combo.slots).joinedload(ComboSlot.item_type),
            selectinload(Combo.slots)
            .selectinload(ComboSlot.choices)
            .joinedload(ComboSlotItem.item)
            .selectinload(Item.modifier_links)
            .joinedload(ItemModifierGroup.group)
            .joinedload(ModifierGroup.options),
            selectinload(Combo.slots)
            .selectinload(ComboSlot.choices)
            .joinedload(ComboSlotItem.item)
            .selectinload(Item.included_links),
        )
    ).scalars().all()

    out: dict = {}
    for combo in combos:
        slots: list[ComboSlotOut] = []
        complete = True

        for slot in combo.slots:
            items = [
                _item(choice.item)
                for choice in slot.choices
                if choice.item is not None
                and choice.item.deleted_at is None
                and (combo.meal_id, choice.item_id) in served
                and (include_empty or choice.item.is_available)
            ]
            if not items:
                # A required course with nothing left to choose makes the
                # combo unsellable. An optional one does not: the customer
                # could have left it out anyway, and the pricing credits a
                # course it receives no choice for. The customer is not
                # shown an empty one -- there is nothing to pick and nothing
                # to decline -- while the portal still sees it, because the
                # restaurant is the one who has to notice it is empty.
                if not slot.is_optional:
                    complete = False
                elif not include_empty:
                    continue
            slots.append(
                ComboSlotOut(
                    id=slot.id,
                    item_type_id=slot.item_type_id,
                    # The restaurant's word for the type, so the prompt reads
                    # in its vocabulary rather than a translated one.
                    label=slot.item_type.name if slot.item_type else "Choice",
                    items=items,
                    upcharges={
                        choice.item_id: choice.upcharge_minor
                        for choice in slot.choices
                        if choice.upcharge_minor
                    },
                    is_optional=slot.is_optional,
                    skip_credit_minor=slot.skip_credit_minor,
                )
            )

        if not include_empty and (not slots or not complete):
            continue

        out.setdefault(combo.meal_id, []).append(
            ComboOut(
                id=combo.id,
                name=combo.name,
                description=combo.description,
                discount_kind=combo.discount_kind,
                discount_value=combo.discount_value,
                image_url=image_url(combo.image_path),
                slots=slots,
            )
        )

    return out


def _item(item: Item) -> ItemOut:
    groups: list[ModifierGroupOut] = []
    # Already ordered by the relationship, tiebreaker and all. Re-sorting
    # here on sort_order alone would suggest the order is decided in this
    # file, which is exactly the confusion that hid the reordering bug.
    for link in item.modifier_links:
        group = link.group
        if group.deleted_at is not None:
            continue
        groups.append(
            ModifierGroupOut(
                id=group.id,
                name=group.name,
                selection_type=group.selection_type,
                is_required=group.is_required,
                min_select=group.min_select,
                max_select=group.max_select,
                options=[
                    OptionOut(
                        id=o.id,
                        name=o.name,
                        price_delta_minor=o.price_delta_minor,
                        calories_delta=o.calories_delta,
                        is_available=o.is_available,
                        image_url=image_url(o.image_path),
                    )
                    for o in group.options
                    if o.deleted_at is None
                ],
            )
        )

    return ItemOut(
        id=item.id,
        name=item.name,
        item_type_id=item.item_type_id,
        description=item.description,
        calories=item.calories,
        base_price_minor=item.base_price_minor,
        currency=item.currency,
        is_available=item.is_available,
        image_url=image_url(item.image_path),
        modifier_groups=groups,
        included_option_ids=[link.option_id for link in item.included_links],
    )
