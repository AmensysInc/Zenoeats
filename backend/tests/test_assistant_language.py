"""Reading what an admin typed.

The parser either understands a sentence or says it does not, and the tests
below are mostly about the second half. An assistant that guesses is worse
than one that asks: these actions change prices customers pay.

No model, no key, no network -- which is also why this file can assert
exact answers rather than "something sensible came back".
"""

import pytest

from app.services.assistant.language import Intent, money_minor, read


# ------------------------------------------------------------ adding a dish ---


@pytest.mark.parametrize(
    "typed,name,heading,price",
    [
        ("add a cheeseburger to burgers for 5.99", "cheeseburger", "burgers", 599),
        ("add cheeseburger for 5.99", "cheeseburger", None, 599),
        ("Add a Double Bacon Burger under Burgers at $8.50",
         "Double Bacon Burger", "Burgers", 850),
        ("create a milkshake in Drinks costing 3", "milkshake", "Drinks", 300),
        ("put a veggie wrap to the menu", "veggie wrap", None, None),
    ],
)
def test_adding_a_dish_is_read_whole(typed, name, heading, price):
    found = read(typed)
    assert found is not None, typed
    assert found.intent is Intent.ADD_ITEM
    assert found.slots.get("name") == name
    assert found.slots.get("heading") == heading
    assert found.slots.get("price_minor") == price


def test_a_dish_with_no_price_is_still_understood():
    """So the answer can be "what should it cost?" rather than a guess.

    A model asked to add a burger "for about a fiver" may fill in 5.00, 4.99
    or 5.50, and all three look equally confident on a menu customers pay
    from."""
    found = read("add a milkshake")
    assert found.intent is Intent.ADD_ITEM
    assert "price_minor" not in found.slots


# -------------------------------------------------------------- the price -----


@pytest.mark.parametrize(
    "typed,name,price",
    [
        ("change the price of fries to 2.50", "fries", 250),
        ("set the price for Jr's Shake to 4", "Jr's Shake", 400),
        ("update fries to 3.00", "fries", 300),
        ("price of fries 2.25", "fries", 225),
    ],
)
def test_a_price_change_is_read_whole(typed, name, price):
    found = read(typed)
    assert found is not None, typed
    assert found.intent is Intent.SET_PRICE
    assert found.slots["name"] == name
    assert found.slots["price_minor"] == price


def test_a_price_change_with_no_number_is_not_a_price_change():
    """Rather than being read as one with a missing slot, which is how an
    assistant ends up setting something to zero."""
    assert read("change the price of fries") is None


# ----------------------------------------------------------- in and out -------


@pytest.mark.parametrize(
    "typed",
    [
        "we're out of fries",
        "we are out of fries",
        "sold out of fries",
        "mark fries unavailable",
        "set fries as out of stock",
        "86 the fries",
    ],
)
def test_running_out_is_said_several_ways(typed):
    found = read(typed)
    assert found is not None, typed
    assert found.intent is Intent.SET_UNAVAILABLE
    assert found.slots["name"] == "fries"


@pytest.mark.parametrize(
    "typed", ["mark fries available", "fries are back", "set fries back in stock"]
)
def test_coming_back_is_the_opposite(typed):
    found = read(typed)
    assert found is not None, typed
    assert found.intent is Intent.SET_AVAILABLE
    assert found.slots["name"] == "fries"


# ------------------------------------------------------------------ reads -----


@pytest.mark.parametrize(
    "typed,intent",
    [
        ("check inventory", Intent.SHOW_UNAVAILABLE),
        ("check the stock", Intent.SHOW_UNAVAILABLE),
        ("what's out of stock", Intent.SHOW_UNAVAILABLE),
        ("show me the menu", Intent.SHOW_MENU),
        ("list the menu", Intent.SHOW_MENU),
        ("how many orders today", Intent.SHOW_ORDERS),
        ("show orders", Intent.SHOW_ORDERS),
        ("list coupons", Intent.SHOW_COUPONS),
        ("help", Intent.HELP),
    ],
)
def test_the_questions_are_recognised(typed, intent):
    found = read(typed)
    assert found is not None, typed
    assert found.intent is intent


# ------------------------------------------------- what it refuses to guess ---


@pytest.mark.parametrize(
    "typed",
    [
        "",
        "   ",
        "burn the place down",
        "refund everyone",
        "why is the sky blue",
        "delete the entire menu",
    ],
)
def test_an_unknown_sentence_is_not_forced_into_an_intent(typed):
    """None is a real answer, and the caller says so plainly. Guessing at a
    sentence nobody wrote is how a price gets changed that nobody asked
    about."""
    assert read(typed) is None


def test_a_destructive_word_is_not_read_as_its_opposite():
    """"delete the burger" contains "the burger" and must not be read as
    adding one. Whole-sentence patterns rather than keyword spotting."""
    found = read("delete the burger")
    assert found is None or found.intent is not Intent.ADD_ITEM


# ------------------------------------------------------------------ money -----


@pytest.mark.parametrize(
    "typed,minor",
    [("5", 500), ("5.99", 599), ("$5.99", 599), ("£4.50", 450), ("0", 0), ("19.99", 1999)],
)
def test_money_is_read_exactly(typed, minor):
    assert money_minor(typed) == minor


def test_money_is_decimal_not_float():
    """19.99 as a float is 19.989999999999998, and a menu that rounded that
    down would undercharge for the rest of its life."""
    assert money_minor("19.99") == 1999
    assert money_minor("0.07") == 7


def test_what_is_not_money_is_not_money():
    assert money_minor("free") is None
    assert money_minor("-5") is None
