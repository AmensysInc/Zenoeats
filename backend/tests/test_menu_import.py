"""Loading a menu onto a restaurant that already exists.

The property worth protecting is that it can be run twice. A menu changes --
a price, a description, an item added for the summer -- and an importer that
can only ever bootstrap is one nobody can use for the second change. So the
tests here are mostly about the second run: same counts, new values, and no
second copy of anything.
"""

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.db.session import system_session, tenant_session
from app.models import Combo, ComboSlot, Item, ItemType, ModifierGroup, ModifierOption

from scripts.import_menu import load

MENU = {
    "item_types": [{"name": "Burgers", "parent": None, "sort_order": 0, "photo": None}],
    "modifier_groups": [
        {
            "name": "Preparation", "selection_type": "MULTI", "is_required": False,
            "min_select": 0, "max_select": 2, "offered_on": ["Burgers"],
            "options": [
                {"name": "Special", "price_delta_minor": 0, "calories_delta": 80, "sort_order": 0},
            ],
        }
    ],
    "items": [
        {
            "name": "Test Double", "item_type": "Burgers", "description": "Two patties.",
            "price_minor": 590, "calories": 670, "currency": "USD", "sort_order": 1,
            "photo": None, "modifier_groups": ["Preparation"], "comes_with": [],
        }
    ],
    "meals": [{"name": "All Day", "sort_order": 1, "items": ["Test Double"]}],
    "combos": [
        {
            "name": "Test Combo", "meal": "All Day", "description": "With fries.",
            "discount_kind": "PERCENT", "discount_value": 500, "sort_order": 0,
            "slots": [{"item_type": "Burgers", "choices": ["Test Double"]}],
        }
    ],
}


@pytest.fixture
def restaurant():
    """An onboarded restaurant with no menu, which is what the importer expects."""
    from app.models import Restaurant, RestaurantStatus

    slug = f"import-{uuid.uuid4().hex[:8]}"
    with system_session() as session:
        row = Restaurant(slug=slug, name="Import Test",
                         status=RestaurantStatus.ACTIVE.value,
                         timezone="UTC", currency="USD")
        session.add(row)
        session.flush()
        rid = row.id

    yield slug, rid

    # A tenant session, not the platform one: every table below is under
    # RLS keyed on app.current_tenant, and the platform session sets no
    # tenant -- the policy then compares against an empty string and the
    # delete fails on the cast rather than on permissions.
    with tenant_session(rid) as session:
        for table in ("combo_slot_items", "combo_slots", "combos", "item_included_options",
                      "item_modifier_groups", "modifier_group_item_types", "modifier_options",
                      "modifier_groups", "meal_items", "menu_items", "item_types", "meals"):
            session.execute(text(f"DELETE FROM {table} WHERE restaurant_id = :r"), {"r": rid})
        session.execute(text("DELETE FROM restaurants WHERE id = :r"), {"r": rid})


def _write(tmp_path: Path, menu: dict) -> Path:
    path = tmp_path / "menu.json"
    path.write_text(json.dumps(menu))
    return path


def _count(rid, model) -> int:
    with tenant_session(rid) as db:
        return len(list(db.scalars(select(model))))


def test_it_loads_a_whole_menu(restaurant, tmp_path):
    slug, rid = restaurant
    load(slug, _write(tmp_path, MENU))

    assert _count(rid, ItemType) == 1
    assert _count(rid, Item) == 1
    assert _count(rid, ModifierGroup) == 1
    assert _count(rid, ModifierOption) == 1
    assert _count(rid, Combo) == 1
    assert _count(rid, ComboSlot) == 1


def test_running_it_twice_updates_rather_than_duplicates(restaurant, tmp_path):
    """The whole reason this exists instead of seed.py, which refuses a
    second run and so can never change a price."""
    slug, rid = restaurant
    load(slug, _write(tmp_path, MENU))

    dearer = json.loads(json.dumps(MENU))
    dearer["items"][0]["price_minor"] = 649
    dearer["items"][0]["description"] = "Two patties, now dearer."
    load(slug, _write(tmp_path, dearer))

    assert _count(rid, Item) == 1, "a second run must not make a second item"
    assert _count(rid, Combo) == 1
    assert _count(rid, ComboSlot) == 1, "slots are rebuilt, not accumulated"
    # Read inside the session: the row is detached once it closes.
    with tenant_session(rid) as db:
        item = db.scalars(select(Item)).one()
        price, description = item.base_price_minor, item.description
    assert price == 649
    assert description == "Two patties, now dearer."


def test_an_item_dropped_from_the_file_is_left_alone(restaurant, tmp_path):
    """Deleting is not this tool's job.

    Orders, favourites and combo slots point at an item, and a typo in a
    menu file must not be able to cascade through them.
    """
    slug, rid = restaurant
    load(slug, _write(tmp_path, MENU))

    without = json.loads(json.dumps(MENU))
    without["items"] = []
    without["meals"][0]["items"] = []
    without["combos"] = []
    load(slug, _write(tmp_path, without))

    assert _count(rid, Item) == 1, "the item should still be there"


def test_it_refuses_a_restaurant_that_does_not_exist(tmp_path):
    """It loads a menu; it does not create a tenant. Onboarding decides the
    name, the Stripe account and who is allowed in."""
    with pytest.raises(SystemExit) as refused:
        load("no-such-restaurant", _write(tmp_path, MENU))
    assert "Onboard it" in str(refused.value)
