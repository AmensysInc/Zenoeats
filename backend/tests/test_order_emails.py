"""The emails that follow an order: ready, on its way, delivered, cancelled,
refunded.

Composition is checked on its own; the rest runs against the database with
SendGrid replaced by a recorder -- which endpoint queues which email, and
that each is sent once however many times it is asked for.
"""

import uuid
from types import SimpleNamespace

import pytest

from app.services import email, order_emails
from tests.test_admin_restaurants import (  # noqa: F401  (fixtures)
    _create,
    _email,
    _owner,
    _set_own_password,
    _staff_client,
    admin_user,
    cleanup,
)
from tests.test_deliveries import _assign, shop  # noqa: F401
from tests.test_notifications import sendgrid  # noqa: F401

REFUND_SENTENCE = (
    "Your order #1042 has been cancelled, and a refund of $21.92 will be credited to "
    "your original payment method within 10 to 14 business days."
)


def _compose(kind, **kw):
    order = SimpleNamespace(
        id=uuid.uuid4(), order_number=1042, currency="USD", fulfillment_type="PICKUP",
    )
    values = dict(restaurant_name="Spice House", slug="spicehouse", order=order,
                  customer_name="Sam", for_guest=False)
    values.update(kw)
    return order_emails.compose(kind, **values)


# ------------------------------------------------------------ composition ---

def test_a_refunded_cancellation_says_how_much_and_when():
    subject, body_html, body_text = _compose("order_cancelled", refund=2192)
    assert subject == "Your order #1042 from Spice House has been cancelled"
    assert REFUND_SENTENCE in body_text
    assert REFUND_SENTENCE in body_html


def test_a_cancellation_without_a_refund_promises_none():
    """A no-show cancelled without its money back must not be told it is
    coming."""
    _, body_html, body_text = _compose("order_cancelled")
    for body in (body_html, body_text):
        assert "refund of" not in body
        assert "business days" not in body
        assert "contact Spice House" in body


def test_a_later_refund_says_this_refunds_amount():
    subject, _, body_text = _compose("refund_issued", refund=450)
    assert subject == "Your refund from Spice House is on its way"
    assert "Spice House has refunded $4.50 for your order #1042" in body_text
    assert "10 to 14 business days" in body_text


def test_ready_to_collect_points_at_the_pin_without_containing_it():
    _, body_html, body_text = _compose("order_ready", pickup_address="1 Main St, Dallas")
    for body in (body_html, body_text):
        assert "ready at Spice House" in body
        assert "1 Main St, Dallas" in body
        assert "/orders/" in body
    assert "Collect from" in body_html


def test_on_the_way_names_the_driver_when_there_is_one():
    _, _, named = _compose("order_on_the_way", driver_name="Dana")
    _, _, unnamed = _compose("order_on_the_way")
    assert named.split("\n")[2].startswith("Dana has picked up your order #1042")
    assert "Your driver has picked up" in unnamed


def test_delivered_offers_to_order_again(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "STOREFRONT_URL_TEMPLATE", "https://{slug}.{root_domain}")
    monkeypatch.setattr(settings, "ROOT_DOMAIN", "zenoeats.com")
    _, body_html, body_text = _compose("order_delivered")
    assert "has been delivered. Enjoy your meal." in body_text
    assert "Order again: https://spicehouse.zenoeats.com/" in body_text
    assert 'href="https://spicehouse.zenoeats.com/"' in body_html


def test_a_guest_link_carries_its_token_after_the_hash():
    from app.core import guest_auth

    order_id = uuid.uuid4()
    _, _, body_text = _compose(
        "order_on_the_way", for_guest=True,
        order=SimpleNamespace(id=order_id, order_number=1, currency="USD",
                              fulfillment_type="DELIVERY"),
    )
    link = next(w for w in body_text.split() if "/orders/" in w)
    assert "?" not in link
    assert guest_auth.order_token_grants(link.split("#t=")[1], order_id)


# ------------------------------------------- which step queues which email ---

integration = pytest.mark.integration


def _queued(queued_emails, order_id):
    return [args[0] for name, args in queued_emails
            if name == "send_order_email" and args[2] == order_id]


@integration
def test_a_collection_is_told_it_is_ready(shop, queued_emails):
    order_id = shop.order()
    assert shop.kitchen.post(f"/api/v1/restaurant/orders/{order_id}/ready").status_code == 200
    assert _queued(queued_emails, order_id) == ["order_ready"]


@integration
def test_a_delivery_is_told_when_it_sets_off_and_arrives(shop, queued_emails):
    """Not when it is ready: that is ready for the driver, not the customer."""
    order_id = shop.order()
    assert _assign(shop.manager, order_id, shop.driver_membership).status_code == 200
    shop.kitchen.post(f"/api/v1/restaurant/orders/{order_id}/ready")
    shop.driver.post(f"/api/v1/restaurant/orders/{order_id}/picked-up")
    shop.driver.post(f"/api/v1/restaurant/orders/{order_id}/delivered")
    assert _queued(queued_emails, order_id) == ["order_on_the_way", "order_delivered"]


@pytest.fixture
def stripe_refunds(monkeypatch):
    """Stripe accepting every refund, stubbed at our own service."""
    from app.api.v1 import restaurant as api

    answer = {"status": "succeeded", "amount": 1500, "id": "re_test"}
    monkeypatch.setattr(api.stripe_service, "refund_order", lambda payment, reason=None: answer)
    return answer


