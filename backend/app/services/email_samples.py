"""Every email, filled in with made-up data.

For looking at a template after editing it (scripts/preview_emails.py), and
for the test that renders every template, so a template that breaks -- a
misspelled variable, an unclosed tag -- fails there rather than in an inbox.

Each email gets at least one example, and one per case that reads
differently: a pick-up and a delivery confirmation, say.
"""

import uuid
from collections.abc import Callable
from types import SimpleNamespace

from app.services import notifications, order_emails


def _order(**overrides) -> SimpleNamespace:
    """An order shaped like the model, as far as the emails read it."""
    values = dict(
        id=uuid.UUID("00000000-0000-4000-8000-000000001042"),
        order_number=1042,
        currency="USD",
        fulfillment_type="PICKUP",
        items=[
            SimpleNamespace(
                quantity=2, name_snapshot="Mc. Chicken", line_total_minor=1800,
                modifiers=[SimpleNamespace(option_name_snapshot="Extra cheese"),
                           SimpleNamespace(option_name_snapshot="Chipotle mayo")],
            ),
            SimpleNamespace(quantity=1, name_snapshot="Mango lassi", line_total_minor=450,
                            modifiers=[]),
        ],
        subtotal_minor=2250, discount_minor=225, delivery_fee_minor=0,
        tax_minor=167, total_minor=2192,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _update(kind: str, *, order=None, **kw) -> tuple[str, str, str]:
    """One of the emails that follow an order, for Spice House's order 1042."""
    values = dict(
        restaurant_name="Spice House", slug="spicehouse", order=order or _order(),
        customer_name="Sam", for_guest=False,
    )
    values.update(kw)
    return order_emails.compose(kind, **values)


def samples() -> dict[str, Callable[[], tuple[str, str, str]]]:
    """name/case -> a function returning (subject, html, text)."""
    delivery = _order(fulfillment_type="DELIVERY", discount_minor=0, delivery_fee_minor=399,
                      total_minor=2816)
    return {
        "order_ready/with_address": lambda: _update(
            "order_ready", pickup_address="1200 Main St, Dallas, TX 75201, US",
        ),
        "order_ready/no_address": lambda: _update("order_ready", customer_name=None),
        "order_on_the_way/delivery": lambda: _update(
            "order_on_the_way", order=delivery, driver_name="Dana",
        ),
        "order_on_the_way/no_driver_name": lambda: _update("order_on_the_way"),
        "order_delivered/delivery": lambda: _update("order_delivered", order=delivery),
        "order_cancelled/refunded": lambda: _update("order_cancelled", refund=2192),
        "order_cancelled/no_refund": lambda: _update("order_cancelled"),
        "refund_issued/part": lambda: _update("refund_issued", refund=450),
        "order_confirmation/pickup": lambda: notifications.compose_order_confirmation(
            restaurant_name="Spice House", slug="spicehouse", order=_order(),
            customer_name="Sam",
        ),
        "order_confirmation/delivery": lambda: notifications.compose_order_confirmation(
            restaurant_name="Spice House", slug="spicehouse",
            order=_order(fulfillment_type="DELIVERY", discount_minor=0, delivery_fee_minor=399,
                         total_minor=2816),
            customer_name=None,
        ),
        "staff_invitation/new_login": lambda: notifications.compose_staff_invitation(
            restaurant_name="Spice House", slug="spicehouse", role_code="KITCHEN",
            has_temporary_password=True, temporary_password="K7QM-4XWP-9DRT",
        ),
        "staff_invitation/password_from_manager": lambda: notifications.compose_staff_invitation(
            restaurant_name="Spice House", slug="spicehouse", role_code="MANAGER",
            has_temporary_password=True,
        ),
        "staff_invitation/existing_login": lambda: notifications.compose_staff_invitation(
            restaurant_name="Spice House", slug="spicehouse", role_code="DRIVER",
            has_temporary_password=False,
        ),
    }
