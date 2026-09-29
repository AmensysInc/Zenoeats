"""Creating an order writes its lines together, not one round trip each.

The lines used to be flushed one at a time, only to learn each line's id for
its modifiers: a ten-line order was ten round trips, all made while the
restaurant's order counter is locked and every other checkout there waits.
The modifiers now hang off their line through the relationship, and the whole
order goes in with one flush.
"""

import pytest
from sqlalchemy import event, text

from tests.test_admin_restaurants import (  # noqa: F401  (fixtures)
    _create,
    _email,
    _owner,
    _set_own_password,
    _staff_client,
    admin_user,
    cleanup,
)
from tests.test_order_delivery_fee import _customer, kitchen  # noqa: F401  (fixture)
from tests.test_staff_removal import team  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration


def _cart(item_id, lines=4, modifiers_each=2):
    from app.services.pricing import PricedCart, PricedLine, PricedModifier

    priced = [
        PricedLine(
            menu_item_id=item_id, name=f"Line {n}", unit_price_minor=500, quantity=1,
            line_total_minor=500,
            modifiers=[
                PricedModifier(option_id=None, group_name="Extras", option_name=f"Extra {n}.{m}",
                               unit_price_delta_minor=0, quantity=1)
                for m in range(modifiers_each)
            ],
        )
        for n in range(lines)
    ]
    total = 500 * lines
    return PricedCart(currency="USD", subtotal_minor=total, discount_minor=0, tax_minor=0,
                      total_minor=total, lines=priced)


def test_an_orders_lines_and_modifiers_go_in_together(kitchen):
    from app.db.session import tenant_session
    from app.models import Restaurant
    from app.services.orders import create_pending_order

    customer_id = _customer()
    inserts = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO"):
            inserts.append(statement.split()[2])

    with tenant_session(kitchen.id) as session:
        restaurant = session.get(Restaurant, kitchen.id)
        engine = session.get_bind()
        event.listen(engine, "before_cursor_execute", record)
        try:
            order = create_pending_order(
                session, restaurant=restaurant, customer_user_id=customer_id,
                cart=_cart(kitchen.item_id), customer_note=None,
            )
        finally:
            event.remove(engine, "before_cursor_execute", record)
        order_id = order.id

    # Four lines, eight modifiers: one statement each, not one per line.
    assert inserts.count("order_items") == 1
    assert inserts.count("order_item_modifiers") == 1

    with tenant_session(kitchen.id) as session:
        rows = session.execute(
            text(
                "SELECT i.name_snapshot, m.option_name_snapshot "
                "FROM order_items i JOIN order_item_modifiers m ON m.order_item_id = i.id "
                "WHERE i.order_id = :o ORDER BY 1, 2"
            ),
            {"o": order_id},
        ).all()

    # Every modifier on its own line, none lost and none crossed over.
    assert [tuple(r) for r in rows] == [
        (f"Line {n}", f"Extra {n}.{m}") for n in range(4) for m in range(2)
    ]
