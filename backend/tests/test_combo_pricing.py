"""What a combo costs, and what it refuses to sell.

Rule 3 applies here more than anywhere: the browser sends which combo and
which choices, never a price. Everything a combo is worth is read from the
database on the quote and read again on the order.

The saving does not hide inside a line. Each slot is priced as the item it
holds, exactly as if ordered alone, and the discount comes off the cart -- so
the subtotal is still what the food costs and the saving is a number a
receipt can show and a manager can check.
"""

import uuid

import pytest

from app.core import errors
from app.services.pricing import combo_discount_minor


# --- the arithmetic --------------------------------------------------------
#
# Split out because it is the part that decides money and the part a rounding
# mistake would quietly bury.


def test_a_percentage_is_taken_off_the_items():
    assert combo_discount_minor("PERCENT", 1000, 1000) == 100


def test_a_percentage_rounds_half_up_like_tax_does():
    """1250 bps of 1005 is 125.625. Two different roundings in one order
    total is how a receipt stops adding up."""
    assert combo_discount_minor("PERCENT", 1250, 1005) == 126


def test_a_flat_amount_is_taken_off_the_items():
    assert combo_discount_minor("AMOUNT", 200, 1000) == 200


def test_a_flat_amount_never_exceeds_what_the_items_cost():
    """A pound off a combination that comes to fifty pence would make the
    line negative and hand money back."""
    assert combo_discount_minor("AMOUNT", 5000, 1000) == 1000


def test_a_percentage_of_everything_is_free_but_never_less_than_free():
    assert combo_discount_minor("PERCENT", 10000, 1000) == 1000


def test_no_discount_means_no_discount():
    """The value is ignored, not applied. A kind of NONE with a stale value
    left over from a previous edit must not quietly come back."""
    assert combo_discount_minor("NONE", 999, 1000) == 0


def test_nothing_is_taken_off_an_empty_total():
    assert combo_discount_minor("PERCENT", 5000, 0) == 0


# --- the whole combo, through price_cart -----------------------------------


class FakeOption:
    def __init__(self, name, delta=0):
        self.id = uuid.uuid4()
        self.name = name
        self.price_delta_minor = delta
        self.is_available = True
        self.is_default = False
        self.deleted_at = None


class FakeGroup:
    def __init__(self, name, options, required=False, selection="MULTI"):
        self.id = uuid.uuid4()
        self.name = name
        self.options = options
        self.is_required = required
        self.selection_type = selection
        self.min_select = 1 if required else 0
        self.max_select = 5
        self.deleted_at = None


class FakeGroupLink:
    def __init__(self, group):
        self.group = group


class FakeType:
    """An item_types row. Its name is what a slot prompt reads."""

    def __init__(self, name, parent=None):
        self.id = uuid.uuid4()
        self.name = name
        self.parent_id = parent.id if parent else None
        self.deleted_at = None


# The meal period every fake item is served in and every fake combo belongs
# to, unless a test says otherwise.
LUNCH = uuid.uuid4()
DINNER = uuid.uuid4()


class FakeMealLink:
    """A meal_items row: this period serves this item."""

    def __init__(self, meal_id):
        self.meal_id = meal_id


FOOD = FakeType("Food")
DRINKS = FakeType("Drinks")
SIDES = FakeType("Sides")


class FakeIncluded:
    """An item_included_options row: what the item comes with."""

    def __init__(self, option):
        self.option_id = option.id


class FakeItem:
    def __init__(self, name, price, item_type=FOOD, groups=(), includes=(), meals=(LUNCH,)):
        self.id = uuid.uuid4()
        self.name = name
        self.item_type_id = item_type.id
        self.base_price_minor = price
        self.calories = None
        self.currency = "USD"
        self.is_available = True
        self.tax_exempt = False
        self.deleted_at = None
        self.modifier_links = [FakeGroupLink(g) for g in groups]
        # What the item comes with. Empty unless a test says otherwise.
        self.included_links = [FakeIncluded(o) for o in includes]
        self.meal_links = [FakeMealLink(m) for m in meals]


class FakeChoice:
    def __init__(self, item):
        self.item_id = item.id
        self.item = item


