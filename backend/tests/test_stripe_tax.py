"""Sales tax through Stripe Tax.

Stripe itself is replaced by recorders at its three call sites -- calculate,
record the sale, reverse on refund -- and everything around them is real:
discount allocation, caching, the webhook handlers, the database state that
makes each step happen exactly once, and the admin gates.
"""

import uuid
from types import SimpleNamespace

import pytest
import stripe
from sqlalchemy import text

from app.core import errors
from app.db.base import utcnow
from app.services import stripe_tax
from app.services.tax import TaxLine, TaxService, allocate_discount


# ------------------------------------------------------------ allocation ---

@pytest.mark.parametrize(
    "amounts, discount",
    [
        ([1000, 300, 400], 170),
        ([333, 333, 334], 100),
        ([1, 1, 1], 2),
        ([999], 1000),       # never below zero
        ([500, 0, 500], 1),  # a free line takes none of it
        ([], 50),
    ],
)
def test_the_discount_is_spread_exactly(amounts, discount):
    allocated = allocate_discount(amounts, discount)
    assert len(allocated) == len(amounts)
    assert all(a >= 0 for a in allocated)
    assert sum(allocated) == max(sum(amounts) - discount, 0)
    for original, after in zip(amounts, allocated):
        assert after <= original


def test_no_discount_changes_nothing():
    assert allocate_discount([1000, 250], 0) == [1000, 250]


# ----------------------------------------------------------- calculation ---

