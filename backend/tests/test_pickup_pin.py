"""The pickup PIN locks after five wrong attempts, and the lock holds.

A wrong PIN incremented the order's failed-attempt count and then raised. The
request's session rolls back on any exception, so the count was discarded
with it: seven wrong PINs in a row left it at zero, the lock never engaged,
and a six-digit PIN could be guessed without limit.

The PIN is sent in the request body, never the URL, which access logs record;
and one staff account may enter only so many wrong PINs across all orders.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor

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

PIN = "482913"


class FakeRedis:
    """Just enough of redis-runtime for the limiter: no Redis runs in CI, and
    a limiter that fails open would make every limit test pass vacuously."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def pipeline(self):
        store = self.store
        ops = []

        class Pipe:
            def incr(self, key):
                ops.append(key)

            def expire(self, key, seconds):
                pass

            def execute(self):
                for key in ops:
                    store[key] = str(int(store.get(key, 0)) + 1)
                return [int(store[ops[-1]]), True] if ops else []

        return Pipe()


@pytest.fixture(autouse=True)
def redis(monkeypatch):
    from app.core import ratelimit

    fake = FakeRedis()
    monkeypatch.setattr(ratelimit, "runtime_redis", lambda: fake)
    return fake


@pytest.fixture
def counter(admin_user, cleanup):
    """A restaurant, a signed-in owner, and a way to put paid orders on its board."""
    from app.db.session import system_session

    restaurant = _create(admin_user, cleanup)
    email = _email()
    out, _ = _owner(admin_user, restaurant.id, email)
    _set_own_password(email, "owner password 123")

    with system_session() as session:
        customer_id = session.execute(
            text(
                "INSERT INTO users (id, kind, email, is_platform_admin, "
                "is_active, must_change_password, created_at, updated_at) "
                "VALUES (gen_random_uuid(), 'CUSTOMER', :e, false, true, false, now(), now()) "
                "RETURNING id"
            ),
            {"e": f"pin-{uuid.uuid4().hex[:8]}@zenoeats.invalid"},
        ).scalar_one()

    numbers = iter(range(2001, 3000))

    def client(user_id=out.user_id):
        c = _staff_client(restaurant.slug)
        c.cookies.set(staff_auth.SESSION_COOKIE, staff_auth.issue_session(user_id))
        return c

    def colleague():
        """A second, active KITCHEN member with a login of their own."""
        from app.db.base import utcnow
        from app.db.session import tenant_session
        from app.models import RestaurantUser

        with system_session() as session:
            user_id = session.execute(
                text(
                    "INSERT INTO users (id, kind, email, password_hash, is_platform_admin, "
                    "is_active, must_change_password, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), 'STAFF', :e, :h, false, true, false, now(), now()) "
                    "RETURNING id"
                ),
                {"e": _email(), "h": staff_auth.hash_password("kitchen password 1")},
            ).scalar_one()
        with tenant_session(restaurant.id) as session:
            session.add(RestaurantUser(
                restaurant_id=restaurant.id, user_id=user_id, role_code="KITCHEN",
                status="ACTIVE", invited_at=utcnow(), accepted_at=utcnow(),
            ))
        return client(user_id)

    def ready_order():
        from app.core.crypto import encrypt_field
        from app.db.base import utcnow
        from app.db.session import tenant_session
        from app.models import Order, Payment

        now = utcnow()
        with tenant_session(restaurant.id) as session:
            order = Order(
                restaurant_id=restaurant.id, order_number=next(numbers),
                customer_user_id=customer_id, status="READY_FOR_PICKUP",
                payment_method="STRIPE", currency="USD", subtotal_minor=1000,
                discount_minor=0, tax_minor=0, total_minor=1000,
                pickup_pin_encrypted=encrypt_field(PIN), paid_at=now,
            )
            session.add(order)
            session.flush()
            session.add(Payment(
                restaurant_id=restaurant.id, order_id=order.id, method="STRIPE",
                status="PAID", amount_minor=1000, currency="USD", succeeded_at=now,
            ))
            return str(order.id)

    def attempts(order_id):
        from app.db.session import tenant_session

        with tenant_session(restaurant.id) as session:
            return session.execute(
                text("SELECT pickup_pin_failed_attempts FROM orders WHERE id = :i"), {"i": order_id}
            ).scalar_one()

    return client, ready_order, attempts, colleague


