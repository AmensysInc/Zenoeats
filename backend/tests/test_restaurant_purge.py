"""Removing a restaurant for good, and refusing to.

The ordinary delete is a soft one and stays that way: a restaurant that has
traded has orders, payments and tax history hanging off it. The purge is for
the other case -- a draft nobody used, a duplicate from a typo, a throwaway
left by a test run -- and the whole question is telling those apart.
"""

import uuid

import pytest
from sqlalchemy import inspect, select, text

from app.api.v1.admin import _PURGE_DETACH, _PURGE_ORDER, purge_restaurant
from app.core import errors
from app.db.session import app_engine, system_session, tenant_session
from app.db.base import utcnow
from app.models import ItemType, Meal, Restaurant, RestaurantStatus, User, UserKind

# Every test here reads the live schema or writes to it. app_engine() is a
# function returning the engine, not the engine itself.
pytestmark = pytest.mark.integration


def _schema():
    return inspect(app_engine())


ADMIN_EMAIL = "purge-tests@zenoeats.invalid"


@pytest.fixture
def admin_user():
    """A real user row, because the purge writes an audit line with a foreign
    key to it. A stand-in object gets as far as the delete and then fails on
    the record of it, which is the wrong half to leave working.

    Found or created rather than made fresh each run, and never cleaned up.
    Neither can be: zenoeats_system is granted SELECT, INSERT and UPDATE on
    users and platform_audit_logs, and deliberately not DELETE -- an audit log
    an application can erase is not an audit log. One reusable row is the way
    to not accumulate one per run.
    """
    with system_session() as session:
        user = session.execute(
            select(User).where(User.email == ADMIN_EMAIL)
        ).scalar_one_or_none()
        if user is None:
            user = User(
                kind=UserKind.PLATFORM_ADMIN.value, email=ADMIN_EMAIL, full_name="Purge Tests"
            )
            session.add(user)
            session.flush()
        return SimpleUser(user.id)


class SimpleUser:
    def __init__(self, uid):
        self.id = uid


def test_the_purge_list_covers_every_table_that_carries_a_restaurant():
    """The list is written out by hand because its order is the correctness
    argument. That makes it something a new table can be left out of, and a
    table left out of it leaves rows behind pointing at a restaurant that no
    longer exists.

    A table may be handled either way. Most carry rows the tenant owns and are
    deleted (_PURGE_ORDER); `locations` is platform data that merely points at
    a tenant, so it is detached instead (_PURGE_DETACH) -- removing one
    restaurant must not take a city off the front page. What this gate refuses
    is a table in neither list, because that is the one leaving orphans.
    """
    columns = _schema()
    tenant_tables = {
        table
        for table in columns.get_table_names(schema="public")
        if any(c["name"] == "restaurant_id" for c in columns.get_columns(table))
    }

    missing = tenant_tables - set(_PURGE_ORDER) - set(_PURGE_DETACH)
    assert not missing, f"tenant tables the purge would leave behind: {sorted(missing)}"

    both = set(_PURGE_ORDER) & set(_PURGE_DETACH)
    assert not both, f"tables both deleted and detached: {sorted(both)}"


def test_the_purge_list_names_only_real_tables():
    columns = _schema()
    real = set(columns.get_table_names(schema="public"))
    unknown = set(_PURGE_ORDER) - real
    assert not unknown, f"purge names tables that do not exist: {sorted(unknown)}"


def test_children_are_listed_before_their_parents():
    """Deepest first, or a delete fails on a foreign key. Checked against the
    real constraints rather than by eye, because the order is long enough to
    get wrong quietly."""
    columns = _schema()
    position = {table: i for i, table in enumerate(_PURGE_ORDER)}

    for table in _PURGE_ORDER:
        for fk in columns.get_foreign_keys(table):
            parent = fk["referred_table"]
            if parent == table or parent not in position:
                continue  # self-reference, or a platform table nothing purges
            assert position[table] < position[parent], (
                f"{table} points at {parent} but is purged after it"
            )


