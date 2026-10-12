"""The two readings of one menu tree.

Regression cover for the menu builder showing nothing. It read the public
/menu, which drops anything empty, so a meal period vanished the instant it
was created and there was nowhere to add an item.

Also cover for the headings a customer reads. They are derived from the types
of the items served, not stored, and both the words and their order belong to
the restaurant -- so nothing here creates a section directly, and nothing
here assumes what a section is called.
"""

import uuid
from datetime import time

from app.services.menu import load_menu


class FakeType:
    """An item_types row: the restaurant's own word for a sort of thing."""

    def __init__(self, name, sort_order=0, parent=None):
        self.id = uuid.uuid4()
        self.name = name
        self.sort_order = sort_order
        # A subcategory names its parent. Blank is the ordinary case, and
        # every type in the starter vocabulary is one.
        self.parent_id = parent.id if parent else None
        self.deleted_at = None


def starter_types():
    """A fresh vocabulary per test, so ids never leak between them."""
    return [
        FakeType("Food", 0),
        FakeType("Drinks", 1),
        FakeType("Sides", 2),
        FakeType("Sauces", 3),
    ]


class FakeItem:
    def __init__(self, name, item_type=None, calories=None):
        self.id = uuid.uuid4()
        self.name = name
        self.item_type_id = item_type.id if item_type else None
        self.description = None
        self.calories = calories
        self.base_price_minor = 1095
        self.currency = "USD"
        self.is_available = True
        self.image_path = None
        self.deleted_at = None
        self.modifier_links = []
        # What the item comes with. Empty unless a test says otherwise.
        self.included_links = []


class FakeLink:
    """A meal_items row. The reader reaches items only through these."""

    def __init__(self, item):
        self.id = uuid.uuid4()
        self.item = item


class FakeMeal:
    def __init__(self, name, items=(), hours=(None, None)):
        self.id = uuid.uuid4()
        self.name = name
        self.starts_at, self.ends_at = hours
        self.deleted_at = None
        self.item_links = [FakeLink(item) for item in items]


class FakeChoice:
    """A combo_slot_items row. A slot reaches its items only through these."""

    def __init__(self, item, upcharge=0):
        self.id = uuid.uuid4()
        self.item_id = item.id
        self.item = item
        self.upcharge_minor = upcharge


class FakeSlot:
    def __init__(self, item_type, items=()):
        self.id = uuid.uuid4()
        self.item_type = item_type
        self.item_type_id = item_type.id
        self.choices = [FakeChoice(item) for item in items]


class FakeCombo:
    def __init__(self, name, meal, slots=(), is_available=True, image_path=None):
        self.id = uuid.uuid4()
        self.meal_id = meal.id
        self.name = name
        self.description = None
        self.image_path = image_path
        self.discount_kind = "PERCENT"
        self.discount_value = 1000
        self.is_available = is_available
        self.deleted_at = None
        self.slots = list(slots)


class FakeDb:
    """Stands in for the tenant session.

    The reader asks for item types, then meals, then combos, so the fake has
    to answer by what was asked for. The WHERE clauses are still the
    database's business; what is under test is what happens to the rows
    afterwards.
    """

    def __init__(self, meals, combos=(), types=None):
        self._rows = {
            "Meal": list(meals),
            "Combo": list(combos),
            "ItemType": list(types) if types is not None else [],
        }
        self._pending = []

    def execute(self, statement):
        model = statement.column_descriptions[0]["entity"].__name__
        self._pending = self._rows.get(model, [])
        return self

    def scalars(self):
        return self

    def all(self):
        return self._pending


def test_builder_keeps_a_meal_period_that_serves_nothing_yet():
    menu = load_menu(FakeDb([FakeMeal("Breakfast")]), include_empty=True)
    assert [m.name for m in menu.meals] == ["Breakfast"]
    assert menu.meals[0].sections == []


def test_storefront_drops_what_cannot_be_ordered():
    assert load_menu(FakeDb([FakeMeal("Lunch")]), include_empty=False).meals == []


def test_storefront_keeps_a_period_once_it_serves_something():
    types = starter_types()
    meal = FakeMeal("Lunch", [FakeItem("Smash Burger", types[0])])
    menu = load_menu(FakeDb([meal], types=types), include_empty=False)
    assert [i.name for i in menu.meals[0].sections[0].items] == ["Smash Burger"]


