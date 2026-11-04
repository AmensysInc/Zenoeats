"""The admin assistant, end to end.

What is worth testing is not that it understands English -- that is
test_assistant_language.py -- but the three promises the endpoints make:

  a question answers and changes nothing,
  a change is described and waits for a person, and
  the assistant can do exactly what the admin driving it could do by
  clicking, and nothing else.

The third is the one that matters. An assistant with its own authority
would be a way around every gate on the screens it replaces.
"""

import pytest
from sqlalchemy import text

from tests.test_admin_restaurants import (  # noqa: F401  (fixtures)
    _create, _email, _owner, _set_own_password, _staff_client, admin_user, cleanup,
)
from tests.test_staff_removal import team  # noqa: F401  (fixture)

ASK = "/api/v1/restaurant/assistant/ask"
DO = "/api/v1/restaurant/assistant/do"


@pytest.fixture(autouse=True)
def no_rate_limits(monkeypatch):
    from app.core import ratelimit

    monkeypatch.setattr(ratelimit, "_consume", lambda *a, **k: None)


@pytest.fixture
def menu(team):
    """A heading and two dishes to talk about."""
    from app.db.session import tenant_session

    with tenant_session(team.id) as session:
        heading = session.execute(
            text(
                "INSERT INTO item_types (id, restaurant_id, name, sort_order, "
                "created_at, updated_at) VALUES (gen_random_uuid(), :r, 'Burgers', "
                "1, now(), now()) RETURNING id"
            ),
            {"r": team.id},
        ).scalar_one()
        for name, price in (("Jr's French Fries", 250), ("Jr's Cheeseburger", 650)):
            session.execute(
                text(
                    "INSERT INTO menu_items (id, restaurant_id, item_type_id, name, "
                    "base_price_minor, currency, is_available, sort_order, "
                    "created_at, updated_at) VALUES (gen_random_uuid(), :r, :t, :n, "
                    ":p, 'USD', true, 0, now(), now())"
                ),
                {"r": team.id, "t": heading, "n": name, "p": price},
            )
    team.heading_id = heading
    return team


def _price_of(restaurant_id, name) -> int:
    from app.db.session import tenant_session

    with tenant_session(restaurant_id) as session:
        return session.execute(
            text("SELECT base_price_minor FROM menu_items WHERE name = :n"), {"n": name}
        ).scalar_one()


# ------------------------------------------------- a question changes nothing --


def test_a_question_is_answered_on_the_spot(menu):
    answered = menu.owner.post(ASK, json={"text": "show me the menu"})
    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert "Jr's French Fries" in body["text"]
    # A read needs no confirming, so there is no button.
    assert body["proposal"] is None


def test_checking_inventory_names_what_is_off(menu):
    from app.db.session import tenant_session

    with tenant_session(menu.id) as session:
        session.execute(
            text("UPDATE menu_items SET is_available = false WHERE name = :n"),
            {"n": "Jr's French Fries"},
        )
    body = menu.owner.post(ASK, json={"text": "check inventory"}).json()
    assert "Jr's French Fries" in body["text"]


def test_an_unknown_sentence_says_so_and_offers_what_it_knows(menu):
    body = menu.owner.post(ASK, json={"text": "burn the place down"}).json()
    assert body["understood"] is False
    assert body["proposal"] is None
    # And says what it can do, rather than only what it cannot.
    assert "change the price" in body["text"]


# -------------------------------------------- a change waits for a person ------


def test_a_price_change_is_proposed_not_performed(menu):
    body = menu.owner.post(
        ASK, json={"text": "change the price of fries to 3.25"}
    ).json()

    # It resolved the nickname to the real dish, which is where a wrong
    # match gets caught before it costs anything.
    assert "Jr's French Fries" in body["text"]
    assert "USD 2.50" in body["text"] and "USD 3.25" in body["text"]
    assert body["proposal"]["intent"] == "SET_PRICE"
    # Nothing happened yet.
    assert _price_of(menu.id, "Jr's French Fries") == 250


def test_confirming_performs_it(menu):
    proposal = menu.owner.post(
        ASK, json={"text": "change the price of fries to 3.25"}
    ).json()["proposal"]

    done = menu.owner.post(DO, json={"proposal": proposal})
    assert done.status_code == 200, done.text
    assert _price_of(menu.id, "Jr's French Fries") == 325


def test_adding_a_dish_is_proposed_with_the_heading_resolved(menu):
    body = menu.owner.post(
        ASK, json={"text": "add a Double Bacon Burger to burgers for 8.50"}
    ).json()
    assert body["proposal"]["intent"] == "ADD_ITEM"
    assert body["proposal"]["heading_name"] == "Burgers"

    menu.owner.post(DO, json={"proposal": body["proposal"]})
    assert _price_of(menu.id, "Double Bacon Burger") == 850


def test_a_dish_with_no_price_is_asked_about_rather_than_guessed(menu):
    """A model asked to add a burger "for about a fiver" may fill in 5.00,
    4.99 or 5.50, and all three look confident on a menu people pay from."""
    body = menu.owner.post(ASK, json={"text": "add a milkshake"}).json()
    assert body["proposal"] is None
    assert "cost" in body["text"].lower()