def test_a_restaurant_that_never_traded_is_purged_whole(admin_user):
    """End to end against the real database, because the point of a purge is
    which rows are actually gone afterwards."""
    with system_session() as session:
        restaurant = Restaurant(
            slug=f"purge-{uuid.uuid4().hex[:8]}", name="Purge Me",
            status=RestaurantStatus.SUSPENDED.value,
            timezone="UTC", currency="USD",
        )
        session.add(restaurant)
        session.flush()
        rid = restaurant.id

    try:
        with tenant_session(rid) as session:
            session.add(Meal(restaurant_id=rid, name="Breakfast"))
            session.add(ItemType(restaurant_id=rid, name="Food", sort_order=0))

        # Not deleted yet, so the purge refuses: it is the second half of a
        # decision, not a shortcut past it.
        with pytest.raises(errors.ApiError) as caught:
            purge_restaurant(rid, admin=admin_user)
        assert caught.value.status_code == 409
        assert caught.value.code == "NOT_DELETED"

        with system_session() as session:
            session.get(Restaurant, rid).deleted_at = utcnow()

        purge_restaurant(rid, admin=admin_user)

        with system_session() as session:
            assert session.get(Restaurant, rid) is None

        # Counted in a tenant session: zenoeats_system can read restaurants,
        # payment accounts, orders and payments, and nothing else. A menu
        # table is not on that list on purpose.
        with tenant_session(rid) as session:
            for table in ("meals", "item_types"):
                left = session.execute(
                    text(f"SELECT count(*) FROM {table} WHERE restaurant_id = :r"),
                    {"r": rid},
                ).scalar_one()
                assert left == 0, f"{table} left {left} rows behind"
    finally:
        # Whatever happened above, this restaurant does not outlive the test.
        with tenant_session(rid) as session:
            for table in _PURGE_ORDER:
                session.execute(
                    text(f"DELETE FROM {table} WHERE restaurant_id = :r"), {"r": rid}
                )
            session.execute(text("DELETE FROM restaurants WHERE id = :r"), {"r": rid})


def test_a_restaurant_with_an_order_is_refused(admin_user):
    """The retention promise, enforced rather than documented. A restaurant
    that has taken money is not purgeable through this API by anyone."""
    with system_session() as session:
        restaurant = Restaurant(
            slug=f"purge-{uuid.uuid4().hex[:8]}", name="Has Traded",
            status=RestaurantStatus.SUSPENDED.value,
            timezone="UTC", currency="USD", deleted_at=utcnow(),
        )
        session.add(restaurant)
        session.flush()
        rid = restaurant.id

    try:
        with tenant_session(rid) as session:
            session.execute(
                text(
                    """
                    INSERT INTO orders (id, restaurant_id, customer_user_id,
                                        order_number, status, fulfillment_type,
                                        payment_method, currency, subtotal_minor,
                                        discount_minor, tax_minor, total_minor,
                                        created_at, updated_at)
                    VALUES (gen_random_uuid(), :r, :u, 9001, 'PENDING_PAYMENT',
                            'PICKUP', 'STRIPE', 'USD', 100, 0, 0, 100, now(), now())
                    """
                ),
                {"r": rid, "u": admin_user.id},
            )

        with pytest.raises(errors.ApiError) as caught:
            purge_restaurant(rid, admin=admin_user)

        assert caught.value.status_code == 409
        assert caught.value.code == "RESTAURANT_HAS_HISTORY"
        assert "1 order" in str(caught.value.detail)

        with system_session() as session:
            assert session.get(Restaurant, rid) is not None, "a refusal deleted something"
    finally:
        with tenant_session(rid) as session:
            for table in _PURGE_ORDER:
                session.execute(
                    text(f"DELETE FROM {table} WHERE restaurant_id = :r"), {"r": rid}
                )
            session.execute(text("DELETE FROM restaurants WHERE id = :r"), {"r": rid})
