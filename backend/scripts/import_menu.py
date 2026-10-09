"""Load a menu into a restaurant that already exists.

    docker compose exec api python scripts/import_menu.py jrs-corner menus/jrs-corner.json

Separate from seed.py on purpose. The seed bootstraps a whole demo tenant --
a restaurant called Spice House, a placeholder Stripe account, an owner with
a printed password, a dev customer, four locations -- and refuses to run a
second time. None of that is wanted when the job is "put this menu on that
restaurant", and the refusal makes it useless for the thing a real menu
needs most: changing.

So this touches the menu and nothing else. It never creates a restaurant,
never writes to Stripe, never makes a user. It expects the restaurant to
have been onboarded already, through the admin portal, by somebody who
checked the details.

RE-RUNNABLE, which is the point. Everything is matched by name and updated
in place, so editing a price in the file and running it again changes that
price rather than producing a second item. A name is what a restaurant
actually calls a dish, and what somebody editing the file will key on; ids
would mean the file could only ever be loaded into the database that
produced it.

WHAT IT WILL NOT DO is delete. An item that disappears from the file is left
alone and reported, not removed -- orders, favourites and combo slots point
at it, and a typo in a menu file should not be able to cascade through them.
Taking something off a menu is a decision for the portal, where the
consequences are shown.
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
from app.services import images  # noqa: E402
from app.services.images import ImageKind  # noqa: E402

ASSETS = Path(__file__).resolve().parents[1] / "app" / "seed_assets"


def _stored(restaurant_id, asset: str, kind: ImageKind) -> str | None:
    """One committed photograph, through the ordinary image pipeline.

    The same path an upload from the portal takes, so an imported menu's
    pictures are produced by the same code as a restaurant's own -- there is
    no second way for an image to get into this system.
    """
    source = ASSETS / f"{asset}.webp"
    if not source.is_file():
        print(f"  ! no asset {asset}.webp; leaving the photo unset")
        return None
    key = images.new_key(restaurant_id, kind)
    images.storage().save(key, images.process(source.read_bytes(), kind))
    return images.accept(key, restaurant_id, kind)


def load(slug: str, path: Path) -> None:
    menu = json.loads(path.read_text())

    with system_session() as session:
        row = session.execute(
            text("SELECT id, name FROM restaurants WHERE slug = :s AND deleted_at IS NULL"),
            {"s": slug},
        ).first()
        if row is None:
            raise SystemExit(
                f"No restaurant with slug {slug!r}. Onboard it in the admin portal first -- "
                "this loads a menu, it does not create a tenant."
            )
        rid, restaurant_name = row.id, row.name

    print(f"Loading {path.name} into {restaurant_name} ({slug})")
    counts = {"created": 0, "updated": 0}

    with tenant_session(rid) as db:
        # --- headings ----------------------------------------------------
        types: dict[str, ItemType] = {
            t.name: t for t in db.scalars(select(ItemType))
        }
        for spec in menu.get("item_types", []):
            existing = types.get(spec["name"])
            if existing is None:
                existing = ItemType(restaurant_id=rid, name=spec["name"])
                db.add(existing)
                counts["created"] += 1
            else:
                counts["updated"] += 1
            existing.sort_order = spec.get("sort_order", 0)
            types[spec["name"]] = existing
        db.flush()
        # Parents in a second pass: a child may be listed before its parent.
        for spec in menu.get("item_types", []):
            parent = spec.get("parent")
            types[spec["name"]].parent_id = types[parent].id if parent else None
        db.flush()

        # --- modifier groups and their options ----------------------------
        groups: dict[str, ModifierGroup] = {
            g.name: g for g in db.scalars(select(ModifierGroup))
        }
        options: dict[tuple[str, str], ModifierOption] = {
            (g.name, o.name): o
            for g in groups.values()
            for o in db.scalars(select(ModifierOption).where(ModifierOption.group_id == g.id))
        }
        for spec in menu.get("modifier_groups", []):
            group = groups.get(spec["name"])
            if group is None:
                group = ModifierGroup(restaurant_id=rid, name=spec["name"])
                db.add(group)
                counts["created"] += 1
            else:
                counts["updated"] += 1
            group.selection_type = spec["selection_type"]
            group.is_required = spec["is_required"]
            group.min_select = spec["min_select"]
            group.max_select = spec["max_select"]
            groups[spec["name"]] = group
            db.flush()

            # Which headings offer it. Rebuilt rather than merged: this is a
            # small join table with no history, and a stale row here offers
            # a fry style on a milkshake.
            db.execute(
                text("DELETE FROM modifier_group_item_types WHERE group_id = :g"),
                {"g": group.id},
            )
            for type_name in spec.get("offered_on", []):
                if type_name in types:
                    db.add(ModifierGroupItemType(
                        restaurant_id=rid, group_id=group.id,
                        item_type_id=types[type_name].id,
                    ))

            for order, opt in enumerate(spec.get("options", [])):
                key = (spec["name"], opt["name"])
                option = options.get(key)
                if option is None:
                    option = ModifierOption(
                        restaurant_id=rid, group_id=group.id, name=opt["name"]
                    )
                    db.add(option)
                option.price_delta_minor = opt.get("price_delta_minor", 0)
                option.calories_delta = opt.get("calories_delta")
                option.sort_order = opt.get("sort_order", order)
                options[key] = option
        db.flush()

        # --- the dishes ----------------------------------------------------
        items: dict[str, Item] = {i.name: i for i in db.scalars(select(Item))}
        for spec in menu.get("items", []):
            item = items.get(spec["name"])
            if item is None:
                item = Item(restaurant_id=rid, name=spec["name"])
                db.add(item)
                counts["created"] += 1
            else:
                counts["updated"] += 1
            item.item_type_id = types[spec["item_type"]].id
            item.description = spec.get("description")
            item.base_price_minor = spec["price_minor"]
            item.calories = spec.get("calories")
            item.currency = spec.get("currency", "USD")
            item.sort_order = spec.get("sort_order", 0)
            items[spec["name"]] = item
            db.flush()

            # A photo is written once. Re-running must not fill the disk with
            # a fresh copy of the same picture on every import.
            if spec.get("photo") and not item.image_path:
                item.image_path = _stored(rid, spec["photo"], ImageKind.ITEMS)

            db.execute(text("DELETE FROM item_modifier_groups WHERE item_id = :i"),
                       {"i": item.id})
            for order, group_name in enumerate(spec.get("modifier_groups", [])):
                if group_name in groups:
                    db.add(ItemModifierGroup(
                        restaurant_id=rid, item_id=item.id,
                        group_id=groups[group_name].id, sort_order=order,
                    ))

            db.execute(text("DELETE FROM item_included_options WHERE item_id = :i"),
                       {"i": item.id})
            for option_name in spec.get("comes_with", []):
                for (group_name, name), option in options.items():
                    if name == option_name and group_name in spec.get("modifier_groups", []):
                        db.add(ItemIncludedOption(
                            restaurant_id=rid, item_id=item.id, option_id=option.id
                        ))
                        break
        db.flush()

        for spec in menu.get("item_types", []):
            asset = spec.get("photo")
            heading = types[spec["name"]]
            if asset and not heading.image_path:
                heading.image_path = _stored(rid, asset, ImageKind.CATEGORIES)
        db.flush()

        # --- meal periods ---------------------------------------------------
        meals: dict[str, Meal] = {m.name: m for m in db.scalars(select(Meal))}
        for spec in menu.get("meals", []):
            meal = meals.get(spec["name"])
            if meal is None:
                meal = Meal(restaurant_id=rid, name=spec["name"])
                db.add(meal)
                counts["created"] += 1
            else:
                counts["updated"] += 1
            meal.sort_order = spec.get("sort_order", 0)
            meals[spec["name"]] = meal
            db.flush()

            db.execute(text("DELETE FROM meal_items WHERE meal_id = :m"), {"m": meal.id})
            for order, item_name in enumerate(spec.get("items", [])):
                if item_name in items:
                    db.add(MealItem(restaurant_id=rid, meal_id=meal.id,
                                    item_id=items[item_name].id, sort_order=order))
        db.flush()

        # --- meal deals ------------------------------------------------------
        combos: dict[str, Combo] = {c.name: c for c in db.scalars(select(Combo))}
        for spec in menu.get("combos", []):
            combo = combos.get(spec["name"])
            if combo is None:
                combo = Combo(restaurant_id=rid, name=spec["name"])
                db.add(combo)
                counts["created"] += 1
            else:
                counts["updated"] += 1
            combo.meal_id = meals[spec["meal"]].id if spec.get("meal") in meals else None
            combo.description = spec.get("description")
            combo.discount_kind = spec["discount_kind"]
            combo.discount_value = spec["discount_value"]
            combo.sort_order = spec.get("sort_order", 0)
            combos[spec["name"]] = combo
            db.flush()

            # Slots are rebuilt. They are the deal's shape rather than things
            # anything else points at, and matching them by position would
            # silently reassign choices when a slot is inserted.
            slot_ids = [s.id for s in db.scalars(
                select(ComboSlot).where(ComboSlot.combo_id == combo.id)
            )]
            if slot_ids:
                db.execute(text("DELETE FROM combo_slot_items WHERE slot_id = ANY(:s)"),
                           {"s": slot_ids})
                db.execute(text("DELETE FROM combo_slots WHERE combo_id = :c"),
                           {"c": combo.id})
            for order, slot_spec in enumerate(spec.get("slots", [])):
                slot = ComboSlot(restaurant_id=rid, combo_id=combo.id,
                                 item_type_id=types[slot_spec["item_type"]].id,
                                 sort_order=order)
                db.add(slot)
                db.flush()
                for position, choice in enumerate(slot_spec.get("choices", [])):
                    if choice in items:
                        db.add(ComboSlotItem(restaurant_id=rid, slot_id=slot.id,
                                             item_id=items[choice].id, sort_order=position))

        # --- what the file did not mention -----------------------------------
        named = {spec["name"] for spec in menu.get("items", [])}
        orphans = sorted(name for name in items if name not in named)

    print(f"  {counts['created']} created, {counts['updated']} updated")
    if orphans:
        print("  left alone, not in the file (remove them in the portal if they are gone):")
        for name in orphans:
            print(f"    - {name}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: import_menu.py <restaurant-slug> <menu.json>")
    load(sys.argv[1], Path(sys.argv[2]))