def test_an_ambiguous_name_asks_which_rather_than_choosing(menu):
    body = menu.owner.post(ASK, json={"text": "we're out of Jr's"}).json()
    assert body["proposal"] is None
    assert len(body["options"]) == 2


def test_a_dish_that_does_not_exist_is_not_invented(menu):
    body = menu.owner.post(ASK, json={"text": "we're out of lobster thermidor"}).json()
    assert body["proposal"] is None
    assert "could not find" in body["text"].lower()


def test_taking_something_off_the_menu_round_trips(menu):
    from app.db.session import tenant_session

    proposal = menu.owner.post(ASK, json={"text": "86 the fries"}).json()["proposal"]
    menu.owner.post(DO, json={"proposal": proposal})
    with tenant_session(menu.id) as session:
        assert session.execute(
            text("SELECT is_available FROM menu_items WHERE name = :n"),
            {"n": "Jr's French Fries"},
        ).scalar_one() is False


# ----------------------------------------- it can do no more than the admin ----


def test_a_cook_cannot_drive_the_assistant(menu):
    """It runs the menu screens, so it is gated like them. An assistant with
    its own authority would be a way around every gate on the screens it
    replaces."""
    cook_id, _ = menu.member("COOK")
    cook = menu.client(cook_id)
    assert cook.post(ASK, json={"text": "show me the menu"}).status_code == 403
    assert cook.post(DO, json={"proposal": {}}).status_code == 403


def test_a_proposal_it_never_made_is_refused(menu):
    """The confirm endpoint does not trust what arrives: a hand-written
    proposal naming an intent outside the four writes is not performed."""
    forged = {"intent": "DELETE_EVERYTHING", "item_id": "x"}
    assert menu.owner.post(DO, json={"proposal": forged}).status_code == 422


def test_a_proposal_for_a_dish_that_has_since_gone_is_refused(menu):
    """Resolved ids are re-read before anything changes, so a menu edited
    between the proposal and the button does not silently hit the wrong
    row."""
    from app.db.session import tenant_session

    proposal = menu.owner.post(
        ASK, json={"text": "change the price of fries to 3.25"}
    ).json()["proposal"]
    with tenant_session(menu.id) as session:
        session.execute(
            text("UPDATE menu_items SET deleted_at = now() WHERE name = :n"),
            {"n": "Jr's French Fries"},
        )

    refused = menu.owner.post(DO, json={"proposal": proposal})
    assert refused.status_code == 422
    assert "no longer" in refused.json()["detail"]["message"]


def test_one_restaurant_cannot_see_another_s_menu(menu, admin_user, cleanup):
    """Row-level security, the same as everywhere else: the assistant runs on
    the staff session and sees exactly what it sees."""
    other = _create(admin_user, cleanup)
    other_email = _email()
    other_owner, _ = _owner(admin_user, other.id, other_email)
    _set_own_password(other_email, "owner password 123")
    theirs = _staff_client(other.slug)

    from app.core import staff_auth

    theirs.cookies.set(
        staff_auth.SESSION_COOKIE, staff_auth.issue_session(other_owner.user_id)
    )
    body = theirs.post(ASK, json={"text": "show me the menu"}).json()
    assert "Jr's French Fries" not in body["text"]


# ------------------------------------------- things that cannot be undone -----
#
# Cancellations and deletions. Each is marked destructive the whole way
# through, says what will be lost before the button, and re-checks the state
# at the moment of doing it rather than only when it was proposed.


def _order_on(restaurant_id, number=1024, status="AUTO_ACCEPTED", total=1850):
    """One order to talk about, in a given state."""
    from app.db.session import system_session, tenant_session

    with system_session() as session:
        user_id = session.execute(
            text(
                "INSERT INTO users (id, kind, email, is_platform_admin, is_active, "
                "must_change_password, created_at, updated_at) VALUES "
                "(gen_random_uuid(), 'GUEST', :e, false, true, false, now(), now()) "
                "RETURNING id"
            ),
            {"e": f"guest-{number}-{restaurant_id}@t.invalid"},
        ).scalar_one()
    with tenant_session(restaurant_id) as session:
        return session.execute(
            text(
                "INSERT INTO orders (id, restaurant_id, customer_user_id, order_number, "
                "status, fulfillment_type, payment_method, currency, subtotal_minor, "
                "tax_minor, total_minor, created_at, updated_at) VALUES "
                "(gen_random_uuid(), :r, :u, :n, :s, 'PICKUP', 'CARD', 'USD', :t, 0, "
                ":t, now(), now()) RETURNING id"
            ),
            {"r": restaurant_id, "u": user_id, "n": number, "s": status, "t": total},
        ).scalar_one()


