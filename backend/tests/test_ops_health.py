"""/health/operations fails when the machinery behind requests stops.

Each check is proved both ways against the real database: the condition
it watches for trips it, and the nearest harmless case does not. The
"does not" cases compare against a baseline rather than expecting an empty
list, because the shared development database may hold rows of its own.
"""

import time
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.base import utcnow
from app.services import ops_health

integration = pytest.mark.integration


@pytest.fixture(autouse=True)
def own_heartbeat(monkeypatch):
    """A key of this test's own, so a running worker's heartbeat cannot
    answer for it."""
    key = f"zenoeats:ops:heartbeat:test-{uuid.uuid4().hex}"
    monkeypatch.setattr(ops_health, "HEARTBEAT_KEY", key)
    yield key
    monkeypatch.undo()
    ops_health.broker().delete(key)


# ------------------------------------------------------------- webhooks ---

@pytest.fixture
def webhook_rows():
    """Insert stripe_events rows; every one removed after."""
    from app.services.retention import _platform_transaction

    made: list[tuple[str, uuid.UUID]] = []

    def add(table: str, status: str, received_at):
        row_id = uuid.uuid4()
        id_column = "stripe_event_id"
        with _platform_transaction() as session:
            session.execute(
                text(f"INSERT INTO {table} (id, {id_column}, type, payload, status, "
                     "received_at, attempts) VALUES (:id, :eid, 'test.ops', '{}'::jsonb, "
                     ":status, :at, 0)"),
                {"id": row_id, "eid": f"evt_ops_{uuid.uuid4().hex}", "status": status,
                 "at": received_at},
            )
        made.append((table, row_id))

    yield add

    # The role the retention sweep deletes with: the system role never may.
    with _platform_transaction() as session:
        for table, row_id in made:
            session.execute(text(f"DELETE FROM {table} WHERE id = :id"), {"id": row_id})


@integration
@pytest.mark.parametrize("table", ["stripe_events"])
def test_a_webhook_left_unprocessed_is_reported(webhook_rows, table):
    before = ops_health.failing()
    webhook_rows(table, "RECEIVED", utcnow() - timedelta(seconds=30))
    assert ops_health.failing() == before, "a webhook seconds old is just in flight"

    webhook_rows(table, "RECEIVED", utcnow() - timedelta(minutes=11))
    assert "webhooks_stuck" in ops_health.failing()


@integration
@pytest.mark.parametrize("table", ["stripe_events"])
def test_a_webhook_that_failed_today_is_reported(webhook_rows, table):
    before = ops_health.failing()
    webhook_rows(table, "FAILED", utcnow() - timedelta(days=2))
    assert ops_health.failing() == before, "an old failure was already alarmed on"

    webhook_rows(table, "FAILED", utcnow() - timedelta(hours=1))
    assert "webhooks_failed" in ops_health.failing()


# ------------------------------------------------------------ checkouts ---

@pytest.fixture
def pending_order():
    """An unpaid order whose expiry the test sets."""
    from app.api.v1.admin import _PURGE_ORDER
    from app.db.session import system_session, tenant_session
    from app.models import Order, OrderStatus, Restaurant, RestaurantStatus, User, UserKind

    suffix = uuid.uuid4().hex[:8]
    with system_session() as session:
        restaurant = Restaurant(slug=f"ops-{suffix}", name="Ops Test",
                                status=RestaurantStatus.ACTIVE.value,
                                timezone="UTC", currency="USD")
        customer = User(kind=UserKind.CUSTOMER.value,
                        email=f"ops-{suffix}@zenoeats.invalid")
        session.add_all([restaurant, customer])
        session.flush()
        rid, uid = restaurant.id, customer.id

    with tenant_session(rid) as session:
        order = Order(
            restaurant_id=rid, order_number=9301, customer_user_id=uid,
            fulfillment_type="PICKUP", status=OrderStatus.PENDING_PAYMENT.value,
            payment_method="STRIPE", currency="USD", subtotal_minor=1000, discount_minor=0,
            tax_minor=0, total_minor=1000, expires_at=utcnow(),
        )
        session.add(order)
        session.flush()
        oid = order.id

    def expired_for(minutes: int):
        with tenant_session(rid) as session:
            session.execute(
                text("UPDATE orders SET expires_at = now() - make_interval(mins => :m) "
                     "WHERE id = :o"),
                {"m": minutes, "o": oid},
            )

    yield expired_for

    with tenant_session(rid) as session:
        for table in _PURGE_ORDER:
            session.execute(text(f"DELETE FROM {table} WHERE restaurant_id = :r"), {"r": rid})
        session.execute(text("DELETE FROM restaurants WHERE id = :r"), {"r": rid})