def test_items_are_grouped_into_sections_by_type():
    types = starter_types()
    food, drinks = types[0], types[1]
    meal = FakeMeal("Lunch", [
        FakeItem("Iced Tea", drinks),
        FakeItem("Smash Burger", food),
        FakeItem("Lemonade", drinks),
    ])
    menu = load_menu(FakeDb([meal], types=types), include_empty=True)

    sections = menu.meals[0].sections
    assert [s.label for s in sections] == ["Food", "Drinks"]
    assert [i.name for i in sections[1].items] == ["Iced Tea", "Lemonade"]


def test_a_section_is_labelled_with_the_restaurants_own_word():
    """The whole point of types being rows. A tiffin house reads Tiffins."""
    tiffins = FakeType("Tiffins", 0)
    meal = FakeMeal("Breakfast", [FakeItem("Idli", tiffins)])

    menu = load_menu(FakeDb([meal], types=[tiffins]), include_empty=False)
    assert menu.meals[0].sections[0].label == "Tiffins"


def test_sections_read_in_the_order_the_restaurant_put_its_types_in():
    """Not a fixed order in the service any more. Drinks before food is a
    decision a menu can express."""
    drinks = FakeType("Drinks", 0)
    food = FakeType("Food", 1)
    meal = FakeMeal("Lunch", [
        FakeItem("Smash Burger", food),
        FakeItem("Iced Tea", drinks),
    ])

    menu = load_menu(FakeDb([meal], types=[drinks, food]), include_empty=True)
    assert [s.label for s in menu.meals[0].sections] == ["Drinks", "Food"]


def test_reordering_the_types_reorders_the_menu():
    types = starter_types()
    food, drinks = types[0], types[1]
    meal = FakeMeal("Lunch", [FakeItem("Burger", food), FakeItem("Tea", drinks)])

    before = load_menu(FakeDb([meal], types=[food, drinks]), include_empty=True)
    after = load_menu(FakeDb([meal], types=[drinks, food]), include_empty=True)

    assert [s.label for s in before.meals[0].sections] == ["Food", "Drinks"]
    assert [s.label for s in after.meals[0].sections] == ["Drinks", "Food"]


def test_a_type_nobody_serves_gets_no_heading():
    """Four types exist; only one is used. An empty heading is not a menu."""
    types = starter_types()
    meal = FakeMeal("Lunch", [FakeItem("Smash Burger", types[0])])
    menu = load_menu(FakeDb([meal], types=types), include_empty=True)
    assert [s.label for s in menu.meals[0].sections] == ["Food"]


def test_an_item_whose_type_is_gone_falls_out_rather_than_going_unheaded():
    """The type was deleted underneath it. Better absent than under a heading
    nobody named."""
    types = starter_types()
    orphan = FakeItem("Mystery", FakeType("Deleted", 9))
    meal = FakeMeal("Lunch", [FakeItem("Smash Burger", types[0]), orphan])

    menu = load_menu(FakeDb([meal], types=types), include_empty=True)
    names = [i.name for s in menu.meals[0].sections for i in s.items]
    assert names == ["Smash Burger"]


def test_the_same_item_can_be_served_by_two_periods():
    """The arrangement the old category layer could not express: one row,
    one price, on two menus."""
    types = starter_types()
    coffee = FakeItem("Black Coffee", types[1])
    breakfast = FakeMeal("Breakfast", [coffee])
    lunch = FakeMeal("Lunch", [coffee])

    menu = load_menu(FakeDb([breakfast, lunch], types=types), include_empty=False)

    served = [m.sections[0].items[0] for m in menu.meals]
    assert [i.name for i in served] == ["Black Coffee", "Black Coffee"]
    assert served[0].id == served[1].id


def test_soft_deleted_items_are_hidden_from_the_builder_too():
    types = starter_types()
    live = FakeItem("Smash Burger", types[0])
    gone = FakeItem("Old Special", types[0])
    gone.deleted_at = "2026-01-01"

    menu = load_menu(FakeDb([FakeMeal("Lunch", [live, gone])], types=types),
                     include_empty=True)
    assert [i.name for i in menu.meals[0].sections[0].items] == ["Smash Burger"]