def _restaurant(**overrides):
    values = dict(
        id=uuid.uuid4(), currency="USD", tax_mode="STRIPE_TAX", tax_rate_bps=0,
        tax_code="txcd_40060003", address_line1="920 5th Ave", address_line2=None,
        address_city="Seattle", address_state="WA", address_postal_code="98104",
        address_country="US",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def stripe_calculations(monkeypatch):
    """Records calculation requests; answers 10.25% tax on the line amounts."""
    calls = []
    cache = {}

    def create(**kwargs):
        calls.append(kwargs)
        base = sum(item["amount"] for item in kwargs["line_items"])
        tax = (base * 1025 + 5000) // 10000
        return SimpleNamespace(
            id=f"taxcalc_{len(calls)}", amount_total=base + tax, tax_amount_exclusive=tax
        )

    monkeypatch.setattr(stripe.tax.Calculation, "create", create)
    monkeypatch.setattr(stripe_tax, "_cache_get", cache.get)
    monkeypatch.setattr(stripe_tax, "_cache_set", cache.__setitem__)
    monkeypatch.setattr("app.core.ratelimit._consume", lambda *a, **k: None)
    return calls


def test_a_calculation_is_made_on_the_restaurants_account_at_the_pickup_address(stripe_calculations):
    result = stripe_tax.calculate(
        _restaurant(), "acct_rest",
        [TaxLine(amount_minor=1000, quantity=2), TaxLine(amount_minor=400, quantity=1)],
    )
    assert result.tax_minor == 144  # 10.25% of 14.00, half up
    assert result.calculation_id == "taxcalc_1"

    request = stripe_calculations[0]
    assert request["stripe_account"] == "acct_rest"
    assert request["currency"] == "usd"
    assert request["customer_details"]["address"]["postal_code"] == "98104"
    assert request["customer_details"]["address_source"] == "shipping"
    assert [i["amount"] for i in request["line_items"]] == [1000, 400]
    assert [i["quantity"] for i in request["line_items"]] == [2, 1]
    assert {i["tax_code"] for i in request["line_items"]} == {"txcd_40060003"}
    assert {i["tax_behavior"] for i in request["line_items"]} == {"exclusive"}
    assert len({i["reference"] for i in request["line_items"]}) == 2


def test_the_same_cart_reuses_its_calculation(stripe_calculations):
    """Stripe bills per calculation, and a quote and the order placed from it
    must land on the same numbers."""
    lines = [TaxLine(amount_minor=1250, quantity=1)]
    first = stripe_tax.calculate(_restaurant(id="r1"), "acct_rest", lines)
    second = stripe_tax.calculate(_restaurant(id="r1"), "acct_rest", lines)
    assert first == second
    assert len(stripe_calculations) == 1

    stripe_tax.calculate(_restaurant(id="r1"), "acct_rest", [TaxLine(amount_minor=1300, quantity=1)])
    assert len(stripe_calculations) == 2


def test_free_lines_are_left_out_and_a_free_cart_costs_no_call(stripe_calculations):
    stripe_tax.calculate(
        _restaurant(), "acct_rest",
        [TaxLine(amount_minor=0, quantity=1), TaxLine(amount_minor=500, quantity=1)],
    )
    assert [i["amount"] for i in stripe_calculations[0]["line_items"]] == [500]

    result = stripe_tax.calculate(_restaurant(), "acct_rest", [TaxLine(amount_minor=0, quantity=3)])
    assert result.tax_minor == 0 and result.calculation_id is None
    assert len(stripe_calculations) == 1


def test_stripe_failing_refuses_rather_than_charging_without_tax(monkeypatch, stripe_calculations):
    def fail(**kwargs):
        raise stripe.InvalidRequestError("location", "customer_details[address]", code="customer_tax_location_invalid")

    monkeypatch.setattr(stripe.tax.Calculation, "create", fail)
    with pytest.raises(errors.ApiError) as caught:
        stripe_tax.calculate(_restaurant(), "acct_rest", [TaxLine(amount_minor=900, quantity=1)])
    assert caught.value.status_code == 503
    assert caught.value.code == "TAX_UNAVAILABLE"


def test_a_total_that_does_not_add_up_is_refused(monkeypatch, stripe_calculations):
    monkeypatch.setattr(
        stripe.tax.Calculation, "create",
        lambda **kw: SimpleNamespace(id="taxcalc_x", amount_total=99999, tax_amount_exclusive=10),
    )
    with pytest.raises(errors.ApiError) as caught:
        stripe_tax.calculate(_restaurant(), "acct_rest", [TaxLine(amount_minor=900, quantity=1)])
    assert caught.value.code == "TAX_UNAVAILABLE"


def test_a_restaurant_without_a_full_address_cannot_calculate(stripe_calculations):
    with pytest.raises(errors.ApiError):
        stripe_tax.calculate(
            _restaurant(address_postal_code=None), "acct_rest", [TaxLine(amount_minor=900, quantity=1)]
        )
    assert stripe_calculations == []


def test_flat_rate_restaurants_never_call_stripe(stripe_calculations):
    result = TaxService.calculate(
        None, _restaurant(tax_mode="FLAT", tax_rate_bps=825),
        [TaxLine(amount_minor=1000, quantity=1), TaxLine(amount_minor=300, quantity=1)],
        discount_minor=100,
    )
    assert result.tax_minor == 99  # 8.25% of 12.00, half up
    assert result.calculation_id is None
    assert stripe_calculations == []


def test_a_flat_rate_leaves_tax_exempt_lines_out(stripe_calculations):
    """A (exempt) and B (taxed): only B is taxed."""
    result = TaxService.calculate(
        None, _restaurant(tax_mode="FLAT", tax_rate_bps=1000),
        [TaxLine(amount_minor=500, quantity=1, taxable=False), TaxLine(amount_minor=1000, quantity=1)],
        discount_minor=0,
    )
    assert result.tax_minor == 100  # 10% of B alone


def test_a_flat_rate_shares_the_discount_with_exempt_lines():
    """The saving is spread over both lines, so the taxed line keeps only its
    own share of it: 1000 of 1500 takes 200 of a 300 discount."""
    result = TaxService.calculate(
        None, _restaurant(tax_mode="FLAT", tax_rate_bps=1000),
        [TaxLine(amount_minor=500, quantity=1, taxable=False), TaxLine(amount_minor=1000, quantity=1)],
        discount_minor=300,
    )
    assert result.tax_minor == 80  # 10% of 800


def test_an_all_exempt_cart_is_not_taxed():
    result = TaxService.calculate(
        None, _restaurant(tax_mode="FLAT", tax_rate_bps=1000),
        [TaxLine(amount_minor=500, quantity=2, taxable=False)],
        discount_minor=0,
    )
    assert result.tax_minor == 0


def test_stripe_tax_sends_exempt_lines_as_nontaxable(stripe_calculations):
    stripe_tax.calculate(
        _restaurant(), "acct_rest",
        [TaxLine(amount_minor=500, quantity=1, taxable=False), TaxLine(amount_minor=1000, quantity=1)],
    )
    codes = [i["tax_code"] for i in stripe_calculations[0]["line_items"]]
    assert codes == [stripe_tax.NONTAXABLE_TAX_CODE, "txcd_40060003"]


def test_stripe_tax_lines_carry_the_discount(stripe_calculations):
    class Session:
        def execute(self, _):
            return SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(stripe_account_id="acct_rest"))

    TaxService.calculate(
        Session(), _restaurant(),
        [TaxLine(amount_minor=1000, quantity=1), TaxLine(amount_minor=300, quantity=1)],
        discount_minor=130,
    )
    assert sum(i["amount"] for i in stripe_calculations[0]["line_items"]) == 1170


