"""Order confirmation and staff invitation emails.

The Resend HTTP call is replaced by a recorder; composition, the send-once
bookkeeping, and the triggers (payment webhook, invite endpoint) are real.
"""

import uuid
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import text

from app.config import settings
from app.db.base import utcnow
from app.services import email, notifications


# ------------------------------------------------------------ composition ---

def _order(**overrides):
    item = SimpleNamespace(
        quantity=2, name_snapshot="<script>alert(1)</script> Burger", line_total_minor=2400,
        modifiers=[SimpleNamespace(option_name_snapshot="Extra & cheese")],
    )
    values = dict(
        id=uuid.uuid4(), order_number=1042, currency="USD", items=[item],
        subtotal_minor=2400, discount_minor=240, tax_minor=178, total_minor=2338,
        fulfillment_type="PICKUP", delivery_fee_minor=0,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_the_confirmation_escapes_what_restaurants_typed(monkeypatch):
    monkeypatch.setattr(settings, "STOREFRONT_URL_TEMPLATE", "https://{slug}.{root_domain}")
    monkeypatch.setattr(settings, "ROOT_DOMAIN", "zenoeats.com")
    order = _order()
    subject, body_html, body_text = notifications.compose_order_confirmation(
        restaurant_name="Tom & Jerry's", slug="tomjerry", order=order, customer_name="Sam"
    )

    assert subject == "Order #1042 confirmed at Tom & Jerry's"
    assert "<script>" not in body_html
    assert "&lt;script&gt;" in body_html
    assert "Tom &amp; Jerry&#39;s" in body_html
    assert f"https://tomjerry.zenoeats.com/orders/{order.id}" in body_html
    assert f"https://tomjerry.zenoeats.com/orders/{order.id}" in body_text
    assert "$23.38" in body_html and "-$2.40" in body_html


def test_the_confirmation_never_contains_the_pickup_pin():
    """The PIN releases the food at the counter; it stays behind sign-in."""
    order = _order(pickup_pin_encrypted="gAAAA-encrypted", pickup_pin="123456")
    _, body_html, body_text = notifications.compose_order_confirmation(
        restaurant_name="Spice House", slug="spicehouse", order=order, customer_name=None
    )
    for body in (body_html, body_text):
        assert "123456" not in body
        assert "gAAAA" not in body
        assert "pickup PIN is on your order page" in body


def test_a_guest_link_keeps_its_token_out_of_every_server_log(monkeypatch):
    """The token opens the order and its PIN for days. After "#", a browser
    never sends it anywhere, so no access log or Referer on the way sees it."""
    from urllib.parse import urlsplit

    from app.core import guest_auth

    monkeypatch.setattr(settings, "STOREFRONT_URL_TEMPLATE", "https://{slug}.{root_domain}")
    monkeypatch.setattr(settings, "ROOT_DOMAIN", "zenoeats.com")
    order = _order()
    _, body_html, body_text = notifications.compose_order_confirmation(
        restaurant_name="Spice House", slug="spicehouse", order=order, customer_name=None,
        for_guest=True,
    )
    link = next(w for w in body_text.split() if w.startswith("https://spicehouse."))
    parts = urlsplit(link)

    assert parts.query == "", "a token in the query string is written to access logs"
    assert parts.fragment.startswith("t=")
    assert guest_auth.order_token_grants(parts.fragment[2:], order.id)


def test_a_delivery_confirmation_promises_no_pin_and_shows_its_fee():
    """A delivery is not collected at a counter, and its fee is part of the total."""
    order = _order(
        fulfillment_type="DELIVERY", delivery_fee_minor=399, discount_minor=0,
        tax_minor=178, total_minor=2977,
    )
    _, body_html, body_text = notifications.compose_order_confirmation(
        restaurant_name="Spice House", slug="spicehouse", order=order, customer_name=None
    )
    for body in (body_html, body_text):
        assert "PIN" not in body
        assert "Delivery fee" in body
        assert "$3.99" in body
        assert "where your driver is" in body


def test_the_invitation_carries_the_temporary_password_it_issued():
    subject, body_html, body_text = notifications.compose_staff_invitation(
        restaurant_name="Spice House", slug="spicehouse", role_code="KITCHEN",
        has_temporary_password=True, temporary_password="AB12-CD34-EF56",
    )
    assert subject == "You're invited to join Spice House on Zenoeats"
    assert "kitchen staff" in body_text
    assert "/manage/login" in body_html
    assert "Temporary password: AB12-CD34-EF56" in body_text
    assert "AB12-CD34-EF56" in body_html
    # And says it will not last, so nobody treats it as their password.
    assert "stops working" in body_text


def test_a_password_in_the_invitation_is_escaped():
    """Generated passwords contain nothing HTML means, but the template
    should not depend on that staying true."""
    _, body_html, _ = notifications.compose_staff_invitation(
        restaurant_name="Spice House", slug="spicehouse", role_code="KITCHEN",
        has_temporary_password=True, temporary_password="<b>x</b>",
    )
    assert "<b>x</b>" not in body_html
    assert "&lt;b&gt;x&lt;/b&gt;" in body_html


@pytest.mark.parametrize(
    "has_temporary_password, expected",
    [
        # A login still to be set up, but no password to hand -- an owner
        # invitation, or a task queued before invitations carried one.
        (True, "manager will give you a temporary password"),
        # Someone who already works at another Zenoeats restaurant.
        (False, "password you already use"),
    ],
)
def test_an_invitation_with_no_password_to_give_says_where_it_comes_from(
    has_temporary_password, expected
):
    _, body_html, body_text = notifications.compose_staff_invitation(
        restaurant_name="Spice House", slug="spicehouse", role_code="KITCHEN",
        has_temporary_password=has_temporary_password,
    )
    assert expected in body_text
    assert "Temporary password:" not in body_text


# ------------------------------------------------------------- transport ---

@pytest.fixture
def resend(monkeypatch):
    calls = []
    answer = {"status": 200, "error": None}

    def post(url, json, headers, timeout):
        calls.append({"url": url, "json": json, "headers": headers})
        if answer["error"]:
            raise answer["error"]
        return httpx.Response(answer["status"], text='{"id":"email_1"}')

    monkeypatch.setattr(email.httpx, "post", post)
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(settings, "EMAIL_FROM", "Zenoeats <orders@zenoeats.com>")
    return SimpleNamespace(calls=calls, answer=answer)


def _message():
    return email.Email(to="sam@example.com", subject="Hi", html="<p>Hi</p>", text="Hi",
                       idempotency_key="order-confirmation/abc")


def test_nothing_is_sent_without_a_key(resend, monkeypatch):
    monkeypatch.setattr(settings, "RESEND_API_KEY", "")
    assert email.send(_message()) is False
    assert resend.calls == []


def test_a_message_is_sent_with_its_idempotency_key(resend):
    assert email.send(_message()) is True
    [call] = resend.calls
    assert call["url"] == "https://api.resend.com/emails"
    assert call["headers"]["Authorization"] == "Bearer re_test_key"
    assert call["headers"]["Idempotency-Key"] == "order-confirmation/abc"
    assert call["json"]["to"] == ["sam@example.com"]
    assert call["json"]["from"] == "Zenoeats <orders@zenoeats.com>"


@pytest.mark.parametrize("status", [429, 500, 503])
def test_rate_limits_and_outages_are_retried(resend, status):
    resend.answer["status"] = status
    with pytest.raises(email.RetryableEmailError):
        email.send(_message())


def test_an_unreachable_provider_is_retried(resend):
    resend.answer["error"] = httpx.ConnectTimeout("timed out")
    with pytest.raises(email.RetryableEmailError):
        email.send(_message())


def test_a_rejected_message_is_not_retried(resend):
    """A 4xx -- an unverified sender domain, a bad address -- will not fix
    itself, so it is logged rather than retried six times."""
    resend.answer["status"] = 422
    assert email.send(_message()) is False


# ------------------------------------------------------ once per paid order ---

integration = pytest.mark.integration


@pytest.fixture
def paid_order():
    from app.api.v1.admin import _PURGE_ORDER
    from app.db.session import system_session, tenant_session
    from app.models import Order, OrderStatus, Restaurant, RestaurantStatus, User, UserKind

    suffix = uuid.uuid4().hex[:8]
    with system_session() as session:
        restaurant = Restaurant(slug=f"mail-{suffix}", name="Mail Test",
                                status=RestaurantStatus.ACTIVE.value, timezone="UTC", currency="USD")
        customer = User(kind=UserKind.CUSTOMER.value, clerk_user_id=f"user_mail_{suffix}",
                        email=f"mail-{suffix}@zenoeats.invalid", full_name="Sam Customer")
        session.add_all([restaurant, customer])
        session.flush()
        rid, uid, customer_email = restaurant.id, customer.id, customer.email

    with tenant_session(rid) as session:
        order = Order(
            restaurant_id=rid, order_number=9201, customer_user_id=uid, fulfillment_type="PICKUP",
            status=OrderStatus.PREPARING.value, payment_method="STRIPE", currency="USD",
            subtotal_minor=1000, discount_minor=0, tax_minor=80, total_minor=1080,
            paid_at=utcnow(),
        )
        session.add(order)
        session.flush()
        oid = order.id

    yield SimpleNamespace(restaurant_id=rid, order_id=oid, customer_id=uid, email=customer_email)

    with tenant_session(rid) as session:
        for table in _PURGE_ORDER:
            session.execute(text(f"DELETE FROM {table} WHERE restaurant_id = :r"), {"r": rid})
        session.execute(text("DELETE FROM restaurants WHERE id = :r"), {"r": rid})


@integration
def test_a_paid_order_is_confirmed_once(paid_order, resend):
    assert notifications.send_order_confirmation(paid_order.restaurant_id, paid_order.order_id)
    assert notifications.send_order_confirmation(paid_order.restaurant_id, paid_order.order_id) is False
    [call] = resend.calls
    assert call["json"]["to"] == [paid_order.email]
    assert call["headers"]["Idempotency-Key"] == f"order-confirmation/{paid_order.order_id}"
    assert "Hi Sam," in call["json"]["text"]


@integration
def test_a_failed_send_is_not_marked_as_sent(paid_order, resend):
    resend.answer["status"] = 503
    with pytest.raises(email.RetryableEmailError):
        notifications.send_order_confirmation(paid_order.restaurant_id, paid_order.order_id)
    resend.answer["status"] = 200
    assert notifications.send_order_confirmation(paid_order.restaurant_id, paid_order.order_id)


@integration
def test_no_confirmation_goes_to_a_placeholder_address(paid_order, resend):
    from app.db.session import system_session
    from app.models import User

    with system_session() as session:
        session.get(User, paid_order.customer_id).email = f"{uuid.uuid4().hex}@pending.local"
    assert notifications.send_order_confirmation(paid_order.restaurant_id, paid_order.order_id) is False
    assert resend.calls == []


@integration
def test_the_payment_webhook_queues_the_confirmation(queued_emails, monkeypatch):
    """Queued from the webhook, sent by its own task: a slow email provider
    must never hold up or fail the payment."""
    from app.workers import tasks

    order_id, restaurant_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(tasks, "_resolve_tenant_for_intent", lambda *a: (restaurant_id, uuid.uuid4()))

    class FakeTenantSession:
        def __enter__(self):
            return SimpleNamespace(get=lambda model, _id, **kw: SimpleNamespace(
                order_id=order_id, succeeded_at=utcnow()))

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(tasks, "tenant_session", lambda rid: FakeTenantSession())
    monkeypatch.setattr(tasks.stripe_tax, "record_transaction", lambda *a: None)

    tasks._handle_intent_succeeded({"data": {"object": {"id": "pi_x"}}}, "acct_x")
    assert ("send_order_confirmation", (str(restaurant_id), str(order_id))) in queued_emails