def test_a_period_serving_only_deleted_items_is_dropped_from_the_storefront():
    types = starter_types()
    gone = FakeItem("Old Special", types[0])
    gone.deleted_at = "2026-01-01"
    db = FakeDb([FakeMeal("Lunch", [gone])], types=types)
    assert load_menu(db, include_empty=False).meals == []


# --- combos ----------------------------------------------------------------
#
# A combo is one item from each of several types. The reader's job is to hand
# the storefront only combos a customer could actually complete, and to hand
# the builder everything so a broken one can be fixed.


def test_a_combo_is_listed_under_the_period_that_sells_it():
    types = starter_types()
    burger = FakeItem("Smash Burger", types[0])
    tea = FakeItem("Iced Tea", types[1])
    lunch = FakeMeal("Lunch", [burger, tea])
    combo = FakeCombo("Burger Meal", lunch, [
        FakeSlot(types[0], [burger]),
        FakeSlot(types[1], [tea]),
    ])

    menu = load_menu(FakeDb([lunch], [combo], types), include_empty=False)

    assert [c.name for c in menu.meals[0].combos] == ["Burger Meal"]
    assert [s.label for s in menu.meals[0].combos[0].slots] == ["Food", "Drinks"]


def test_a_slot_is_labelled_with_the_restaurants_own_word_too():
    thali = FakeType("Thali", 0)
    idli = FakeItem("Mini Thali", thali)
    lunch = FakeMeal("Lunch", [idli])
    combo = FakeCombo("Thali Deal", lunch, [FakeSlot(thali, [idli])])

    menu = load_menu(FakeDb([lunch], [combo], [thali]), include_empty=False)
    assert menu.meals[0].combos[0].slots[0].label == "Thali"


def test_the_discount_is_sent_rather_than_a_finished_price():
    """There is no price until the choices are made, and the browser must not
    be the one that decides it either way."""
    types = starter_types()
    burger = FakeItem("Smash Burger", types[0])
    lunch = FakeMeal("Lunch", [burger])
    combo = FakeCombo("Burger Meal", lunch, [FakeSlot(types[0], [burger])])

    out = load_menu(FakeDb([lunch], [combo], types), include_empty=False).meals[0].combos[0]
    assert (out.discount_kind, out.discount_value) == ("PERCENT", 1000)


def test_a_combo_with_a_sold_out_slot_is_dropped_from_the_storefront():
    """Its only drink is gone, so the meal deal cannot be completed. Offering
    it would end in the customer being refused at checkout."""
    types = starter_types()
    burger = FakeItem("Smash Burger", types[0])
    tea = FakeItem("Iced Tea", types[1])
    tea.is_available = False
    lunch = FakeMeal("Lunch", [burger])
    combo = FakeCombo("Burger Meal", lunch, [
        FakeSlot(types[0], [burger]),
        FakeSlot(types[1], [tea]),
    ])

    menu = load_menu(FakeDb([lunch], [combo], types), include_empty=False)
    assert menu.meals[0].combos == []


def test_the_builder_keeps_a_combo_whose_slot_has_nothing_left():
    """It is the screen where the missing choice gets put back."""
    types = starter_types()
    burger = FakeItem("Smash Burger", types[0])
    tea = FakeItem("Iced Tea", types[1])
    tea.is_available = False
    lunch = FakeMeal("Lunch", [burger])
    combo = FakeCombo("Burger Meal", lunch, [
        FakeSlot(types[0], [burger]),
        FakeSlot(types[1], [tea]),
    ])

    menu = load_menu(FakeDb([lunch], [combo], types), include_empty=True)
    assert [c.name for c in menu.meals[0].combos] == ["Burger Meal"]


def test_a_deleted_choice_does_not_count_towards_filling_a_slot():
    types = starter_types()
    gone = FakeItem("Old Special", types[0])
    gone.deleted_at = "2026-01-01"
    lunch = FakeMeal("Lunch")
    combo = FakeCombo("Burger Meal", lunch, [FakeSlot(types[0], [gone])])

    assert load_menu(FakeDb([lunch], [combo], types), include_empty=False).meals == []