class FakeSlot:
    def __init__(self, item_type, items):
        self.id = uuid.uuid4()
        self.item_type = item_type
        self.item_type_id = item_type.id
        self.choices = [FakeChoice(i) for i in items]


class FakeCombo:
    def __init__(self, name, slots, kind="NONE", value=0, available=True, meal_id=LUNCH):
        self.id = uuid.uuid4()
        self.meal_id = meal_id
        self.name = name
        self.slots = slots
        self.discount_kind = kind
        self.discount_value = value
        self.is_available = available
        self.deleted_at = None


class FakeRestaurant:
    def __init__(self):
        self.id = uuid.uuid4()
        self.currency = "USD"
        self.tax_mode = "FLAT"
        self.tax_rate_bps = 0  # tax has its own tests; keep the sums readable


class FakeSession:
    """Answers the two reads price_cart makes: the items, then the combos.
    `combo` is one combo or a list of them. Every read is counted."""

    def __init__(self, items, combo):
        self._items = items
        self._combos = combo if isinstance(combo, list) else [combo]
        self._pending = None
        self.reads = []

    def execute(self, statement):
        model = statement.column_descriptions[0]["entity"].__name__
        self.reads.append(model)
        self._pending = self._items if model == "Item" else self._combos
        return self

    def scalars(self):
        return self

    def all(self):
        return self._pending

    def first(self):
        return self._pending[0] if self._pending else None


@pytest.fixture
def meal_deal():
    """A burger, a drink and a side, sold together for 10% off."""
    burger = FakeItem("Smash Burger", 1000)
    tea = FakeItem("Iced Tea", 300, DRINKS)
    fries = FakeItem("Fries", 400, SIDES)
    combo = FakeCombo(
        "Burger Meal",
        [
            FakeSlot(FOOD, [burger]),
            FakeSlot(DRINKS, [tea]),
            FakeSlot(SIDES, [fries]),
        ],
        kind="PERCENT",
        value=1000,
    )
    return burger, tea, fries, combo


def _cart(meal_deal, **overrides):
    from app.services.pricing import price_cart

    burger, tea, fries, combo = meal_deal
    raw = {
        "combo_id": str(combo.id),
        "quantity": 1,
        "selections": [
            {"slot_id": str(combo.slots[0].id), "menu_item_id": str(burger.id)},
            {"slot_id": str(combo.slots[1].id), "menu_item_id": str(tea.id)},
            {"slot_id": str(combo.slots[2].id), "menu_item_id": str(fries.id)},
        ],
    }
    raw.update(overrides)
    session = FakeSession([burger, tea, fries], combo)
    return price_cart(session, FakeRestaurant(), [], [raw])


def test_each_slot_is_priced_as_the_item_it_holds(meal_deal):
    """The subtotal is still what the food costs. Burying the saving in the
    line prices would leave a receipt that cannot be checked against a menu."""
    cart = _cart(meal_deal)
    assert cart.subtotal_minor == 1700
    assert [l.unit_price_minor for l in cart.lines] == [1000, 300, 400]


def test_the_saving_comes_off_the_cart(meal_deal):
    cart = _cart(meal_deal)
    assert cart.discount_minor == 170
    assert cart.total_minor == 1530


def test_every_line_of_a_combo_is_marked_as_belonging_to_it(meal_deal):
    """The kitchen has to plate a meal deal as one thing, not as a burger and
    an unrelated drink."""
    cart = _cart(meal_deal)
    _b, _t, _f, combo = meal_deal
    assert {l.combo_id for l in cart.lines} == {combo.id}
    assert {l.combo_name for l in cart.lines} == {"Burger Meal"}
    assert {l.combo_group for l in cart.lines} == {1}


def test_ordering_two_of_a_combo_doubles_both_the_food_and_the_saving(meal_deal):
    cart = _cart(meal_deal, quantity=2)
    assert cart.subtotal_minor == 3400
    assert cart.discount_minor == 340


