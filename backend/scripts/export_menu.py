"""Write a restaurant's menu out as JSON.

The other half of import_menu.py. A menu that can only be produced by
running the seed script is a menu nobody can hand to anybody: it lives
inside Python, mixed up with a demo tenant, a placeholder Stripe account
and a dev customer. This writes just the menu, in a form a person can read,
a reviewer can diff, and the importer can load into any restaurant.

    docker compose exec api python scripts/export_menu.py spicehouse \\
        > menus/jrs-corner.json

Names, not ids. Every reference inside the file -- an item's type, a group's
options, a combo's slots -- is by name, because ids belong to the database
that happened to produce the file and mean nothing in the one that reads it.
That is also what lets the importer re-run against a menu it has already
loaded and update it rather than duplicate it.

Photographs travel as the asset name they came from (app/seed_assets), not
as bytes: the files are already in the repository, and a megabyte of base64
in a JSON file is not something anyone can review.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text  # noqa: E402

from app.db.session import system_session, tenant_session  # noqa: E402
from app.models import (  # noqa: E402
    Combo, ComboSlot, ComboSlotItem, Item, ItemIncludedOption, ItemModifierGroup,
    ItemType, Meal, MealItem, ModifierGroup, ModifierGroupItemType, ModifierOption,
)

# Which asset each stored image came from. The database keeps a content key
# ("…/items/9f3c….webp") that says nothing about what the picture is, so the
# mapping back to a reviewable name lives here, matching ITEM_PHOTOS in
# seed.py. An image with no entry is exported as null rather than guessed.
from scripts.seed import CATEGORY_PHOTOS, ITEM_PHOTOS  # noqa: E402


def export(slug: str) -> dict:
    with system_session() as session:
        row = session.execute(
            text("SELECT id, name FROM restaurants WHERE slug = :s"), {"s": slug}
        ).first()
        if row is None:
            raise SystemExit(f"No restaurant with slug {slug!r}.")
        rid, name = row.id, row.name

    with tenant_session(rid) as db:
        types = list(db.scalars(select(ItemType).order_by(ItemType.sort_order, ItemType.id)))
        by_type_id = {t.id: t for t in types}

        groups = list(db.scalars(select(ModifierGroup).order_by(ModifierGroup.name)))
        options = list(db.scalars(select(ModifierOption).order_by(ModifierOption.sort_order)))
        group_types = list(db.scalars(select(ModifierGroupItemType)))
        items = list(db.scalars(select(Item).order_by(Item.sort_order, Item.id)))
        links = list(db.scalars(select(ItemModifierGroup).order_by(ItemModifierGroup.sort_order)))
        included = list(db.scalars(select(ItemIncludedOption)))
        meals = list(db.scalars(select(Meal).order_by(Meal.sort_order, Meal.id)))
        meal_items = list(db.scalars(select(MealItem).order_by(MealItem.sort_order)))
        combos = list(db.scalars(select(Combo).order_by(Combo.sort_order, Combo.id)))
        slots = list(db.scalars(select(ComboSlot).order_by(ComboSlot.sort_order)))
        slot_items = list(db.scalars(select(ComboSlotItem).order_by(ComboSlotItem.sort_order)))

        by_item_id = {i.id: i for i in items}
        by_group_id = {g.id: g for g in groups}
        by_option_id = {o.id: o for o in options}
        by_meal_id = {m.id: m for m in meals}

        return {
            "restaurant": {"slug": slug, "name": name},
            "item_types": [
                {
                    "name": t.name,
                    "parent": by_type_id[t.parent_id].name if t.parent_id else None,
                    "sort_order": t.sort_order,
                    "photo": CATEGORY_PHOTOS.get(t.name),
                }
                for t in types
            ],
            "modifier_groups": [
                {
                    "name": g.name,
                    "selection_type": g.selection_type,
                    "is_required": g.is_required,
                    "min_select": g.min_select,
                    "max_select": g.max_select,
                    "offered_on": sorted(
                        by_type_id[gt.item_type_id].name
                        for gt in group_types
                        if gt.group_id == g.id and gt.item_type_id in by_type_id
                    ),
                    "options": [
                        {
                            "name": o.name,
                            "price_delta_minor": o.price_delta_minor,
                            "calories_delta": o.calories_delta,
                            "sort_order": o.sort_order,
                        }
                        for o in options
                        if o.group_id == g.id
                    ],
                }
                for g in groups
            ],
            "items": [
                {
                    "name": i.name,
                    "item_type": by_type_id[i.item_type_id].name,
                    "description": i.description,
                    "price_minor": i.base_price_minor,
                    "calories": i.calories,
                    "currency": i.currency,
                    "sort_order": i.sort_order,
                    "photo": ITEM_PHOTOS.get(i.name),
                    "modifier_groups": [
                        by_group_id[link.group_id].name
                        for link in links
                        if link.item_id == i.id and link.group_id in by_group_id
                    ],
                    "comes_with": [
                        by_option_id[inc.option_id].name
                        for inc in included
                        if inc.item_id == i.id and inc.option_id in by_option_id
                    ],
                }
                for i in items
            ],
            "meals": [
                {
                    "name": m.name,
                    "sort_order": m.sort_order,
                    "items": [
                        by_item_id[mi.item_id].name
                        for mi in meal_items
                        if mi.meal_id == m.id and mi.item_id in by_item_id
                    ],
                }
                for m in meals
            ],
            "combos": [
                {
                    "name": c.name,
                    "meal": by_meal_id[c.meal_id].name if c.meal_id in by_meal_id else None,
                    "description": c.description,
                    "discount_kind": c.discount_kind,
                    "discount_value": c.discount_value,
                    "sort_order": c.sort_order,
                    "slots": [
                        {
                            "item_type": by_type_id[s.item_type_id].name,
                            "choices": [
                                by_item_id[si.item_id].name
                                for si in slot_items
                                if si.slot_id == s.id and si.item_id in by_item_id
                            ],
                        }
                        for s in slots
                        if s.combo_id == c.id
                    ],
                }
                for c in combos
            ],
        }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: export_menu.py <restaurant-slug>")
    print(json.dumps(export(sys.argv[1]), indent=2, ensure_ascii=False))
