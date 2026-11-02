"""A restaurant's report is for its own days, and refunds come off.

The report used to be all-time only and counted a refunded order as full
revenue, tax included. It now takes a range of the restaurant's local dates,
counts each order on the day it was paid there, and nets refunds out of sales
and tax.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

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


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


@pytest.fixture
def books(admin_user, cleanup):
    """A Chicago restaurant (UTC-5 in September) and a way to record orders."""
    from app.db.session import system_session, tenant_session
    from app.models import Order, OrderItem, Payment

    restaurant = _create(admin_user, cleanup, timezone="America/Chicago")
    owner_email = _email()
    owner, _ = _owner(admin_user, restaurant.id, owner_email)
    _set_own_password(owner_email, "owner password 123")

    with system_session() as session:
        customer_id = session.execute(
            text(
                "INSERT INTO users (id, kind, email, is_platform_admin, "
                "is_active, must_change_password, created_at, updated_at) "
                "VALUES (gen_random_uuid(), 'CUSTOMER', :e, false, true, false, now(), now()) "
                "RETURNING id"
            ),
            {"e": f"books-{uuid.uuid4().hex[:8]}@zenoeats.invalid"},
        ).scalar_one()

    numbers = iter(range(7001, 8000))

    def order(paid_at, total=1500, tax=100, status="COMPLETED", refunded=0,
              item="Smash Burger", discount=0, created_at=None, driver_id=None):
        with tenant_session(restaurant.id) as session:
            row = Order(
                restaurant_id=restaurant.id, order_number=next(numbers),
                customer_user_id=customer_id, status=status, payment_method="STRIPE",
                currency="USD", subtotal_minor=total - tax + discount,
                discount_minor=discount, tax_minor=tax, total_minor=total,
                paid_at=paid_at,
                fulfillment_type="DELIVERY" if driver_id else "PICKUP",
                driver_user_id=driver_id,
                delivery_address="12 Oak Street" if driver_id else None,
            )
            session.add(row)
            session.flush()
            if created_at is not None:
                session.execute(
                    text("UPDATE orders SET created_at = :c WHERE id = :i"),
                    {"c": created_at, "i": row.id},
                )
            session.add(OrderItem(
                restaurant_id=restaurant.id, order_id=row.id, name_snapshot=item,
                unit_price_minor=total - tax, quantity=1, line_total_minor=total - tax,
            ))
            if paid_at is not None:
                payment_status = (
                    "REFUNDED" if refunded >= total
                    else "PARTIALLY_REFUNDED" if refunded else "PAID"
                )
                session.add(Payment(
                    restaurant_id=restaurant.id, order_id=row.id, method="STRIPE",
                    status=payment_status, amount_minor=total, currency="USD",
                    succeeded_at=paid_at, refunded_minor=refunded,
                ))
            return str(row.id)

    client = _staff_client(restaurant.slug)
    client.cookies.set(staff_auth.SESSION_COOKIE, staff_auth.issue_session(owner.user_id))

    def report(**params):
        res = client.get("/api/v1/restaurant/reports", params=params)
        assert res.status_code == 200, res.text
        return res.json()

    return order, report, client


def test_an_order_belongs_to_the_day_it_was_paid_where_the_restaurant_is(books):
    order, report, _ = books
    # 23:30 on the 9th in Chicago, though already the 10th in UTC.
    order(utc(2026, 9, 10, 4, 30), total=1000, tax=0)
    # 00:30 on the 10th in Chicago.
    order(utc(2026, 9, 10, 5, 30), total=2000, tax=0)

    ninth = report(**{"from": "2026-09-09", "to": "2026-09-09"})
    tenth = report(**{"from": "2026-09-10", "to": "2026-09-10"})
    assert (ninth["orders_paid"], ninth["gross_sales_minor"]) == (1, 1000)
    assert (tenth["orders_paid"], tenth["gross_sales_minor"]) == (1, 2000)
    assert tenth["timezone"] == "America/Chicago"


def test_refunds_come_off_sales_and_tax(books):
    order, report, _ = books
    day = utc(2026, 9, 10, 18, 0)
    order(day, total=1500, tax=100)                              # kept
    order(day, total=1500, tax=100, refunded=1500)               # fully refunded
    order(day, total=1500, tax=100, refunded=500)                # partly refunded
    order(day, total=1500, tax=100, status="CANCELLED")          # cancelled, not refunded

    r = report(**{"from": "2026-09-10", "to": "2026-09-10"})
    assert r["orders_paid"] == 4
    assert r["gross_sales_minor"] == 6000
    assert r["refunds_minor"] == 2000
    assert r["net_sales_minor"] == 4000
    # 400 of tax, less all of one order's 100 and a third of another's (33).
    assert r["tax_collected_minor"] == 400 - 100 - 33
    assert (r["orders_refunded"], r["orders_cancelled"], r["orders_completed"]) == (2, 1, 3)
    assert r["average_order_value_minor"] == 1500


def test_top_items_leave_out_what_was_not_really_sold(books):
    order, report, _ = books
    day = utc(2026, 9, 10, 18, 0)
    order(day, item="Smash Burger")
    order(day, item="Smash Burger", refunded=500)       # partly refunded: still sold
    order(day, item="Refunded Pie", refunded=1500)      # fully refunded
    order(day, item="Cancelled Tea", status="CANCELLED")

    r = report(**{"from": "2026-09-10", "to": "2026-09-10"})
    assert [(i["name"], i["units"]) for i in r["top_items"]] == [("Smash Burger", 2)]


def test_a_range_is_broken_down_by_day(books):
    order, report, _ = books
    order(utc(2026, 9, 8, 18, 0), total=1000, tax=0)
    order(utc(2026, 9, 10, 18, 0), total=2000, tax=0, refunded=500)
    order(utc(2026, 9, 12, 18, 0), total=4000, tax=0)  # outside the range

    r = report(**{"from": "2026-09-08", "to": "2026-09-10"})
    assert r["gross_sales_minor"] == 3000
    assert r["by_day"] == [
        {"date": "2026-09-08", "orders": 1, "gross_minor": 1000, "refunds_minor": 0, "net_minor": 1000},
        {"date": "2026-09-10", "orders": 1, "gross_minor": 2000, "refunds_minor": 500, "net_minor": 1500},
    ]


def test_it_defaults_to_today_in_the_restaurants_timezone(books):
    from zoneinfo import ZoneInfo

    _, report, _ = books
    today = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
    r = report()
    assert (r["from"], r["to"], r["today"]) == (today, today, today)


def test_expired_checkouts_are_counted_by_when_they_started(books):
    order, report, _ = books
    order(None, status="EXPIRED", created_at=utc(2026, 9, 10, 18, 0))
    order(None, status="EXPIRED", created_at=utc(2026, 9, 11, 18, 0))

    assert report(**{"from": "2026-09-10", "to": "2026-09-10"})["orders_expired"] == 1


def test_an_impossible_range_is_refused(books):
    _, _, client = books
    backwards = client.get("/api/v1/restaurant/reports", params={"from": "2026-09-10", "to": "2026-09-09"})
    assert backwards.status_code == 422
    too_long = client.get("/api/v1/restaurant/reports", params={"from": "2024-01-01", "to": "2026-09-09"})
    assert too_long.status_code == 422
    not_a_date = client.get("/api/v1/restaurant/reports", params={"from": "yesterday"})
    assert not_a_date.status_code == 422


def test_the_refund_webhook_records_how_much_was_refunded(books, monkeypatch):
    """Stripe's amount_refunded is cumulative, so a redelivery writes the same
    total instead of adding to it."""
    from app.db.session import tenant_session
    from app.workers import tasks

    order, report, _ = books
    order_id = order(utc(2026, 9, 10, 18, 0), total=1500, tax=0)
    with tenant_session(_restaurant_of(order_id)) as session:
        payment_id = session.execute(
            text("SELECT id FROM payments WHERE order_id = :o"), {"o": order_id}
        ).scalar_one()
    restaurant_id = _restaurant_of(order_id)

    monkeypatch.setattr(tasks, "_resolve_tenant_for_intent", lambda *a: (restaurant_id, payment_id))
    monkeypatch.setattr(tasks.stripe_tax, "record_refund", lambda *a: None)

    charge = {"data": {"object": {"payment_intent": "pi_test", "amount": 1500, "amount_refunded": 400}}}
    tasks._handle_charge_refunded(charge, "acct_test")
    tasks._handle_charge_refunded(charge, "acct_test")  # redelivered

    r = report(**{"from": "2026-09-10", "to": "2026-09-10"})
    assert r["refunds_minor"] == 400
    assert r["net_sales_minor"] == 1100


def _restaurant_of(order_id):
    from app.db.session import system_session

    with system_session() as session:
        return session.execute(
            text("SELECT restaurant_id FROM orders WHERE id = :o"), {"o": order_id}
        ).scalar_one()


def test_a_restaurant_timezone_must_be_a_real_one():
    from pydantic import ValidationError

    from app.schemas.api import CreateRestaurantIn, UpdateRestaurantIn

    assert CreateRestaurantIn(slug="ab", name="x", timezone="Asia/Kolkata").timezone == "Asia/Kolkata"
    for bad in ("America/Chicgo", "Mars/Olympus"):
        with pytest.raises(ValidationError, match="is not a timezone"):
            CreateRestaurantIn(slug="ab", name="x", timezone=bad)
        with pytest.raises(ValidationError, match="is not a timezone"):
            UpdateRestaurantIn(timezone=bad)
    assert UpdateRestaurantIn().timezone is None


# --------------------------------------------------------- deliveries ---
#
# The orders a restaurant ran out itself, counted apart from collections and
# per driver: they are the same sales, split, not added.


def _driver(name):
    """A driver on this restaurant's team, for the per-driver breakdown."""
    from app.db.session import system_session

    with system_session() as session:
        return session.execute(
            text(
                "INSERT INTO users (id, kind, email, full_name, password_hash, "
                "is_platform_admin, is_active, must_change_password, created_at, updated_at) "
                "VALUES (gen_random_uuid(), 'STAFF', :e, :n, 'x', false, true, false, "
                "now(), now()) RETURNING id"
            ),
            {"e": _email(), "n": name},
        ).scalar_one()