def test_tax_settings_readiness(monkeypatch):
    monkeypatch.setattr(stripe.tax.Settings, "retrieve", lambda **kw: SimpleNamespace(status="active"))
    assert stripe_tax.settings_problems("acct_rest") == []

    monkeypatch.setattr(
        stripe.tax.Settings, "retrieve",
        lambda **kw: SimpleNamespace(
            status="pending",
            status_details=SimpleNamespace(pending=SimpleNamespace(missing_fields=["head_office"])),
        ),
    )
    problems = stripe_tax.settings_problems("acct_rest")
    assert "Stripe tax settings are pending" in problems
    assert "missing in Stripe: head office" in problems


# ---------------------------------------------- after payment, in the DB ---

integration = pytest.mark.integration


@pytest.fixture
def paid_path(monkeypatch):
    """A Stripe Tax restaurant with a connected account, an order that came
    from a calculation, and a payment for it. Stripe's transaction calls are
    recorded instead of made."""
    from app.api.v1.admin import _PURGE_ORDER
    from app.db.session import system_session, tenant_session
    from app.models import (
        Order, OrderStatus, Payment, PaymentStatus, Restaurant, RestaurantPaymentAccount,
        RestaurantStatus, User, UserKind,
    )

    suffix = uuid.uuid4().hex[:8]
    with system_session() as session:
        restaurant = Restaurant(
            slug=f"tax-{suffix}", name="Tax Test", status=RestaurantStatus.ACTIVE.value,
            timezone="UTC", currency="USD", tax_mode="STRIPE_TAX",
            address_line1="920 5th Ave", address_city="Seattle", address_state="WA",
            address_postal_code="98104", address_country="US",
        )
        customer = User(kind=UserKind.CUSTOMER.value,
                        email=f"tax-{suffix}@zenoeats.invalid")
        session.add_all([restaurant, customer])
        session.flush()
        rid, uid = restaurant.id, customer.id

    account_id = f"acct_tax_{suffix}"
    intent_id = f"pi_tax_{suffix}"
    with tenant_session(rid) as session:
        session.add(RestaurantPaymentAccount(restaurant_id=rid, stripe_account_id=account_id,
                                             charges_enabled=True))
        order = Order(
            restaurant_id=rid, order_number=9101, customer_user_id=uid,
            fulfillment_type="PICKUP", status=OrderStatus.PENDING_PAYMENT.value,
            payment_method="STRIPE", currency="USD", subtotal_minor=2000, discount_minor=0,
            tax_minor=205, total_minor=2205, tax_calculation_id="taxcalc_order",
            expires_at=utcnow(),
        )
        session.add(order)
        session.flush()
        session.add(Payment(restaurant_id=rid, order_id=order.id, status=PaymentStatus.PROCESSING.value,
                            amount_minor=2205, currency="USD", stripe_account_id=account_id,
                            stripe_payment_intent_id=intent_id))
        oid = order.id

    calls = {"transactions": [], "reversals": []}

    def create_from_calculation(**kwargs):
        calls["transactions"].append(kwargs)
        return SimpleNamespace(id="tax_txn_1")

    def create_reversal(**kwargs):
        calls["reversals"].append(kwargs)
        return SimpleNamespace(id=f"tax_rev_{len(calls['reversals'])}")

    monkeypatch.setattr(stripe.tax.Transaction, "create_from_calculation", create_from_calculation)
    monkeypatch.setattr(stripe.tax.Transaction, "create_reversal", create_reversal)

    yield SimpleNamespace(restaurant_id=rid, order_id=oid, account_id=account_id,
                          intent_id=intent_id, calls=calls)

    with tenant_session(rid) as session:
        for table in _PURGE_ORDER:
            session.execute(text(f"DELETE FROM {table} WHERE restaurant_id = :r"), {"r": rid})
        session.execute(text("DELETE FROM restaurants WHERE id = :r"), {"r": rid})


def _order(path):
    from app.db.session import tenant_session
    from app.models import Order

    with tenant_session(path.restaurant_id) as session:
        order = session.get(Order, path.order_id)
        session.expunge(order)
        return order


def _succeeded(path):
    return {"data": {"object": {"id": path.intent_id}}}


@integration
def test_a_paid_order_records_its_tax_exactly_once(paid_path):
    from app.workers.tasks import _handle_intent_succeeded

    _handle_intent_succeeded(_succeeded(paid_path), paid_path.account_id)
    order = _order(paid_path)
    assert order.status == "PREPARING"
    assert order.tax_transaction_id == "tax_txn_1"

    [call] = paid_path.calls["transactions"]
    assert call["calculation"] == "taxcalc_order"
    assert call["stripe_account"] == paid_path.account_id
    assert call["reference"] == f"order_{paid_path.order_id}"

    _handle_intent_succeeded(_succeeded(paid_path), paid_path.account_id)  # redelivery
    assert len(paid_path.calls["transactions"]) == 1