@integration
def test_checkouts_the_sweep_has_not_expired_are_reported(pending_order):
    pending_order(5)
    if "stale_checkouts" in ops_health.failing():
        pytest.skip("this database already has stale checkouts of its own")

    pending_order(16)
    assert "stale_checkouts" in ops_health.failing()


# ------------------------------------------------------------ heartbeat ---

@integration
def test_the_heartbeat_proves_beat_and_a_worker_are_running(own_heartbeat):
    assert "worker_heartbeat" in ops_health.failing(), "never beaten"

    ops_health.beat()
    assert "worker_heartbeat" not in ops_health.failing()

    ops_health.broker().set(own_heartbeat, str(time.time() - 301))
    assert "worker_heartbeat" in ops_health.failing()


@integration
def test_the_heartbeat_task_beats():
    from app.workers.tasks import heartbeat

    heartbeat()
    assert "worker_heartbeat" not in ops_health.failing()


# --------------------------------------------------------- dependencies ---

class _Down:
    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise ConnectionError("down")
        return fail


@integration
def test_redis_being_down_is_reported(monkeypatch):
    ops_health.beat()
    monkeypatch.setattr(ops_health, "runtime_redis", lambda: _Down())
    assert "redis_runtime" in ops_health.failing()

    monkeypatch.setattr(ops_health, "broker", lambda: _Down())
    assert "redis_broker" in ops_health.failing()


@pytest.mark.parametrize(
    "total, free, reported",
    [
        (100 * 1024**3, 30 * 1024**3, False),
        (100 * 1024**3, 9 * 1024**3, True),   # under a tenth
        (10 * 1024**3, 1.5 * 1024**3, True),  # a tenth, but under 2 GB
    ],
)
def test_a_filling_disk_is_reported(monkeypatch, total, free, reported):
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(ops_health.shutil, "disk_usage",
                        lambda path: usage(total, total - free, free))
    monkeypatch.setattr(ops_health, "system_session", _unreachable)
    monkeypatch.setattr(ops_health, "runtime_redis", lambda: _Down())
    monkeypatch.setattr(ops_health, "broker", lambda: _Down())
    assert ("disk" in ops_health.failing()) is reported


def _unreachable():
    raise ConnectionError("down")


def test_the_database_being_unreachable_is_reported(monkeypatch):
    from collections import namedtuple

    # A roomy disk, whatever the machine running this has.
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(ops_health.shutil, "disk_usage", lambda path: usage(100, 10, 90 * 1024**3))
    monkeypatch.setattr(ops_health, "system_session", _unreachable)
    monkeypatch.setattr(ops_health, "runtime_redis", lambda: _Down())
    monkeypatch.setattr(ops_health, "broker", lambda: _Down())
    assert ops_health.failing() == ["database", "redis_runtime", "redis_broker"]


# ------------------------------------------------------------- endpoint ---

def test_the_endpoint_names_what_is_failing_and_nothing_more(monkeypatch):
    from app.main import app

    monkeypatch.setattr(ops_health, "failing", lambda: ["webhooks_stuck"])
    with TestClient(app) as client:
        response = client.get("/health/operations")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "failing": ["webhooks_stuck"]}

    monkeypatch.setattr(ops_health, "failing", lambda: [])
    with TestClient(app) as client:
        response = client.get("/health/operations")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