def test_the_saving_is_rounded_once_per_combo_not_once_per_order():
    """Ten combos at a rounded 12.6p is not the same as 126p of ten combos,
    and the receipt shows the per-combo figure."""
    burger = FakeItem("Odd Burger", 1005)
    combo = FakeCombo("Odd Deal", [FakeSlot(FOOD, [burger])], "PERCENT", 1250)
    from app.services.pricing import price_cart

    cart = price_cart(
        FakeSession([burger], combo),
        FakeRestaurant(),
        [],
        [{
            "combo_id": str(combo.id),
            "quantity": 10,
            "selections": [
                {"slot_id": str(combo.slots[0].id), "menu_item_id": str(burger.id)}
            ],
        }],
    )
    assert cart.discount_minor == 1260


def test_a_modifier_is_priced_before_the_saving_is_taken():
    """Extra bacon is part of what the meal costs, so the discount applies to
    it. Taking the saving off the base price alone would make a customer pay
    full price for the extras inside a deal."""
    bacon = FakeOption("Extra bacon", 200)
    extras = FakeGroup("Extras", [bacon])
    burger = FakeItem("Smash Burger", 1000, groups=[extras])
    combo = FakeCombo("Burger Deal", [FakeSlot(FOOD, [burger])], "PERCENT", 1000)
    from app.services.pricing import price_cart

    cart = price_cart(
        FakeSession([burger], combo),
        FakeRestaurant(),
        [],
        [{
            "combo_id": str(combo.id),
            "quantity": 1,
            "selections": [{
                "slot_id": str(combo.slots[0].id),
                "menu_item_id": str(burger.id),
                "modifiers": [{"option_id": str(bacon.id), "quantity": 1}],
            }],
        }],
    )
    assert cart.subtotal_minor == 1200
    assert cart.discount_minor == 120


def test_a_missing_slot_is_refused(meal_deal):
    """An incomplete meal deal sold at meal-deal price."""
    burger, tea, _fries, combo = meal_deal
    with pytest.raises(errors.ApiError) as caught:
        _cart(meal_deal, selections=[
            {"slot_id": str(combo.slots[0].id), "menu_item_id": str(burger.id)},
            {"slot_id": str(combo.slots[1].id), "menu_item_id": str(tea.id)},
        ])
    # Named in the restaurant's own word, not a hard-coded one, and with no
    # article in front of it: a type may be called Tiffins.
    detail = str(caught.value.detail)
    assert "Sides" in detail, detail
    assert "a Sides" not in detail and "a sides" not in detail, detail


def test_an_item_that_is_not_one_of_the_choices_is_refused(meal_deal):
    """Otherwise the discount is a way to buy anything on the menu cheaply."""
    _b, tea, _f, combo = meal_deal
    steak = FakeItem("Ribeye", 4000)
    with pytest.raises(errors.ApiError):
        from app.services.pricing import price_cart

        price_cart(
            FakeSession([steak, tea], combo),
            FakeRestaurant(),
            [],
            [{
                "combo_id": str(combo.id),
                "quantity": 1,
                "selections": [
                    {"slot_id": str(combo.slots[0].id), "menu_item_id": str(steak.id)}
                ],
            }],
        )


def test_two_choices_for_one_slot_are_refused(meal_deal):
    burger, tea, fries, combo = meal_deal
    with pytest.raises(errors.ApiError):
        _cart(meal_deal, selections=[
            {"slot_id": str(combo.slots[0].id), "menu_item_id": str(burger.id)},
            {"slot_id": str(combo.slots[0].id), "menu_item_id": str(burger.id)},
            {"slot_id": str(combo.slots[1].id), "menu_item_id": str(tea.id)},
            {"slot_id": str(combo.slots[2].id), "menu_item_id": str(fries.id)},
        ])


def test_a_sold_out_choice_is_refused(meal_deal):
    burger, _t, _f, _c = meal_deal
    burger.is_available = False
    with pytest.raises(errors.ApiError) as caught:
        _cart(meal_deal)
    assert "sold out" in str(caught.value.detail).lower()