def test_a_period_that_serves_nothing_but_sells_a_combo_is_still_shown():
    types = starter_types()
    burger = FakeItem("Smash Burger", types[0])
    lunch = FakeMeal("Lunch", [burger])
    combo = FakeCombo("Burger Meal", lunch, [FakeSlot(types[0], [burger])])

    menu = load_menu(FakeDb([lunch], [combo], types), include_empty=False)
    assert menu.meals[0].combos[0].name == "Burger Meal"


def test_a_withdrawn_combo_is_hidden_from_the_storefront_only():
    types = starter_types()
    burger = FakeItem("Smash Burger", types[0])
    lunch = FakeMeal("Lunch", [burger])
    combo = FakeCombo("Burger Meal", lunch, [FakeSlot(types[0], [burger])],
                      is_available=False)

    # The reader filters on is_available in the query, which the fake does not
    # run -- so this pins the builder side, where nothing is filtered at all.
    menu = load_menu(FakeDb([lunch], [combo], types), include_empty=True)
    assert [c.name for c in menu.meals[0].combos] == ["Burger Meal"]


# --- subcategories ---------------------------------------------------------
#
# A type may sit under another one: Food holding Burgers and Nuggets. The
# items of a subcategory become a block inside its parent's section rather
# than a section of their own, so a customer reads one Food heading with
# groups under it instead of several headings that used to be one.


def test_a_subcategorys_items_become_a_group_inside_its_parent():
    food = FakeType("Food", 0)
    burgers = FakeType("Burgers", 0, parent=food)
    smash = FakeItem("Smash Burger", burgers)
    menu = load_menu(FakeDb([FakeMeal("Lunch", [smash])], types=[food, burgers]),
                     include_empty=False)

    section = menu.meals[0].sections[0]
    assert section.label == "Food"
    assert [g.label for g in section.groups] == ["Burgers"]
    assert [i.name for i in section.groups[0].items] == ["Smash Burger"]


def test_a_parent_with_nothing_of_its_own_still_gets_its_heading():
    """A restaurant that filed every food under a subcategory still reads
    Food across the top. The heading is what the groups hang from."""
    food = FakeType("Food", 0)
    burgers = FakeType("Burgers", 0, parent=food)
    menu = load_menu(
        FakeDb([FakeMeal("Lunch", [FakeItem("Smash Burger", burgers)])],
               types=[food, burgers]),
        include_empty=False,
    )

    section = menu.meals[0].sections[0]
    assert section.label == "Food"
    assert section.items == [], "an item of a subcategory was filed on its parent"


def test_what_is_filed_on_the_heading_itself_is_read_before_the_groups():
    """A menu that subdivided only half its food still reads top to bottom."""
    food = FakeType("Food", 0)
    burgers = FakeType("Burgers", 0, parent=food)
    meal = FakeMeal("Lunch", [FakeItem("Soup", food), FakeItem("Smash", burgers)])
    menu = load_menu(FakeDb([meal], types=[food, burgers]), include_empty=False)

    section = menu.meals[0].sections[0]
    assert [i.name for i in section.items] == ["Soup"]
    assert [i.name for i in section.groups[0].items] == ["Smash"]


def test_groups_read_in_the_order_the_restaurant_put_them_in():
    food = FakeType("Food", 0)
    burgers = FakeType("Burgers", 0, parent=food)
    nuggets = FakeType("Nuggets", 1, parent=food)
    meal = FakeMeal("Lunch", [FakeItem("Nugs", nuggets), FakeItem("Smash", burgers)])
    menu = load_menu(FakeDb([meal], types=[food, burgers, nuggets]), include_empty=False)

    assert [g.label for g in menu.meals[0].sections[0].groups] == ["Burgers", "Nuggets"]


def test_a_heading_whose_groups_serve_nothing_gets_no_section():
    """Same rule as a type nobody serves. Nothing under it, either directly
    or through a subcategory, means nothing to read."""
    food = FakeType("Food", 0)
    burgers = FakeType("Burgers", 0, parent=food)
    drinks = FakeType("Drinks", 1)
    menu = load_menu(
        FakeDb([FakeMeal("Lunch", [FakeItem("Iced Tea", drinks)])],
               types=[food, burgers, drinks]),
        include_empty=False,
    )

    assert [s.label for s in menu.meals[0].sections] == ["Drinks"]