@integration
def test_a_cancellation_and_a_later_refund_are_each_queued(shop, queued_emails, stripe_refunds):
    order_id = shop.order()
    shop.manager.post(f"/api/v1/restaurant/orders/{order_id}/cancel",
                      json={"reason": "Never collected", "refund": False})
    shop.manager.post(f"/api/v1/restaurant/orders/{order_id}/refund",
                      json={"reason": "Agreed on the phone"})

    sent = [(name, args) for name, args in queued_emails if name == "send_order_email"]
    assert [args[0] for _, args in sent] == ["order_cancelled", "refund_issued"]
    assert sent[1][1][3] == {"refunded_before": 0}


def _stripe_reports_refund(shop, monkeypatch, order_id, amount_refunded):
    """Run the charge.refunded webhook for this order's payment."""
    from sqlalchemy import text

    from app.db.session import tenant_session
    from app.workers import tasks

    with tenant_session(shop.id) as session:
        payment_id = session.execute(
            text("SELECT id FROM payments WHERE order_id = :o"), {"o": order_id}
        ).scalar_one()
    monkeypatch.setattr(tasks, "_resolve_tenant_for_intent", lambda *a: (shop.id, payment_id))
    monkeypatch.setattr(tasks.stripe_tax, "record_refund", lambda *a: None)
    tasks._handle_charge_refunded(
        {"data": {"object": {"payment_intent": "pi_test", "amount": 1500,
                             "amount_refunded": amount_refunded}}},
        "acct_test",
    )


@integration
def test_a_refund_from_stripes_dashboard_is_queued_once(shop, queued_emails, monkeypatch):
    order_id = shop.order(status="COMPLETED")
    _stripe_reports_refund(shop, monkeypatch, order_id, 400)
    _stripe_reports_refund(shop, monkeypatch, order_id, 400)  # redelivered

    sent = [args for name, args in queued_emails if name == "send_order_email"]
    assert sent == [("refund_issued", str(shop.id), order_id,
                     {"refunded_before": 0, "refunded_after": 400})]


@integration
def test_a_pending_refund_from_the_board_completing_is_not_news(shop, queued_emails, monkeypatch):
    """The customer was told the whole amount was coming when it was issued."""
    from sqlalchemy import text

    from app.db.session import tenant_session

    order_id = shop.order()
    with tenant_session(shop.id) as session:
        session.execute(text("UPDATE payments SET status = 'REFUND_PENDING' WHERE order_id = :o"),
                        {"o": order_id})
    _stripe_reports_refund(shop, monkeypatch, order_id, 1500)
    assert _queued(queued_emails, order_id) == []


# ------------------------------------------------------------ sent once ---

def _sent_to(sendgrid):
    return [call["json"]["subject"] for call in sendgrid.calls]


@integration
def test_each_email_is_sent_once_however_often_it_is_asked_for(shop, sendgrid):
    order_id = uuid.UUID(shop.order())
    assert order_emails.send("order_ready", shop.id, order_id) is True
    assert order_emails.send("order_ready", shop.id, order_id) is False
    assert len(sendgrid.calls) == 1


@integration
def test_a_failure_worth_retrying_leaves_the_email_to_be_sent(shop, sendgrid):
    order_id = uuid.UUID(shop.order())
    sendgrid.answer["status"] = 503
    with pytest.raises(email.RetryableEmailError):
        order_emails.send("order_delivered", shop.id, order_id)
    sendgrid.answer["status"] = 202
    assert order_emails.send("order_delivered", shop.id, order_id) is True


@integration
def test_a_refund_the_cancellation_mentioned_is_not_sent_again(shop, sendgrid, stripe_refunds):
    """Stripe reports the board's refund back a moment later. The customer
    was already told, in the cancellation."""
    order_id = shop.order()
    shop.manager.post(f"/api/v1/restaurant/orders/{order_id}/cancel",
                      json={"reason": "Kitchen closed early"})

    assert order_emails.send("order_cancelled", shop.id, uuid.UUID(order_id)) is True
    [cancelled] = sendgrid.calls
    assert "a refund of $15.00 will be credited" in cancelled["json"]["content"][0]["value"]

    # What the webhook would queue for the same refund.
    assert order_emails.send(
        "refund_issued", shop.id, uuid.UUID(order_id),
        {"refunded_before": 0, "refunded_after": 1500},
    ) is False
    assert len(sendgrid.calls) == 1


@integration
def test_refunds_from_the_dashboard_are_each_told_once(shop, sendgrid):
    """Two partial refunds are two emails, each for its own amount; the
    webhook delivered twice is still two."""
    order_id = uuid.UUID(shop.order(status="COMPLETED"))
    first = {"refunded_before": 0, "refunded_after": 500}
    second = {"refunded_before": 500, "refunded_after": 800}
    assert order_emails.send("refund_issued", shop.id, order_id, first)
    assert order_emails.send("refund_issued", shop.id, order_id, second)
    assert not order_emails.send("refund_issued", shop.id, order_id, second)

    bodies = [call["json"]["content"][0]["value"] for call in sendgrid.calls]
    assert len(bodies) == 2
    assert "has refunded $5.00" in bodies[0]
    assert "has refunded $3.00" in bodies[1]


@integration
def test_no_email_goes_to_a_placeholder_address(shop, sendgrid):
    from app.db.session import system_session, tenant_session
    from app.models import Order, User

    order_id = uuid.UUID(shop.order())
    with tenant_session(shop.id) as session:
        customer_id = session.get(Order, order_id).customer_user_id
    with system_session() as session:
        session.get(User, customer_id).email = f"{uuid.uuid4().hex}@pending.local"
    assert order_emails.send("order_ready", shop.id, order_id) is False
    assert sendgrid.calls == []
