"""The menu API refuses what the builder refuses.

The builder checks names and modifier-group rules before it sends anything,
but the API stored whatever arrived: an item named "   ", a pick-one group
with a minimum of three, a required group asking for more choices than it has
options. The last two make every item offering the group impossible to order.
"""

import pytest

from app.core import staff_auth

from tests.test_admin_restaurants import (  # noqa: F401  (fixtures)
    _create,
    _email,
    _owner,
    _set_own_password,
    _staff_client,
    admin_user,
    cleanup,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def manager(admin_user, cleanup):
    restaurant = _create(admin_user, cleanup)
    email = _email()
    owner, _ = _owner(admin_user, restaurant.id, email)
    _set_own_password(email, "owner password 123")
    client = _staff_client(restaurant.slug)
    client.cookies.set(staff_auth.SESSION_COOKIE, staff_auth.issue_session(owner.user_id))
    return client


def _types(client):
    return {t["name"]: t["id"] for t in client.get("/api/v1/restaurant/item-types").json()}


def _group(client, **overrides):
    body = {
        "name": "Veggies", "selection_type": "MULTI", "is_required": False,
        "min_select": 0, "max_select": 3,
        "options": [{"name": "Lettuce"}, {"name": "Onion"}, {"name": "Pickles"}],
    }
    body.update(overrides)
    return client.post("/api/v1/restaurant/modifier-groups", json=body)


def _message(res):
    return res.json()["detail"]["message"]


# --------------------------------------------------------------- names ---

def test_blank_names_are_refused_where_things_are_created(manager):
    food = _types(manager)["Food"]

    item = manager.post(
        "/api/v1/restaurant/items",
        json={"name": "   ", "item_type_id": food, "base_price_minor": 500},
    )
    assert item.status_code == 422 and _message(item) == "An item needs a name."

    meal = manager.post("/api/v1/restaurant/meals", json={"name": "  "})
    assert meal.status_code == 422 and _message(meal) == "A meal period needs a name."

    assert _group(manager, name="  ").status_code == 422
    blank_option = _group(manager, options=[{"name": "Lettuce"}, {"name": " "}])
    assert blank_option.status_code == 422 and _message(blank_option) == "Every option needs a name."

    assert manager.get("/api/v1/restaurant/items").json() == []
    assert manager.get("/api/v1/restaurant/modifier-groups").json() == []


def test_names_are_stored_trimmed(manager):
    food = _types(manager)["Food"]
    item = manager.post(
        "/api/v1/restaurant/items",
        json={"name": "  Smash Burger ", "item_type_id": food, "base_price_minor": 500,
              "description": "   "},
    )
    assert item.status_code == 201 and item.json()["name"] == "Smash Burger"
    listed = manager.get("/api/v1/restaurant/items").json()[0]
    assert (listed["name"], listed["description"]) == ("Smash Burger", None)

    group = _group(manager, name=" Veggies ", options=[{"name": " Lettuce "}])
    assert group.status_code == 201
    stored = manager.get("/api/v1/restaurant/modifier-groups").json()[0]
    assert (stored["name"], stored["options"][0]["name"]) == ("Veggies", "Lettuce")


# --------------------------------------------------------------- rules ---

@pytest.mark.parametrize(
    "overrides, says",
    [
        ({"options": []}, "needs at least one option"),
        ({"selection_type": "SINGLE", "is_required": True, "min_select": 3, "max_select": 5},
         "is pick-one"),
        ({"selection_type": "SINGLE", "is_required": False, "min_select": 0, "max_select": 2},
         "is pick-one"),
        ({"is_required": True, "min_select": 0, "max_select": 3}, "is required"),
        ({"is_required": False, "min_select": 2, "max_select": 3}, "is optional"),
        ({"is_required": True, "min_select": 4, "max_select": 5}, "has only 3 options"),
        ({"is_required": True, "min_select": 3, "max_select": 2}, "below its minimum"),
    ],
)
def test_a_group_no_customer_could_complete_is_refused(manager, overrides, says):
    res = _group(manager, **overrides)
    assert res.status_code == 422, res.text
    assert says in _message(res)
    assert manager.get("/api/v1/restaurant/modifier-groups").json() == []


@pytest.mark.parametrize(
    "overrides",
    [
        {},  # optional, pick up to three
        {"selection_type": "SINGLE", "is_required": True, "min_select": 1, "max_select": 1},
        {"selection_type": "SINGLE", "is_required": False, "min_select": 0, "max_select": 1},
        {"is_required": True, "min_select": 3, "max_select": 3},
        {"is_required": False, "min_select": 0, "max_select": 10},  # a max above the options is harmless
    ],
)
def test_the_groups_the_builder_makes_are_accepted(manager, overrides):
    assert _group(manager, **overrides).status_code == 201


# ----------------------------------------------------- deleting options ---

def test_an_option_a_required_group_needs_cannot_be_deleted(manager):
    _group(manager, name="Two sauces", is_required=True, min_select=2, max_select=2,
           options=[{"name": "Ketchup"}, {"name": "Mayo"}, {"name": "Mustard"}])
    options = manager.get("/api/v1/restaurant/modifier-groups").json()[0]["options"]

    first = manager.delete(f"/api/v1/restaurant/modifier-options/{options[0]['id']}")
    assert first.status_code == 200  # three down to two still works

    second = manager.delete(f"/api/v1/restaurant/modifier-options/{options[1]['id']}")
    assert second.status_code == 422
    assert "needs at least 2 options" in _message(second)
    assert len(manager.get("/api/v1/restaurant/modifier-groups").json()[0]["options"]) == 2


def test_the_last_option_of_a_group_cannot_be_deleted(manager):
    _group(manager, options=[{"name": "Lettuce"}])
    option = manager.get("/api/v1/restaurant/modifier-groups").json()[0]["options"][0]

    res = manager.delete(f"/api/v1/restaurant/modifier-options/{option['id']}")
    assert res.status_code == 422
    assert "last option" in _message(res)


# ------------------------------------------------------- editing rules ---
#
# A group's rules used to be fixed at creation: correcting "up to 2" meant
# deleting the group and attaching a new one to every item.


def _edit(client, group_id, **changes):
    return client.patch(f"/api/v1/restaurant/modifier-groups/{group_id}", json=changes)


def _only_group(client):
    return client.get("/api/v1/restaurant/modifier-groups").json()[0]


def test_a_groups_rules_can_be_changed(manager):
    group_id = _group(manager).json()["id"]  # optional, up to 3

    res = _edit(manager, group_id, max_select=2, is_required=True, min_select=1)
    assert res.status_code == 200, res.text
    stored = _only_group(manager)
    assert (stored["is_required"], stored["min_select"], stored["max_select"]) == (True, 1, 2)

    res = _edit(manager, group_id, selection_type="SINGLE", max_select=1)
    assert res.status_code == 200, res.text
    assert _only_group(manager)["selection_type"] == "SINGLE"


def test_a_rule_change_is_checked_as_the_group_it_makes(manager):
    group_id = _group(manager).json()["id"]  # optional, min 0, up to 3

    # Required on its own leaves the minimum at 0, which is not a required group.
    alone = _edit(manager, group_id, is_required=True)
    assert alone.status_code == 422 and "is required" in _message(alone)

    single = _edit(manager, group_id, selection_type="SINGLE")  # max still 3
    assert single.status_code == 422 and "is pick-one" in _message(single)

    too_many = _edit(manager, group_id, is_required=True, min_select=4, max_select=4)
    assert too_many.status_code == 422 and "has only 3 options" in _message(too_many)

    stored = _only_group(manager)
    assert (stored["selection_type"], stored["is_required"], stored["min_select"],
            stored["max_select"]) == ("MULTI", False, 0, 3)


def test_the_maximum_cannot_drop_below_what_an_item_comes_with(manager):
    group_id = _group(manager).json()["id"]
    options = [o["id"] for o in _only_group(manager)["options"]]
    food = _types(manager)["Food"]
    item = manager.post(
        "/api/v1/restaurant/items",
        json={"name": "Loaded Burger", "item_type_id": food, "base_price_minor": 900,
              "modifier_group_ids": [group_id], "included_option_ids": options},
    )
    assert item.status_code == 201, item.text

    res = _edit(manager, group_id, max_select=2)
    assert res.status_code == 422
    assert _message(res).startswith("Loaded Burger comes with 3 options from Veggies")
    assert _only_group(manager)["max_select"] == 3

    # Once it comes with fewer, the lower maximum is fine.
    manager.patch(f"/api/v1/restaurant/items/{item.json()['id']}",
                  json={"included_option_ids": options[:2]})
    assert _edit(manager, group_id, max_select=2).status_code == 200


def test_renaming_alone_leaves_the_rules_untouched(manager):
    group_id = _group(manager, is_required=True, min_select=2, max_select=3).json()["id"]
    assert _edit(manager, group_id, name="Toppings").status_code == 200
    stored = _only_group(manager)
    assert (stored["name"], stored["min_select"], stored["max_select"]) == ("Toppings", 2, 3)


# ------------------------------------------------------ refiling types ---


def _lunch_combo(client, types):
    """A Lunch combo asking for one of each of Food and Sides."""
    lunch = client.post("/api/v1/restaurant/meals", json={"name": "Lunch"}).json()["id"]
    ids = {}
    for name, type_name in (("Smash Burger", "Food"), ("Fries", "Sides")):
        ids[type_name] = client.post(
            "/api/v1/restaurant/items",
            json={"name": name, "item_type_id": types[type_name], "base_price_minor": 500,
                  "meal_ids": [lunch]},
        ).json()["id"]
    combo = client.post(
        "/api/v1/restaurant/combos",
        json={"meal_id": lunch, "name": "Burger Meal",
              "slots": [{"item_type_id": types[t], "item_ids": [ids[t]]} for t in ("Food", "Sides")]},
    )
    assert combo.status_code == 201, combo.text
    return combo.json()["id"]


def test_a_type_in_a_live_combo_cannot_become_a_subcategory(manager):
    types = _types(manager)
    _lunch_combo(manager, types)

    res = manager.patch(f"/api/v1/restaurant/item-types/{types['Sides']}",
                        json={"parent_id": types["Food"]})
    assert res.status_code == 422
    assert "is a choice in 1 combo (Burger Meal)" in _message(res)


def test_a_deleted_combo_no_longer_holds_its_types_back(manager):
    """Sides was once in a combo. The combo is gone, and the type used to stay
    stuck as a heading for good, blamed on a combo nobody could see."""
    types = _types(manager)
    combo = _lunch_combo(manager, types)
    assert manager.delete(f"/api/v1/restaurant/combos/{combo}").status_code == 200

    res = manager.patch(f"/api/v1/restaurant/item-types/{types['Sides']}",
                        json={"parent_id": types["Food"]})
    assert res.status_code == 200, res.text
    assert res.json()["parent_id"] == types["Food"]


# ------------------------------------------------------ fixed-price combos ---

def _meal_with_sides(client, types):
    lunch = client.post("/api/v1/restaurant/meals", json={"name": "Lunch"}).json()["id"]
    ids = {}
    for name, type_name, price in (
        ("Double Cheese", "Food", 589), ("French Fries", "Sides", 259),
        ("Cheese Fries", "Sides", 439),
    ):
        ids[name] = client.post(
            "/api/v1/restaurant/items",
            json={"name": name, "item_type_id": types[type_name], "base_price_minor": price,
                  "meal_ids": [lunch]},
        ).json()["id"]
    return lunch, ids


def test_a_fixed_price_combo_keeps_its_price_and_upcharges(manager):
    types = _types(manager)
    lunch, ids = _meal_with_sides(manager, types)
    res = manager.post("/api/v1/restaurant/combos", json={
        "meal_id": lunch, "name": "Double Cheese Combo",
        "discount_kind": "FIXED_PRICE", "discount_value": 899,
        "slots": [
            {"item_type_id": types["Food"], "item_ids": [ids["Double Cheese"]]},
            {"item_type_id": types["Sides"],
             "item_ids": [ids["French Fries"], ids["Cheese Fries"]],
             "upcharges": {ids["Cheese Fries"]: 100}},
        ],
    })
    assert res.status_code == 201, res.text

    [combo] = manager.get("/api/v1/restaurant/combos").json()
    assert (combo["discount_kind"], combo["discount_value"]) == ("FIXED_PRICE", 899)
    sides = combo["slots"][1]
    assert sides["upcharges"] == {ids["Cheese Fries"]: 100}  # only the dearer one

    # Editing the slots without upcharges clears them, as any slot edit does.
    res = manager.patch(f"/api/v1/restaurant/combos/{combo['id']}", json={"slots": [
        {"item_type_id": types["Food"], "item_ids": [ids["Double Cheese"]]},
        {"item_type_id": types["Sides"], "item_ids": [ids["French Fries"], ids["Cheese Fries"]]},
    ]})
    assert res.status_code == 200, res.text
    assert res.json()["slots"][1]["upcharges"] == {}


@pytest.mark.parametrize("change, says", [
    ({"discount_kind": "FIXED_PRICE", "discount_value": 0}, "price"),
    ({"discount_kind": "FIXED_PRICE", "discount_value": 899,
      "upcharge_on": "Double Cheese"}, "does not offer"),
    ({"discount_kind": "FIXED_PRICE", "discount_value": 899,
      "upcharge_amount": -50}, "negative"),
])
def test_a_fixed_price_combo_that_makes_no_sense_is_refused(manager, change, says):
    types = _types(manager)
    lunch, ids = _meal_with_sides(manager, types)
    upcharge_on = ids[change.pop("upcharge_on", "Cheese Fries")]
    amount = change.pop("upcharge_amount", 100)
    res = manager.post("/api/v1/restaurant/combos", json={
        "meal_id": lunch, "name": "Bad Combo", **change,
        "slots": [
            {"item_type_id": types["Food"], "item_ids": [ids["Double Cheese"]]},
            {"item_type_id": types["Sides"], "item_ids": [ids["Cheese Fries"]],
             "upcharges": {upcharge_on: amount}},
        ],
    })
    assert res.status_code == 422, res.text
    assert says in _message(res)
