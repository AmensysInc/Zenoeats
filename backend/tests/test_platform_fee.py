"""Zenoeats' application fee on each order.

Money arithmetic in minor units, so every case is exact: no float, no
rounding that drifts a cent per thousand orders.
"""

from types import SimpleNamespace

import pytest

from app.config import settings
from app.services import stripe_service


@pytest.fixture
def fee(monkeypatch):
    def configure(bps: int, fixed: int = 0):
        monkeypatch.setattr(settings, "PLATFORM_FEE_BPS", bps)
        monkeypatch.setattr(settings, "PLATFORM_FEE_FIXED_MINOR", fixed)
        return stripe_service.platform_fee_minor

    return configure


def test_no_fee_by_default(fee):
    assert fee(0, 0)(2599) == 0


@pytest.mark.parametrize(
    "bps, fixed, total, expected",
    [
        (250, 0, 1000, 25),      # 2.5% of $10.00
        (250, 30, 1000, 55),     # 2.5% + $0.30
        (250, 0, 1019, 25),      # 25.475 rounds half up -> 25
        (250, 0, 1020, 26),      # 25.5 rounds half up -> 26
        (0, 30, 1000, 30),       # fixed only
        (1000, 50, 40, 40),      # never more than the order itself
        (250, 30, 0, 0),         # nothing to take from
    ],
)
def test_fee_arithmetic(fee, bps, fixed, total, expected):
    assert fee(bps, fixed)(total) == expected


def test_negative_settings_are_treated_as_no_fee(fee):
    assert fee(-100, -30)(1000) == 0


def _order(total_minor: int):
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001", total_minor=total_minor, currency="USD",
        restaurant_id="r", order_number=1001,
    )


def _account():
    return SimpleNamespace(charges_enabled=True, stripe_account_id="acct_test")


def test_a_fee_is_sent_to_stripe_only_when_there_is_one(fee, monkeypatch):
    calls = []
    monkeypatch.setattr(
        stripe_service.stripe.PaymentIntent, "create",
        lambda **kwargs: calls.append(kwargs) or SimpleNamespace(id="pi_1"),
    )

    fee(0, 0)
    stripe_service.create_payment_intent(_order(2000), _account())
    assert "application_fee_amount" not in calls[-1]

    fee(250, 30)
    stripe_service.create_payment_intent(_order(2000), _account())
    assert calls[-1]["application_fee_amount"] == 80
    assert calls[-1]["metadata"]["platform_fee_minor"] == "80"
    # A changed fee must not replay the old idempotency key.
    assert calls[-1]["idempotency_key"] != calls[-2]["idempotency_key"]


def test_stripe_is_never_asked_to_send_its_own_receipt(fee, monkeypatch):
    """In live mode a receipt_email makes Stripe email a receipt whatever the
    account's settings say, and another for each refund -- a second copy of
    what Zenoeats' own confirmation and refund emails already say."""
    calls = []
    monkeypatch.setattr(
        stripe_service.stripe.PaymentIntent, "create",
        lambda **kwargs: calls.append(kwargs) or SimpleNamespace(id="pi_1"),
    )
    fee(0, 0)
    stripe_service.create_payment_intent(_order(2000), _account())
    assert "receipt_email" not in calls[-1]