@integration
def test_a_retry_records_tax_that_failed_the_first_time(paid_path, monkeypatch):
    """The order is marked paid before Stripe Tax is called. If that call
    fails, the retry finds the order already paid -- and must still record
    the tax, not return early."""
    from app.workers.tasks import _handle_intent_succeeded

    def down(**kwargs):
        raise stripe.APIConnectionError("stripe unreachable")

    original = stripe.tax.Transaction.create_from_calculation
    monkeypatch.setattr(stripe.tax.Transaction, "create_from_calculation", down)
    with pytest.raises(stripe.APIConnectionError):
        _handle_intent_succeeded(_succeeded(paid_path), paid_path.account_id)
    assert _order(paid_path).status == "PREPARING"
    assert _order(paid_path).tax_transaction_id is None

    monkeypatch.setattr(stripe.tax.Transaction, "create_from_calculation", original)
    _handle_intent_succeeded(_succeeded(paid_path), paid_path.account_id)
    assert _order(paid_path).tax_transaction_id == "tax_txn_1"


@integration
def test_refunds_reverse_the_tax_once_each_and_never_twice(paid_path):
    from app.workers.tasks import _handle_charge_refunded, _handle_intent_succeeded

    _handle_intent_succeeded(_succeeded(paid_path), paid_path.account_id)

    def refunded(amount):
        return {"data": {"object": {"payment_intent": paid_path.intent_id,
                                    "amount_refunded": amount, "amount": 2205}}}

    _handle_charge_refunded(refunded(1000), paid_path.account_id)
    _handle_charge_refunded(refunded(1000), paid_path.account_id)  # redelivery
    [partial] = paid_path.calls["reversals"]
    assert partial["mode"] == "partial" and partial["flat_amount"] == -1000
    assert partial["original_transaction"] == "tax_txn_1"
    assert _order(paid_path).tax_reversed_amount_minor == 1000

    _handle_charge_refunded(refunded(2205), paid_path.account_id)
    assert len(paid_path.calls["reversals"]) == 2
    remainder = paid_path.calls["reversals"][1]
    assert remainder["mode"] == "partial" and remainder["flat_amount"] == -1205
    assert _order(paid_path).tax_reversed_amount_minor == 2205


@integration
def test_a_full_refund_straight_away_is_a_full_reversal(paid_path):
    from app.workers.tasks import _handle_charge_refunded, _handle_intent_succeeded

    _handle_intent_succeeded(_succeeded(paid_path), paid_path.account_id)
    _handle_charge_refunded(
        {"data": {"object": {"payment_intent": paid_path.intent_id,
                             "amount_refunded": 2205, "amount": 2205}}},
        paid_path.account_id,
    )
    [reversal] = paid_path.calls["reversals"]
    assert reversal["mode"] == "full"
    assert "flat_amount" not in reversal


# ------------------------------------------------------------ admin gates ---

@pytest.fixture
def admin_user():
    from app.db.session import system_session
    from app.models import User, UserKind

    email = "tax-tests@zenoeats.invalid"
    with system_session() as session:
        user = session.execute(
            text("SELECT id FROM users WHERE email = :e"), {"e": email}
        ).first()
        if user is None:
            row = User(kind=UserKind.PLATFORM_ADMIN.value, email=email, full_name="Tax Tests",
                       is_platform_admin=True)
            session.add(row)
            session.flush()
            return SimpleNamespace(id=row.id)
        return SimpleNamespace(id=user.id)


@integration
def test_switching_to_stripe_tax_needs_an_address_and_active_settings(paid_path, admin_user, monkeypatch):
    from app.api.v1.admin import update_restaurant
    from app.db.session import tenant_session
    from app.models import Restaurant
    from app.schemas.api import UpdateRestaurantIn

    rid = paid_path.restaurant_id
    with tenant_session(rid) as session:
        session.get(Restaurant, rid).tax_mode = "FLAT"

    monkeypatch.setattr(stripe_tax, "settings_problems", lambda acct: ["Stripe tax settings are pending"])
    with pytest.raises(errors.ApiError) as caught:
        update_restaurant(rid, UpdateRestaurantIn(tax_mode="STRIPE_TAX"), admin=admin_user)
    assert caught.value.code == "STRIPE_TAX_NOT_READY"
    assert "pending" in caught.value.detail["message"]

    with pytest.raises(errors.ApiError) as caught:
        update_restaurant(
            rid, UpdateRestaurantIn(tax_mode="STRIPE_TAX", address_postal_code=None), admin=admin_user
        )
    assert "postal code" in caught.value.detail["message"]

    monkeypatch.setattr(stripe_tax, "settings_problems", lambda acct: [])
    out = update_restaurant(
        rid, UpdateRestaurantIn(tax_mode="STRIPE_TAX", address_country="us"), admin=admin_user
    )
    assert out.tax_mode == "STRIPE_TAX"
    assert out.address_country == "US"
    assert out.tax_code == "txcd_40060003"