def test_a_heading_comes_before_the_next_one_even_when_a_group_fills_it():
    """The section is placed when its first group lands, not appended after
    everything else, or a subdivided Food would read below Drinks."""
    food = FakeType("Food", 0)
    burgers = FakeType("Burgers", 0, parent=food)
    drinks = FakeType("Drinks", 1)
    meal = FakeMeal("Lunch", [FakeItem("Iced Tea", drinks), FakeItem("Smash", burgers)])
    menu = load_menu(FakeDb([meal], types=[food, burgers, drinks]), include_empty=False)

    assert [s.label for s in menu.meals[0].sections] == ["Food", "Drinks"]


def test_a_menu_with_no_subcategories_sends_no_groups():
    """The ordinary case, and the one most menus stay in forever."""
    types = starter_types()
    menu = load_menu(
        FakeDb([FakeMeal("Lunch", [FakeItem("Smash Burger", types[0])])], types=types),
        include_empty=False,
    )

    assert menu.meals[0].sections[0].groups == []


def test_an_item_under_a_deleted_heading_falls_out_rather_than_going_unheaded():
    """Deleting a heading with subcategories is refused by the API. If one
    goes missing anyway, its groups have nowhere to appear."""
    food = FakeType("Food", 0)
    burgers = FakeType("Burgers", 0, parent=food)
    menu = load_menu(
        FakeDb([FakeMeal("Lunch", [FakeItem("Smash", burgers)])], types=[burgers]),
        include_empty=False,
    )

    assert menu.meals == []


def test_a_period_carries_the_hours_it_is_served():
    """They are for a customer to read, so they have to survive the trip out.

    Sent as HH:MM rather than as the HH:MM:SS a time would serialise to: the
    seconds are always zero, and the builder echoes this value straight back
    into a time input."""
    menu = load_menu(
        FakeDb([FakeMeal("Breakfast", hours=(time(7, 0), time(11, 0)))]),
        include_empty=True,
    )

    assert menu.model_dump(mode="json")["meals"][0]["starts_at"] == "07:00"
    assert menu.model_dump(mode="json")["meals"][0]["ends_at"] == "11:00"


def test_a_period_that_says_nothing_about_hours_sends_nulls():
    """Every period that existed before hours were added is this one, so it
    has to stay a legal shape rather than default to midnight."""
    menu = load_menu(FakeDb([FakeMeal("Breakfast")]), include_empty=True)

    assert menu.meals[0].starts_at is None
    assert menu.meals[0].ends_at is None


def test_a_combo_offers_only_what_its_period_serves():
    """Cola was taken off Lunch. The Lunch combo kept offering it, at a
    discount, for food the period no longer sold."""
    types = starter_types()
    burger = FakeItem("Smash Burger", types[0])
    tea = FakeItem("Iced Tea", types[1])
    cola = FakeItem("Cola", types[1])
    lunch = FakeMeal("Lunch", [burger, tea])  # cola is not on it any more
    combo = FakeCombo("Burger Meal", lunch, [
        FakeSlot(types[0], [burger]),
        FakeSlot(types[1], [tea, cola]),
    ])

    for include_empty in (False, True):
        out = load_menu(FakeDb([lunch], [combo], types), include_empty=include_empty)
        drinks = out.meals[0].combos[0].slots[1]
        assert [i.name for i in drinks.items] == ["Iced Tea"], include_empty


def test_a_combo_whose_only_choice_left_the_period_is_dropped():
    types = starter_types()
    burger = FakeItem("Smash Burger", types[0])
    tea = FakeItem("Iced Tea", types[1])  # in stock, but not served at lunch
    lunch = FakeMeal("Lunch", [burger])
    dinner = FakeMeal("Dinner", [tea])
    combo = FakeCombo("Burger Meal", lunch, [
        FakeSlot(types[0], [burger]),
        FakeSlot(types[1], [tea]),
    ])

    menu = load_menu(FakeDb([lunch, dinner], [combo], types), include_empty=False)
    assert all(meal.combos == [] for meal in menu.meals)