def test_cancelling_names_the_order_back_before_doing_it(menu):
    """A mistyped number is the obvious way to cancel somebody else's
    dinner, so the proposal says which order and what it cost."""
    _order_on(menu.id, number=1024, total=1850)
    body = menu.owner.post(ASK, json={"text": "cancel order 1024"}).json()

    assert body["destructive"] is True
    assert "#1024" in body["text"] and "USD 18.50" in body["text"]
    assert "cannot be undone" in body["text"]
    assert body["proposal"]["intent"] == "CANCEL_ORDER"


def test_cancelling_performs_through_the_state_machine(menu):
    from app.db.session import tenant_session

    _order_on(menu.id, number=1025)
    proposal = menu.owner.post(ASK, json={"text": "cancel order 1025"}).json()["proposal"]
    done = menu.owner.post(DO, json={"proposal": proposal})
    assert done.status_code == 200, done.text

    with tenant_session(menu.id) as session:
        assert session.execute(
            text("SELECT status FROM orders WHERE order_number = 1025")
        ).scalar_one() == "CANCELLED"


def test_an_unpaid_order_is_refused_the_way_the_orders_screen_refuses_it(menu):
    _order_on(menu.id, number=1026, status="PENDING_PAYMENT")
    body = menu.owner.post(ASK, json={"text": "cancel order 1026"}).json()
    assert body["proposal"] is None
    assert "not been paid" in body["text"]


def test_an_order_that_does_not_exist_is_not_invented(menu):
    body = menu.owner.post(ASK, json={"text": "cancel order 9999"}).json()
    assert body["proposal"] is None
    assert "no order #9999" in body["text"].lower()


def test_an_order_cancelled_in_between_is_refused_at_the_button(menu):
    """State is re-checked when it happens, not only when it was proposed."""
    from app.db.session import tenant_session

    _order_on(menu.id, number=1027)
    proposal = menu.owner.post(ASK, json={"text": "cancel order 1027"}).json()["proposal"]
    with tenant_session(menu.id) as session:
        session.execute(
            text("UPDATE orders SET status = 'CANCELLED' WHERE order_number = 1027")
        )

    refused = menu.owner.post(DO, json={"proposal": proposal})
    assert refused.status_code == 422
    assert "already cancelled" in refused.json()["detail"]["message"].lower()


def test_deleting_a_dish_says_what_goes_with_it(menu):
    body = menu.owner.post(ASK, json={"text": "delete the cheeseburger"}).json()
    assert body["destructive"] is True
    assert "Jr's Cheeseburger" in body["text"]
    assert "cannot be undone" in body["text"]

    menu.owner.post(DO, json={"proposal": body["proposal"]})
    from app.db.session import tenant_session

    with tenant_session(menu.id) as session:
        # Soft, so orders that included it still read correctly.
        assert session.execute(
            text("SELECT deleted_at IS NOT NULL FROM menu_items WHERE name = :n"),
            {"n": "Jr's Cheeseburger"},
        ).scalar_one() is True


def test_a_heading_with_dishes_under_it_is_refused(menu):
    """The same answer the Menu screen gives, and the same reason: deleting
    it would take real dishes with it or orphan them."""
    body = menu.owner.post(ASK, json={"text": "delete the Burgers heading"}).json()
    assert body["proposal"] is None
    assert "still has 2 dishes" in body["text"]


def test_an_empty_heading_can_go(menu):
    import uuid as _uuid

    from app.db.session import tenant_session

    # Unique per run: item type names are unique per restaurant, and a
    # rerun that collided would look like a feature failure.
    empty = f"Sides{_uuid.uuid4().hex[:6]}"

    with tenant_session(menu.id) as session:
        session.execute(
            text(
                "INSERT INTO item_types (id, restaurant_id, name, sort_order, "
                "created_at, updated_at) VALUES (gen_random_uuid(), :r, :n, "
                "9, now(), now())"
            ),
            {"r": menu.id, "n": empty},
        )
    body = menu.owner.post(ASK, json={"text": f"delete the {empty} heading"}).json()
    assert body["destructive"] is True
    assert body["proposal"]["intent"] == "DELETE_HEADING"

    menu.owner.post(DO, json={"proposal": body["proposal"]})
    with tenant_session(menu.id) as session:
        assert session.execute(
            text("SELECT deleted_at IS NOT NULL FROM item_types WHERE name = :n"),
            {"n": empty},
        ).scalar_one() is True


@pytest.mark.parametrize(
    "typed",
    ["delete everything", "delete all the items", "cancel all orders", "make everything free"],
)
def test_nothing_happens_in_bulk(menu, typed):
    """Refused before the sentence is read at all: narrowing "delete
    everything" down to whichever single row matched would be worse than
    refusing."""
    body = menu.owner.post(ASK, json={"text": typed}).json()
    assert body["proposal"] is None
    assert "one thing at a time" in body["text"]


def test_a_refund_is_still_not_something_it_does(menu):
    """Money going back to a customer stays on the order, where the amount
    is in front of whoever sends it."""
    body = menu.owner.post(ASK, json={"text": "refund order 1024"}).json()
    assert body["proposal"] is None
    assert "refunds are on the order" in body["text"].lower()