def test_every_combo_in_the_cart_is_read_in_one_query(meal_deal):
    """Three meal deals -- two of them the same deal -- used to be three reads
    of the combos table, on the quote and again at checkout. Now the items
    are one read and the combos are one, however many deals there are."""
    from app.services.pricing import price_cart

    burger, tea, fries, meal = meal_deal
    solo = FakeCombo("Burger Solo", [FakeSlot(FOOD, [burger])], "AMOUNT", 100)

    def deal(combo, *items):
        return {
            "combo_id": str(combo.id),
            "quantity": 1,
            "selections": [
                {"slot_id": str(slot.id), "menu_item_id": str(item.id)}
                for slot, item in zip(combo.slots, items)
            ],
        }

    session = FakeSession([burger, tea, fries], [meal, solo])
    cart = price_cart(
        session, FakeRestaurant(), [],
        [deal(meal, burger, tea, fries), deal(solo, burger), deal(meal, burger, tea, fries)],
    )

    assert session.reads == ["Item", "Combo"]
    assert [l.combo_group for l in cart.lines] == [1, 1, 1, 2, 3, 3, 3]
    assert cart.discount_minor == 170 + 100 + 170


def test_a_withdrawn_combo_cannot_be_ordered(meal_deal):
    _b, _t, _f, combo = meal_deal
    combo.is_available = False
    with pytest.raises(errors.ApiError):
        _cart(meal_deal)


def test_a_required_modifier_is_still_required_inside_a_combo():
    """A combo does not relax the rules the restaurant set on an item."""
    light = FakeOption("Light")
    ice = FakeGroup("Ice level", [light], required=True, selection="SINGLE")
    tea = FakeItem("Iced Tea", 300, DRINKS, groups=[ice])
    combo = FakeCombo("Drink Deal", [FakeSlot(DRINKS, [tea])])
    from app.services.pricing import price_cart

    with pytest.raises(errors.ApiError) as caught:
        price_cart(
            FakeSession([tea], combo),
            FakeRestaurant(),
            [],
            [{
                "combo_id": str(combo.id),
                "quantity": 1,
                "selections": [
                    {"slot_id": str(combo.slots[0].id), "menu_item_id": str(tea.id)}
                ],
            }],
        )
    assert "Ice level" in str(caught.value.detail)


def test_a_cart_of_nothing_but_a_combo_is_a_real_cart(meal_deal):
    """Not every order has a loose item in it."""
    assert _cart(meal_deal).total_minor > 0


def test_a_combo_quantity_below_one_is_refused(meal_deal):
    with pytest.raises(errors.ApiError):
        _cart(meal_deal, quantity=0)


# --- what the combo's meal period serves -----------------------------------
#
# A choice is only sold while the combo's own period serves the item. Taking
# fries off Lunch used to leave them in the Lunch combo, still sold at a
# discount.


def test_a_choice_taken_off_the_combos_period_is_refused(meal_deal):
    burger, tea, fries, combo = meal_deal
    fries.meal_links = [FakeMealLink(DINNER)]  # moved to dinner only

    with pytest.raises(errors.ApiError) as caught:
        _cart(meal_deal)
    assert caught.value.code == "ITEM_UNAVAILABLE"
    assert "not one of the choices for Burger Meal" in caught.value.detail["message"]


def test_the_choice_comes_back_when_the_item_is_served_again(meal_deal):
    burger, tea, fries, combo = meal_deal
    fries.meal_links = [FakeMealLink(DINNER), FakeMealLink(LUNCH)]
    assert _cart(meal_deal).subtotal_minor == 1700


def test_a_combo_whose_period_was_deleted_sells_nothing(meal_deal):
    """Deleting a period deletes its item links, so nothing is served there."""
    burger, tea, fries, combo = meal_deal
    for item in (burger, tea, fries):
        item.meal_links = []

    with pytest.raises(errors.ApiError) as caught:
        _cart(meal_deal)
    assert caught.value.code == "ITEM_UNAVAILABLE"


def test_an_item_on_no_meal_period_is_not_sold_on_its_own():
    """The storefront hides it, but a cart outlives a menu edit and the API can
    be called directly."""
    from app.services.pricing import price_cart

    retired = FakeItem("Seasonal Pie", 600, meals=())
    line = {"menu_item_id": str(retired.id), "quantity": 1, "modifiers": []}

    with pytest.raises(errors.ApiError) as caught:
        price_cart(FakeSession([retired], None), FakeRestaurant(), [line], [])
    assert caught.value.code == "ITEM_UNAVAILABLE"
    assert caught.value.detail["message"] == "Seasonal Pie is no longer on the menu."