def _complete(client, order_id, pin):
    return client.post(f"/api/v1/restaurant/orders/{order_id}/complete", json={"pin": pin})


def test_five_wrong_pins_lock_the_order_and_the_lock_holds(counter):
    client, ready_order, attempts, _ = counter
    staff = client()
    order_id = ready_order()

    for expected_left in (4, 3, 2, 1):
        res = _complete(staff, order_id, "000000")
        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "PIN_INVALID"
        assert f"{expected_left} attempt" in res.json()["detail"]["message"]

    # The fifth wrong PIN is the one that locks, and says so.
    fifth = _complete(staff, order_id, "000000")
    assert fifth.status_code == 423
    assert fifth.json()["detail"]["code"] == "PIN_LOCKED"
    assert attempts(order_id) == 5

    # Locked for the right PIN too, and from another device.
    assert _complete(client(), order_id, PIN).status_code == 423
    assert attempts(order_id) == 5


def test_the_right_pin_still_works_before_the_lock(counter):
    client, ready_order, attempts, _ = counter
    staff = client()
    order_id = ready_order()

    assert _complete(staff, order_id, "111111").status_code == 400
    assert _complete(staff, order_id, "222222").status_code == 400
    assert attempts(order_id) == 2

    done = _complete(staff, order_id, PIN)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "COMPLETED"


def test_simultaneous_guesses_cannot_get_past_five(counter):
    """Without the row lock, guesses sent together each read the same count
    and write back one more, so a burst could far exceed five."""
    client, ready_order, attempts, _ = counter
    order_id = ready_order()

    with ThreadPoolExecutor(max_workers=10) as pool:
        codes = list(pool.map(lambda _: _complete(client(), order_id, "000000").status_code, range(10)))

    assert attempts(order_id) == 5
    assert codes.count(400) == 4
    assert codes.count(423) == 6


def test_a_pin_in_the_url_is_not_read(counter):
    """The query string is what access logs record, so it is not accepted at
    all -- and a request carrying one costs the order no attempt."""
    client, ready_order, attempts, _ = counter
    order_id = ready_order()

    res = client().post(f"/api/v1/restaurant/orders/{order_id}/complete", params={"pin": PIN})
    assert res.status_code == 422
    assert attempts(order_id) == 0


def test_one_account_cannot_spend_five_guesses_on_every_order(counter, redis):
    from app.api.v1.restaurant import PIN_FAILURES_PER_STAFF, PIN_ATTEMPTS

    client, ready_order, attempts, colleague = counter
    staff = client()

    # Four wrong on each order, one short of locking any of them, until the
    # account's budget of wrong PINs is spent.
    spent = 0
    while spent < PIN_FAILURES_PER_STAFF:
        order_id = ready_order()
        for _ in range(min(PIN_ATTEMPTS - 1, PIN_FAILURES_PER_STAFF - spent)):
            assert _complete(staff, order_id, "000000").status_code == 400
            spent += 1

    fresh = ready_order()
    refused = _complete(staff, fresh, PIN)
    assert refused.status_code == 429
    assert refused.json()["detail"]["code"] == "RATE_LIMITED"
    # Refused before the PIN was looked at: even the right one does not go
    # through, and the order is not charged an attempt.
    assert attempts(fresh) == 0

    # Someone else on the same counter is unaffected.
    assert _complete(colleague(), fresh, PIN).status_code == 200


def test_correct_pins_never_count_against_the_budget(counter, redis):
    client, ready_order, _, _ = counter
    staff = client()

    for _ in range(3):
        assert _complete(staff, ready_order(), PIN).status_code == 200

    assert not [key for key in redis.store if key.startswith("rl:pickup_pin_failures:")]