def test_deliveries_are_counted_apart_and_per_driver(books):
    order, report, _ = books
    day = utc(2026, 9, 10, 18, 0)
    dana, sam = _driver("Dana Driver"), _driver("Sam Second")

    order(day, total=1000, tax=0)                                     # a collection
    order(day, total=2000, tax=0, driver_id=dana)                     # delivered
    order(day, total=1500, tax=0, driver_id=dana, status="OUT_FOR_DELIVERY")
    order(day, total=3000, tax=0, driver_id=sam)                      # delivered

    r = report(**{"from": "2026-09-10", "to": "2026-09-10"})
    assert r["orders_paid"] == 4
    assert (r["orders_delivery"], r["orders_delivered"]) == (3, 2)
    assert r["delivery_sales_minor"] == 6500
    # The split does not inflate the takings.
    assert r["gross_sales_minor"] == 7500

    assert r["by_driver"] == [
        {"driver": "Dana Driver", "orders": 2, "delivered": 1, "gross_minor": 3500},
        {"driver": "Sam Second", "orders": 1, "delivered": 1, "gross_minor": 3000},
    ]


def test_a_day_without_deliveries_says_so_with_zeroes(books):
    order, report, _ = books
    order(utc(2026, 9, 10, 18, 0), total=1000, tax=0)

    r = report(**{"from": "2026-09-10", "to": "2026-09-10"})
    assert (r["orders_delivery"], r["orders_delivered"], r["delivery_sales_minor"]) == (0, 0, 0)
    assert r["by_driver"] == []
