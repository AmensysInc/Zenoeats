"""The content migration: what `alembic upgrade head` puts on a storefront.

Revision 0047 loads a menu and writes its photographs, which makes it a
migration with a lot more to get wrong than a column. The things worth
holding still are:

  - it loads the whole menu, not most of it
  - applying it twice leaves one of everything
  - it never overwrites a photograph the restaurant uploaded itself
  - with nowhere to write the pictures, the menu still loads and no row is
    left pointing at a file that does not exist
  - on a database with no such restaurant it does nothing, quietly, because
    that is CI and every fresh database

Each test runs the revision against a transaction and rolls it back, so the
rows never outlive the test and the pictures are written into tmp_path.

Skipped without DATABASE_URL_MIGRATE: this is the schema owner's work, and
those credentials are deliberately given to the migrate job alone -- the api
container is handed an empty string (see docker-compose.yml). CI sets it, so
the gate is real there.
"""

import importlib.util
import json
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.config import settings

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "alembic" / "versions" / "0047_jrs_corner_storefront.py"


def _migration():
    """The revision, imported from its path.

    Version files are not importable as modules -- the directory is not a
    package, and the name starts with a digit -- so this is how the thing
    under test gets into the test.
    """
    spec = importlib.util.spec_from_file_location("revision_0047", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _migration()
MENU = json.loads(mod.SNAPSHOT.read_text())


@pytest.fixture
def conn():
    """A migrate-role connection whose transaction is thrown away."""
    if not settings.DATABASE_URL_MIGRATE:
        pytest.skip("DATABASE_URL_MIGRATE is not set; this runs as the schema owner")

    engine = create_engine(settings.DATABASE_URL_MIGRATE)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            # Undoes the rows and the FORCE RLS lift together: both were
            # done in this transaction and neither was committed.
            transaction.rollback()
    engine.dispose()


@pytest.fixture
def store(conn):
    """An onboarded restaurant with no menu -- what the revision expects."""
    slug = f"mig-{uuid.uuid4().hex[:8]}"
    conn.execute(text("ALTER TABLE restaurants NO FORCE ROW LEVEL SECURITY"))
    rid = conn.execute(
        text(
            "INSERT INTO restaurants (id, slug, name, status, timezone, currency) "
            "VALUES (gen_random_uuid(), :s, 'Migration Test', 'ACTIVE', 'UTC', 'USD') "
            "RETURNING id"
        ),
        {"s": slug},
    ).scalar_one()
    conn.execute(text("ALTER TABLE restaurants FORCE ROW LEVEL SECURITY"))
    return slug, rid


@pytest.fixture
def images(tmp_path, monkeypatch):
    """Photographs land in a folder that disappears with the test."""
    folder = tmp_path / "images"
    monkeypatch.setenv("IMAGES_DIR", str(folder))
    return folder


@pytest.fixture
def targeted(store, monkeypatch):
    """Point the revision at the test's restaurant instead of Jr's Corner."""
    slug, rid = store
    monkeypatch.setattr(mod, "TARGET_SLUGS", (slug,))
    return slug, rid


@contextmanager
def owner_reads(conn):
    """Let this connection see the rows it just wrote.

    FORCE ROW LEVEL SECURITY applies to the table owner too, and every policy
    on these tables names zenoeats_app or zenoeats_system -- so the migrate
    role's own SELECT matches no policy and comes back empty rather than
    failing. Without this the assertions below compared nothing to nothing
    and passed while the migration did the wrong thing.
    """
    mod._force_rls(conn, False)
    try:
        yield
    finally:
        mod._force_rls(conn, True)


def _count(conn, table: str, rid) -> int:
    return conn.execute(
        text(f"SELECT count(*) FROM {table} WHERE restaurant_id = :r"), {"r": rid}
    ).scalar_one()


def test_it_loads_the_whole_frozen_menu(conn, targeted, images):
    _, rid = targeted
    mod._apply(conn)

    with owner_reads(conn):
        assert _count(conn, "item_types", rid) == len(MENU["item_types"])
        assert _count(conn, "menu_items", rid) == len(MENU["items"])
        assert _count(conn, "modifier_groups", rid) == len(MENU["modifier_groups"])
        assert _count(conn, "modifier_options", rid) == sum(
            len(group["options"]) for group in MENU["modifier_groups"]
        )
        assert _count(conn, "meals", rid) == len(MENU["meals"])
        assert _count(conn, "combos", rid) == len(MENU["combos"])
        assert _count(conn, "combo_slots", rid) == sum(
            len(combo["slots"]) for combo in MENU["combos"]
        )
        # The home page, which is not in the menu file.
        assert _count(conn, "storefront_banners", rid) == len(mod.BANNERS)
        assert _count(conn, "storefront_collections", rid) == 1


def test_every_photograph_it_records_is_on_disk(conn, targeted, images):
    """The failure this rules out is a storefront of broken image icons."""
    _, rid = targeted
    mod._apply(conn)

    with owner_reads(conn):
        keys = [
            row[0]
            for table in ("menu_items", "item_types", "storefront_banners")
            for row in conn.execute(
                text(
                    f"SELECT image_path FROM {table} "
                    "WHERE restaurant_id = :r AND image_path IS NOT NULL"
                ),
                {"r": rid},
            ).all()
        ]

    expected = (
        sum(1 for item in MENU["items"] if item.get("photo"))
        + sum(1 for heading in MENU["item_types"] if heading.get("photo"))
        + len(mod.BANNERS)
    )
    assert len(keys) == expected
    assert [key for key in keys if not (images / key).is_file()] == []


def test_applying_it_twice_leaves_one_of_everything(conn, targeted, images):
    """A content migration is re-askable (the downgrade removes nothing), so
    the second pass has to update rather than duplicate."""
    _, rid = targeted
    tables = (
        "item_types", "menu_items", "modifier_groups", "modifier_options",
        "meals", "combos", "combo_slots", "combo_slot_items",
        "item_modifier_groups", "item_included_options",
        "storefront_banners", "storefront_collections",
    )
    mod._apply(conn)
    with owner_reads(conn):
        before = {table: _count(conn, table, rid) for table in tables}
    assert before["menu_items"] == len(MENU["items"]), "nothing loaded, so nothing is proven"
    files = sorted(path.name for path in images.rglob("*.webp"))

    mod._apply(conn)

    with owner_reads(conn):
        assert {table: _count(conn, table, rid) for table in tables} == before
    # And no second copy of a picture that was already written.
    assert sorted(path.name for path in images.rglob("*.webp")) == files


def test_it_leaves_a_photograph_the_restaurant_uploaded_alone(conn, targeted, images):
    _, rid = targeted
    mod._apply(conn)

    theirs = f"restaurants/{rid}/items/{uuid.uuid4().hex}.webp"
    name = MENU["items"][0]["name"]
    with owner_reads(conn):
        changed = conn.execute(
            text(
                "UPDATE menu_items SET image_path = :k "
                "WHERE restaurant_id = :r AND name = :n"
            ),
            {"k": theirs, "r": rid, "n": name},
        ).rowcount
    assert changed == 1, "the dish was not there to photograph"

    mod._apply(conn)

    with owner_reads(conn):
        assert conn.execute(
            text("SELECT image_path FROM menu_items WHERE restaurant_id = :r AND name = :n"),
            {"r": rid, "n": name},
        ).scalar_one() == theirs


def test_the_menu_loads_when_the_pictures_cannot_be_written(conn, targeted, monkeypatch):
    """No volume mounted. The menu is the part that matters; a photo key with
    no file behind it would be worse than no photo at all."""
    monkeypatch.setenv("IMAGES_DIR", "/proc/not-a-writable-place")
    _, rid = targeted

    mod._apply(conn)

    with owner_reads(conn):
        assert _count(conn, "menu_items", rid) == len(MENU["items"])
        assert conn.execute(
            text(
                "SELECT count(*) FROM menu_items "
                "WHERE restaurant_id = :r AND image_path IS NOT NULL"
            ),
            {"r": rid},
        ).scalar_one() == 0
        # image_path is NOT NULL on a banner, so there is no row to write.
        assert _count(conn, "storefront_banners", rid) == 0


def test_it_does_nothing_when_the_restaurant_is_not_there(conn, monkeypatch, images):
    """Every fresh database and every CI run. Not a failure: a migration
    cannot wait for somebody to onboard a tenant."""
    monkeypatch.setattr(mod, "TARGET_SLUGS", (f"absent-{uuid.uuid4().hex[:8]}",))
    with owner_reads(conn):
        before = conn.execute(text("SELECT count(*) FROM menu_items")).scalar_one()

    mod._apply(conn)

    with owner_reads(conn):
        assert conn.execute(text("SELECT count(*) FROM menu_items")).scalar_one() == before
    assert list(images.rglob("*.webp")) == []


def test_force_rls_is_put_back(conn, targeted, images):
    """The lift is temporary. Leaving a menu table NO FORCE would quietly
    remove tenant isolation for the app role's own queries."""
    mod._apply(conn)

    unforced = conn.execute(
        text(
            "SELECT relname FROM pg_class "
            "WHERE relname = ANY(:tables) AND NOT relforcerowsecurity"
        ),
        {"tables": list(mod.CONTENT_TABLES)},
    ).all()
    assert unforced == []


def test_every_photograph_the_snapshot_names_exists(images):
    """A named asset that is not in the repository loads as a dish with no
    picture, and nothing fails -- so the check belongs here."""
    named = {item["photo"] for item in MENU["items"] if item.get("photo")}
    named |= {heading["photo"] for heading in MENU["item_types"] if heading.get("photo")}
    named |= {asset for asset, _, _ in mod.BANNERS}
    named.add(mod.LOCATION_CARD)

    missing = sorted(name for name in named if not (mod.ASSETS / f"{name}.webp").is_file())
    assert missing == []


# ---------------------------------------------------- the platform picker ---
#
# These start by clearing the four rows, because a development database has
# them from seed.py and CI has nothing: a test that only holds in one of
# those two worlds is not a gate, it is a coincidence. The deletes roll back
# with everything else.

@pytest.fixture
def empty_picker(conn):
    with owner_reads(conn):
        conn.execute(
            text("DELETE FROM locations WHERE slug = ANY(:slugs)"),
            {"slugs": [spec["slug"] for spec in mod.LOCATIONS]},
        )
    return None


def _location(conn, slug):
    with owner_reads(conn):
        return conn.execute(
            text("SELECT status, restaurant_id, image_path FROM locations WHERE slug = :s"),
            {"s": slug},
        ).first()


def test_it_fills_the_platform_picker(conn, empty_picker, targeted, images):
    _, rid = targeted
    mod._apply(conn)

    with owner_reads(conn):
        listed = {
            row[0]
            for row in conn.execute(
                text("SELECT slug FROM locations WHERE slug = ANY(:slugs)"),
                {"slugs": [spec["slug"] for spec in mod.LOCATIONS]},
            ).all()
        }
    assert listed == {spec["slug"] for spec in mod.LOCATIONS}

    # The one that opens a store opens this one, and carries its card photo.
    open_card = _location(conn, "jrs-corner")
    assert open_card.status == "OPEN"
    assert open_card.restaurant_id == rid
    assert (images / open_card.image_path).is_file()

    # The other three are honest about not being open.
    assert _location(conn, "big-apple").status == "COMING_SOON"
    assert _location(conn, "big-apple").restaurant_id is None


def test_a_location_with_no_tenant_behind_it_is_listed_as_coming_soon(
    conn, empty_picker, monkeypatch, images
):
    """The deploy that runs before anybody has onboarded the store. A card
    that says "not open yet" is a front page; a card that goes nowhere is a
    dead end, and the table's own CHECK constraint refuses it anyway."""
    monkeypatch.setattr(mod, "TARGET_SLUGS", (f"absent-{uuid.uuid4().hex[:8]}",))

    mod._apply(conn)

    row = _location(conn, "jrs-corner")
    assert row.status == "COMING_SOON"
    assert row.restaurant_id is None


def test_an_open_location_is_never_closed_or_re_pointed(conn, store, monkeypatch, images):
    """What this protects is a live store on the front page. If the tenant a
    card points at is missing the day this runs, that is not a reason to shut
    the door on it, and a deploy is never a reason to point it somewhere
    else."""
    _, live = store
    with owner_reads(conn):
        conn.execute(
            text("DELETE FROM locations WHERE slug = ANY(:slugs)"),
            {"slugs": [spec["slug"] for spec in mod.LOCATIONS]},
        )
        conn.execute(
            text(
                "INSERT INTO locations (slug, name, city, status, restaurant_id) "
                "VALUES ('jrs-corner', 'Jr''s Corner', 'Ardmore', 'OPEN', :r)"
            ),
            {"r": live},
        )

    # A different restaurant is the one being loaded this time.
    other = f"mig-{uuid.uuid4().hex[:8]}"
    with owner_reads(conn):
        conn.execute(text("ALTER TABLE restaurants NO FORCE ROW LEVEL SECURITY"))
        conn.execute(
            text(
                "INSERT INTO restaurants (id, slug, name, status, timezone, currency) "
                "VALUES (gen_random_uuid(), :s, 'Other', 'ACTIVE', 'UTC', 'USD')"
            ),
            {"s": other},
        )
    monkeypatch.setattr(mod, "TARGET_SLUGS", (other,))

    mod._apply(conn)

    row = _location(conn, "jrs-corner")
    assert row.status == "OPEN"
    assert row.restaurant_id == live
