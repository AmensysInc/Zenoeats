"""Jr's Corner's menu and its photographs arrive with the deploy.

Until now the menu existed in two places and neither of them was a release:
inside scripts/seed.py, which bootstraps a whole demo tenant and refuses to
run twice, and inside scripts/import_menu.py, which is a command somebody has
to remember to run on the production box after the containers are up. A
storefront that is empty until a human runs a script by hand is a storefront
that ships empty.

So the content travels the way the schema does -- `alembic upgrade head`,
the step the deploy already runs with the api image (see ci.yml, "Say what
was published"). After it, zenoeats.com lists the four locations and the
store behind Jr's Corner has its headings, its dishes, its modifier groups,
its combos, its home banners and its photography.

The locations are here for the same reason and a worse one: 0044 built that
table and left it empty, so until now the platform front page was filled by
seed.py alone and a production database would open on nothing at all.

WHAT THIS DOES NOT DO IS CREATE A TENANT. A restaurant row is the small part
of onboarding; the rest is an order counter, an admin who can sign in, a
Stripe account and delivery zones, and inventing those here would produce a
tenant that looks onboarded and cannot take a payment. So this attaches a
menu to a restaurant that exists and otherwise does nothing.

IF THE RESTAURANT IS NOT THERE YET -- a fresh database, CI, or a production
deploy that runs before anyone has onboarded Jr's Corner -- this loads
nothing and says so. A migration runs once per database and will not come
back around later, so the catch-up is to ask for it again, in the migrate
job, once the tenant exists:

    alembic downgrade 0046 && alembic upgrade head

That is a safe thing to say about a content migration and would not be about
most: the downgrade below removes nothing, and everything the upgrade does is
matched by name and written only where it is missing. It is the whole of this
step rather than part of it -- scripts/import_menu.py loads the menu and its
dish photographs, which is most of this but not the home banners or the
favourites row.

THE SNAPSHOT IS FROZEN. The menu this loads is the file beside this one,
data/0047_jrs_corner_menu.json, which is a copy of menus/jrs-corner.json
taken when this revision was written and never edited afterwards. The two
are separate on purpose. menus/jrs-corner.json is the live, editable menu --
the thing the importer loads and somebody changes when a price changes. If
this migration read that file instead, what revision 0047 does to a database
would depend on when the database was created, and two deployments at the
same revision would hold different menus. A migration has to mean one thing.

RE-APPLYING IS SAFE. Everything is matched by name and updated in place, so
nothing here produces a second copy of anything -- which matters less for
alembic, which runs it once, than for the fact that the same rows may already
have been loaded by seed.py or the importer on the database this meets.

ON PHOTOGRAPHS. The pictures are files, not rows, and they go wherever
IMAGES_DIR points -- so this step needs the images volume mounted on the
container that runs the migration, which a job that only ever ran `alembic
upgrade head` had no reason to have. docker-compose.yml now mounts it on the
migrate service, and the deploy command ci.yml prints carries the same -v.

If the directory cannot be written anyway, the menu still loads and every
photo is left unset rather than recorded as a key pointing at a file that is
not there: a dish with no picture reads fine, a broken image does not. The
summary printed at the end says which happened and how to come back for
them.

A photo is also only ever written onto a row that has none, so this can never
overwrite a picture the restaurant uploaded itself.

DOWNGRADE DOES NOT DELETE THE MENU. By the time anyone rolls back, the menu
may have been edited, reordered, priced and photographed through the portal,
and orders, favourites and combo slots point at the items. Undoing a schema
revision is not a reason to delete a restaurant's menu, so the downgrade is
deliberately empty -- there is nothing structural here to undo, and removing
an item is a decision for the portal, where the consequences are shown.

Revision ID: 0047
Revises: 0046
"""

import io
import json
import os
import uuid
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

# The frozen menu, and the photographs it names.
SNAPSHOT = Path(__file__).resolve().parent / "data" / "0047_jrs_corner_menu.json"
ASSETS = Path(__file__).resolve().parents[2] / "app" / "seed_assets"

# Which restaurant this menu belongs to. Two spellings because the tenant
# behind the Jr's Corner location is named `spicehouse` on every database
# seeded so far, while a tenant onboarded by hand for the one real store is
# the likelier `jrs-corner`. Whichever exists gets the menu; normally that is
# exactly one of them, and on a database with neither this does nothing.
TARGET_SLUGS = ("spicehouse", "jrs-corner")

# The home page's rotating hero. Not in the menu file -- a banner is
# storefront decoration rather than something anybody orders -- and small
# enough to carry here as the frozen data it is.
BANNERS = [
    ("banner-burger", "Fresh from our kitchen", "Never frozen, cooked when you order."),
    ("banner-fries", "Cut here, every day", "Whole potatoes, into the fryer, onto your tray."),
    ("banner-shake", "Made with real ice cream", "Chocolate, vanilla, strawberry — or all three."),
]

# The row of dishes under the hero, by name. Names rather than ids for the
# same reason the menu file uses them: an id means nothing in another
# database. An item named here that the menu does not contain is skipped.
FAVOURITES = ("House favourites", ["Jr's Double-Double", "Jr's French Fries", "Jr's Shake"])

# The photograph on the location's card on the platform picker.
LOCATION_CARD = "hero-counter"

# The platform front page. 0044 built the table and left it empty, so the
# only thing that has ever filled it is seed.py -- which means a production
# database gets the picker with nothing in it, and zenoeats.com opens on four
# missing cards. These are the four, and this is platform data rather than
# any restaurant's: the door says "Jr's Corner", the tenant it opens is
# Spice House.
#
# `attach` is the one that opens a store. A location may only be OPEN if it
# opens something (0044's ck_locations_open_has_restaurant), so on a database
# where the tenant has not been onboarded yet this one is written as coming
# soon and promoted when this step is asked for again -- a card that says
# "not open yet" is a worse front page than the real one and a far better one
# than a card that goes nowhere.
LOCATIONS = [
    {
        "slug": "jrs-corner", "name": "Jr's Corner",
        "city": "Ardmore", "region": "Oklahoma",
        "address_line": "1500 Sam Noble Parkway, Ardmore, OK 73401-7154",
        "blurb": "Open daily, 10:30am to 1:00am",
        "status": "OPEN", "attach": True,
    },
    {
        "slug": "big-apple", "name": "Big Apple",
        "city": "New York", "region": "New York",
        "address_line": None, "blurb": "Opening next spring",
        "status": "COMING_SOON", "attach": False,
    },
    {
        "slug": "rainforest", "name": "Rainforest",
        "city": "Portland", "region": "Oregon",
        "address_line": None, "blurb": "We are building the kitchen now",
        "status": "COMING_SOON", "attach": False,
    },
    {
        "slug": "sunset-strip", "name": "Sunset Strip",
        "city": "Los Angeles", "region": "California",
        "address_line": None, "blurb": "Signing the lease",
        "status": "COMING_SOON", "attach": False,
    },
]

# Every table touched below. FORCE ROW LEVEL SECURITY has to come off around
# the work and go back on afterwards: this role owns these tables, FORCE makes
# the policies apply to the owner too, and the policies name zenoeats_app and
# zenoeats_system -- so anything run here matches no policy at all. 0004
# explains it at length; this is the same lift.
#
# It covers the reads as well as the writes, which is easy to get wrong: a
# SELECT under a policy that matches nothing does not raise, it returns no
# rows. `restaurants` is first in this list because the lookup that finds the
# store ran before the lift once and reported, perfectly calmly, that a
# restaurant sitting in the table did not exist.
CONTENT_TABLES = [
    "restaurants",
    "item_types", "modifier_groups", "modifier_options", "modifier_group_item_types",
    "menu_items", "item_modifier_groups", "item_included_options",
    "meals", "meal_items", "combos", "combo_slots", "combo_slot_items",
    "storefront_banners", "storefront_collections", "storefront_collection_items",
    "locations",
]

# What each kind of picture is stored at, which is not one number: a category
# circle 65 pixels across has no use for a 2000-pixel photograph. These match
# EDGE_BY_KIND in app/services/images.py at the time this was written, and are
# repeated rather than imported -- see _encode below.
EDGE_BY_KIND = {"banners": 2000, "items": 900, "categories": 420}
WEBP_QUALITY = 82


# ----------------------------------------------------------- photographs ---

def _encode(data: bytes, kind: str) -> bytes:
    """One committed asset, re-encoded to the size that kind is shown at.

    Pillow is imported here rather than at module scope because alembic
    imports every file in this directory to build the revision map, and a
    migration that cannot even be listed without an image library installed
    is a migration that breaks `alembic history` on a machine that only wants
    to read it.

    This repeats app/services/images.py rather than calling it, as every
    other revision in this directory repeats the SQL it needs rather than
    importing a model. A version file has to keep doing the same thing
    forever; the image pipeline is free to change, and the day it grows a
    second argument or a different default size is not the day an applied
    revision should start meaning something else.
    """
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as source:
        icc_profile = source.info.get("icc_profile")
        # Applies the orientation tag and drops it, so the picture is the
        # right way up once the metadata it depended on is gone.
        picture = ImageOps.exif_transpose(source)

    # Every asset here is an opaque photograph. RGB, so none of them carries
    # an alpha channel nobody asked for into the stored file.
    picture = picture.convert("RGB")
    edge = EDGE_BY_KIND[kind]
    picture.thumbnail((edge, edge), Image.Resampling.LANCZOS)

    out = io.BytesIO()
    save_args: dict = {"quality": WEBP_QUALITY, "method": 4, "exif": b"", "xmp": b""}
    if icc_profile:
        # Kept: it says nothing about anyone, and dropping it shifts the
        # colours of every photograph taken on a recent phone.
        save_args["icc_profile"] = icc_profile
    picture.save(out, "WEBP", **save_args)
    return out.getvalue()


class Photos:
    """Writes the pictures, or explains why it could not.

    Holds the one decision that has to be made once -- whether this container
    can write to the images directory at all -- so that thirteen dishes do
    not each discover it separately and so the summary can say it plainly.
    """

    def __init__(self) -> None:
        # The same default as app.config: <repo>/images, which is where a
        # native API reads and writes. Compose and production both set
        # IMAGES_DIR to wherever that folder is mounted in the container.
        self.root = Path(os.environ.get("IMAGES_DIR") or Path(__file__).resolve().parents[3] / "images")
        self.written = 0
        self.unavailable: str | None = None

        try:
            self.root.mkdir(parents=True, exist_ok=True)
            probe = self.root / f".write-probe-{uuid.uuid4().hex}"
            probe.write_bytes(b"")
            probe.unlink()
        except OSError as exc:
            self.unavailable = f"{self.root} is not writable ({exc.strerror})"

    def put(self, restaurant_id, asset: str, kind: str) -> str | None:
        """A stored key, or None -- which means the row keeps no photo.

        None is returned rather than raised for a missing asset or an
        unwritable volume, because a menu without pictures is a working menu
        and a key with no file behind it is a broken storefront.
        """
        if self.unavailable:
            return None

        source = ASSETS / f"{asset}.webp"
        if not source.is_file():
            print(f"    ! no asset {asset}.webp; leaving that photo unset")
            return None

        # A fresh random key per file, exactly as app/services/images.py
        # mints one, so a stored image is immutable and a browser may cache
        # it for as long as it likes.
        key = f"restaurants/{restaurant_id}/{kind}/{uuid.uuid4().hex}.webp"
        path = self.root / key
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            body = _encode(source.read_bytes(), kind)
            # Written aside and moved into place, so a request for the key
            # never reads half a file.
            partial = path.with_name(path.name + ".part")
            partial.write_bytes(body)
            os.replace(partial, path)
        except OSError as exc:
            self.unavailable = f"{self.root} stopped accepting writes ({exc.strerror})"
            return None

        self.written += 1
        return key


# ------------------------------------------------------------- the loader ---

def _named(conn, restaurant_id, table: str) -> dict:
    """name -> id for a restaurant's live rows in one of the menu tables."""
    rows = conn.execute(
        text(f"SELECT id, name FROM {table} WHERE restaurant_id = :r AND deleted_at IS NULL"),
        {"r": restaurant_id},
    ).all()
    return {row.name: row.id for row in rows}


def _locations(conn, restaurant_id) -> dict:
    """The four cards on the platform front page.

    Matched by slug, which is what the URL already uses and what the unique
    constraint is on. Two things are deliberately one-way:

    A location that is open is never closed again here. If the tenant behind
    it is missing the day this runs -- soft-deleted, renamed, not yet
    onboarded -- that is not a reason to take a live store off the front page
    while its storefront is still serving.

    And a location that already points at a restaurant keeps pointing at it.
    Re-pointing a door at a different kitchen is a decision, not a side
    effect of a deploy.
    """
    counts = {"created": 0, "updated": 0}
    for order, spec in enumerate(LOCATIONS):
        existing = conn.execute(
            text("SELECT id, restaurant_id FROM locations WHERE slug = :s"),
            {"s": spec["slug"]},
        ).first()
        attach = restaurant_id if spec["attach"] else None
        opens_something = attach is not None or (existing and existing.restaurant_id)
        status = spec["status"] if spec["status"] != "OPEN" or opens_something else "COMING_SOON"

        values = {
            "n": spec["name"], "c": spec["city"], "g": spec["region"],
            "a": spec["address_line"], "b": spec["blurb"],
            "st": status, "o": order, "r": attach,
        }
        if existing is None:
            conn.execute(
                text(
                    "INSERT INTO locations (slug, name, city, region, address_line, blurb, "
                    "status, restaurant_id, sort_order) "
                    "VALUES (:s, :n, :c, :g, :a, :b, :st, :r, :o)"
                ),
                {**values, "s": spec["slug"]},
            )
            counts["created"] += 1
        else:
            conn.execute(
                text(
                    "UPDATE locations SET name = :n, city = :c, region = :g, "
                    "address_line = :a, blurb = :b, sort_order = :o, "
                    "restaurant_id = COALESCE(restaurant_id, :r), "
                    "status = CASE WHEN status = 'OPEN' THEN status ELSE :st END, "
                    "updated_at = now() WHERE id = :i"
                ),
                {**values, "i": existing.id},
            )
            counts["updated"] += 1
    return counts


def _load(conn, restaurant_id, menu: dict, photos: Photos) -> dict:
    """The whole menu onto one restaurant. Returns what it did, for printing."""
    counts = {"created": 0, "updated": 0}

    def tally(existing) -> None:
        counts["updated" if existing else "created"] += 1

    # --- headings ---------------------------------------------------------
    # First, because an item cannot be filed under a type that does not exist.
    types = _named(conn, restaurant_id, "item_types")
    for spec in menu.get("item_types", []):
        name = spec["name"]
        tally(name in types)
        if name in types:
            conn.execute(
                text("UPDATE item_types SET sort_order = :s, updated_at = now() WHERE id = :i"),
                {"s": spec.get("sort_order", 0), "i": types[name]},
            )
        else:
            types[name] = conn.execute(
                text(
                    "INSERT INTO item_types (id, restaurant_id, name, sort_order) "
                    "VALUES (gen_random_uuid(), :r, :n, :s) RETURNING id"
                ),
                {"r": restaurant_id, "n": name, "s": spec.get("sort_order", 0)},
            ).scalar_one()

    # Parents in a second pass: the file may list a child before its parent.
    for spec in menu.get("item_types", []):
        parent = spec.get("parent")
        conn.execute(
            text("UPDATE item_types SET parent_id = :p, updated_at = now() WHERE id = :i"),
            {"p": types[parent] if parent in types else None, "i": types[spec["name"]]},
        )

    # --- modifier groups and their options --------------------------------
    groups = _named(conn, restaurant_id, "modifier_groups")
    options: dict = {}
    for spec in menu.get("modifier_groups", []):
        name = spec["name"]
        tally(name in groups)
        if name in groups:
            conn.execute(
                text(
                    "UPDATE modifier_groups SET selection_type = :t, is_required = :q, "
                    "min_select = :lo, max_select = :hi, updated_at = now() WHERE id = :i"
                ),
                {
                    "t": spec["selection_type"], "q": spec["is_required"],
                    "lo": spec["min_select"], "hi": spec["max_select"], "i": groups[name],
                },
            )
        else:
            groups[name] = conn.execute(
                text(
                    "INSERT INTO modifier_groups "
                    "(id, restaurant_id, name, selection_type, is_required, min_select, max_select) "
                    "VALUES (gen_random_uuid(), :r, :n, :t, :q, :lo, :hi) RETURNING id"
                ),
                {
                    "r": restaurant_id, "n": name, "t": spec["selection_type"],
                    "q": spec["is_required"], "lo": spec["min_select"], "hi": spec["max_select"],
                },
            ).scalar_one()
        group_id = groups[name]

        # Which headings offer the group. Rebuilt rather than merged: it is a
        # join table with no history, and a row left behind here offers a fry
        # style on a milkshake.
        conn.execute(text("DELETE FROM modifier_group_item_types WHERE group_id = :g"),
                     {"g": group_id})
        for type_name in spec.get("offered_on", []):
            if type_name in types:
                conn.execute(
                    text(
                        "INSERT INTO modifier_group_item_types "
                        "(id, restaurant_id, group_id, item_type_id) "
                        "VALUES (gen_random_uuid(), :r, :g, :t)"
                    ),
                    {"r": restaurant_id, "g": group_id, "t": types[type_name]},
                )

        existing_options = {
            row.name: row.id
            for row in conn.execute(
                text(
                    "SELECT id, name FROM modifier_options "
                    "WHERE group_id = :g AND deleted_at IS NULL"
                ),
                {"g": group_id},
            ).all()
        }
        for order, opt in enumerate(spec.get("options", [])):
            values = {
                "d": opt.get("price_delta_minor", 0),
                "c": opt.get("calories_delta"),
                "s": opt.get("sort_order", order),
            }
            if opt["name"] in existing_options:
                option_id = existing_options[opt["name"]]
                conn.execute(
                    text(
                        "UPDATE modifier_options SET price_delta_minor = :d, calories_delta = :c, "
                        "sort_order = :s, updated_at = now() WHERE id = :i"
                    ),
                    {**values, "i": option_id},
                )
            else:
                option_id = conn.execute(
                    text(
                        "INSERT INTO modifier_options "
                        "(id, restaurant_id, group_id, name, price_delta_minor, calories_delta, sort_order) "
                        "VALUES (gen_random_uuid(), :r, :g, :n, :d, :c, :s) RETURNING id"
                    ),
                    {**values, "r": restaurant_id, "g": group_id, "n": opt["name"]},
                ).scalar_one()
            options[(name, opt["name"])] = option_id

    # --- the dishes --------------------------------------------------------
    items = _named(conn, restaurant_id, "menu_items")
    for spec in menu.get("items", []):
        name = spec["name"]
        tally(name in items)
        values = {
            "t": types[spec["item_type"]],
            "d": spec.get("description"),
            "p": spec["price_minor"],
            "c": spec.get("calories"),
            "u": spec.get("currency", "USD"),
            "s": spec.get("sort_order", 0),
        }
        if name in items:
            conn.execute(
                text(
                    "UPDATE menu_items SET item_type_id = :t, description = :d, "
                    "base_price_minor = :p, calories = :c, currency = :u, sort_order = :s, "
                    "updated_at = now() WHERE id = :i"
                ),
                {**values, "i": items[name]},
            )
        else:
            items[name] = conn.execute(
                text(
                    "INSERT INTO menu_items (id, restaurant_id, name, item_type_id, description, "
                    "base_price_minor, calories, currency, sort_order) "
                    "VALUES (gen_random_uuid(), :r, :n, :t, :d, :p, :c, :u, :s) RETURNING id"
                ),
                {**values, "r": restaurant_id, "n": name},
            ).scalar_one()
        item_id = items[name]

        if spec.get("photo"):
            _photograph(conn, photos, restaurant_id, "menu_items", item_id, spec["photo"], "items")

        conn.execute(text("DELETE FROM item_modifier_groups WHERE item_id = :i"), {"i": item_id})
        for order, group_name in enumerate(spec.get("modifier_groups", [])):
            if group_name in groups:
                conn.execute(
                    text(
                        "INSERT INTO item_modifier_groups "
                        "(id, restaurant_id, item_id, group_id, sort_order) "
                        "VALUES (gen_random_uuid(), :r, :i, :g, :s)"
                    ),
                    {"r": restaurant_id, "i": item_id, "g": groups[group_name], "s": order},
                )

        # What the dish comes with before anyone changes anything. Matched
        # within the groups the dish actually offers, so "Cheese" on a burger
        # cannot resolve to the "Cheese" of a fries group.
        conn.execute(text("DELETE FROM item_included_options WHERE item_id = :i"), {"i": item_id})
        offered = spec.get("modifier_groups", [])
        for option_name in spec.get("comes_with", []):
            option_id = next(
                (oid for (g, o), oid in options.items() if o == option_name and g in offered),
                None,
            )
            if option_id is not None:
                conn.execute(
                    text(
                        "INSERT INTO item_included_options (id, restaurant_id, item_id, option_id) "
                        "VALUES (gen_random_uuid(), :r, :i, :o)"
                    ),
                    {"r": restaurant_id, "i": item_id, "o": option_id},
                )

    # Heading photographs, once the headings exist.
    for spec in menu.get("item_types", []):
        if spec.get("photo"):
            _photograph(
                conn, photos, restaurant_id, "item_types",
                types[spec["name"]], spec["photo"], "categories",
            )

    # --- meal periods -------------------------------------------------------
    meals = _named(conn, restaurant_id, "meals")
    for spec in menu.get("meals", []):
        name = spec["name"]
        tally(name in meals)
        if name in meals:
            conn.execute(
                text("UPDATE meals SET sort_order = :s, updated_at = now() WHERE id = :i"),
                {"s": spec.get("sort_order", 0), "i": meals[name]},
            )
        else:
            meals[name] = conn.execute(
                text(
                    "INSERT INTO meals (id, restaurant_id, name, sort_order) "
                    "VALUES (gen_random_uuid(), :r, :n, :s) RETURNING id"
                ),
                {"r": restaurant_id, "n": name, "s": spec.get("sort_order", 0)},
            ).scalar_one()

        conn.execute(text("DELETE FROM meal_items WHERE meal_id = :m"), {"m": meals[name]})
        for order, item_name in enumerate(spec.get("items", [])):
            if item_name in items:
                conn.execute(
                    text(
                        "INSERT INTO meal_items (id, restaurant_id, meal_id, item_id, sort_order) "
                        "VALUES (gen_random_uuid(), :r, :m, :i, :s)"
                    ),
                    {"r": restaurant_id, "m": meals[name], "i": items[item_name], "s": order},
                )

    # --- meal deals ---------------------------------------------------------
    combos = _named(conn, restaurant_id, "combos")
    for spec in menu.get("combos", []):
        name = spec["name"]
        tally(name in combos)
        values = {
            "m": meals.get(spec.get("meal")),
            "d": spec.get("description"),
            "k": spec["discount_kind"],
            "v": spec["discount_value"],
            "s": spec.get("sort_order", 0),
        }
        if name in combos:
            conn.execute(
                text(
                    "UPDATE combos SET meal_id = :m, description = :d, discount_kind = :k, "
                    "discount_value = :v, sort_order = :s, updated_at = now() WHERE id = :i"
                ),
                {**values, "i": combos[name]},
            )
        else:
            combos[name] = conn.execute(
                text(
                    "INSERT INTO combos (id, restaurant_id, name, meal_id, description, "
                    "discount_kind, discount_value, sort_order) "
                    "VALUES (gen_random_uuid(), :r, :n, :m, :d, :k, :v, :s) RETURNING id"
                ),
                {**values, "r": restaurant_id, "n": name},
            ).scalar_one()
        combo_id = combos[name]

        # Slots are rebuilt. They are the deal's shape rather than something
        # else points at, and matching them by position would quietly
        # reassign every choice the moment a slot is inserted in the middle.
        conn.execute(
            text(
                "DELETE FROM combo_slot_items WHERE slot_id IN "
                "(SELECT id FROM combo_slots WHERE combo_id = :c)"
            ),
            {"c": combo_id},
        )
        conn.execute(text("DELETE FROM combo_slots WHERE combo_id = :c"), {"c": combo_id})
        for order, slot_spec in enumerate(spec.get("slots", [])):
            slot_id = conn.execute(
                text(
                    "INSERT INTO combo_slots (id, restaurant_id, combo_id, item_type_id, sort_order) "
                    "VALUES (gen_random_uuid(), :r, :c, :t, :s) RETURNING id"
                ),
                {
                    "r": restaurant_id, "c": combo_id,
                    "t": types[slot_spec["item_type"]], "s": order,
                },
            ).scalar_one()
            for position, choice in enumerate(slot_spec.get("choices", [])):
                if choice in items:
                    conn.execute(
                        text(
                            "INSERT INTO combo_slot_items "
                            "(id, restaurant_id, slot_id, item_id, sort_order) "
                            "VALUES (gen_random_uuid(), :r, :s, :i, :o)"
                        ),
                        {"r": restaurant_id, "s": slot_id, "i": items[choice], "o": position},
                    )

    _storefront(conn, restaurant_id, items, photos)

    # What the restaurant has that the file did not mention. Reported, never
    # removed: orders and favourites point at these rows, and a name missing
    # from a menu file is as likely to be a typo as a decision.
    named = {spec["name"] for spec in menu.get("items", [])}
    counts["orphans"] = sorted(name for name in items if name not in named)
    return counts


def _photograph(conn, photos: Photos, restaurant_id, table: str, row_id, asset: str, kind: str) -> None:
    """Attach a picture to a row that has none.

    The guard is what makes this safe to apply to a live restaurant: a dish
    whose photograph was replaced through the portal keeps the replacement.
    """
    held = conn.execute(
        text(f"SELECT image_path FROM {table} WHERE id = :i"), {"i": row_id}
    ).scalar()
    if held:
        return
    key = photos.put(restaurant_id, asset, kind)
    if key:
        conn.execute(
            text(f"UPDATE {table} SET image_path = :k WHERE id = :i"), {"k": key, "i": row_id}
        )


def _storefront(conn, restaurant_id, items: dict, photos: Photos) -> None:
    """The home page: the rotating hero, the favourites row, the door photo.

    Each piece is written only when the restaurant has none of it. These are
    things a manager arranges in the portal, and a migration that re-asserted
    them would undo that arrangement -- or, worse, bring back a banner
    somebody deliberately took down.
    """
    has_banner = conn.execute(
        text("SELECT 1 FROM storefront_banners WHERE restaurant_id = :r LIMIT 1"),
        {"r": restaurant_id},
    ).first()
    if not has_banner:
        for order, (asset, headline, subline) in enumerate(BANNERS):
            key = photos.put(restaurant_id, asset, "banners")
            if not key:
                # image_path is NOT NULL on this table: a banner is a
                # photograph with words on it, so there is no row to write
                # without one.
                continue
            conn.execute(
                text(
                    "INSERT INTO storefront_banners (id, restaurant_id, image_path, headline, "
                    "subline, cta_label, cta_target_kind, sort_order) "
                    "VALUES (gen_random_uuid(), :r, :k, :h, :s, 'Explore the menu', 'menu', :o)"
                ),
                {"r": restaurant_id, "k": key, "h": headline, "s": subline, "o": order},
            )

    title, wanted = FAVOURITES
    has_collection = conn.execute(
        text("SELECT 1 FROM storefront_collections WHERE restaurant_id = :r LIMIT 1"),
        {"r": restaurant_id},
    ).first()
    if not has_collection:
        chosen = [items[name] for name in wanted if name in items]
        if chosen:
            collection_id = conn.execute(
                text(
                    "INSERT INTO storefront_collections (id, restaurant_id, title, sort_order) "
                    "VALUES (gen_random_uuid(), :r, :t, 0) RETURNING id"
                ),
                {"r": restaurant_id, "t": title},
            ).scalar_one()
            for order, item_id in enumerate(chosen):
                conn.execute(
                    text(
                        "INSERT INTO storefront_collection_items "
                        "(collection_id, item_id, restaurant_id, sort_order) "
                        "VALUES (:c, :i, :r, :s)"
                    ),
                    {"c": collection_id, "i": item_id, "r": restaurant_id, "s": order},
                )

    # The card on the platform picker. Keyed on the restaurant rather than a
    # location slug, because the row that opens this tenant is the one whose
    # picture this is -- and on a database where no location points here yet,
    # this updates nothing.
    for row in conn.execute(
        text(
            "SELECT id FROM locations WHERE restaurant_id = :r AND image_path IS NULL"
        ),
        {"r": restaurant_id},
    ).all():
        key = photos.put(restaurant_id, LOCATION_CARD, "banners")
        if key:
            conn.execute(
                text("UPDATE locations SET image_path = :k, updated_at = now() WHERE id = :i"),
                {"k": key, "i": row.id},
            )


# ---------------------------------------------------------------- alembic ---

def _force_rls(conn, on: bool) -> None:
    verb = "FORCE" if on else "NO FORCE"
    for table in CONTENT_TABLES:
        conn.execute(text(f"ALTER TABLE {table} {verb} ROW LEVEL SECURITY"))


def _apply(conn) -> None:
    """Everything this revision does, against a given connection.

    Separate from upgrade() only so that tests/test_menu_migration.py can run
    it on a transaction it rolls back afterwards. A migration whose only entry
    point is op.get_bind() can be tested by applying it to a database and
    looking, which is a slow way to find out that a menu of thirteen dishes
    loaded as twelve.
    """
    photos: Photos | None = None

    # Around the lookups too, not just the writes. See CONTENT_TABLES.
    _force_rls(conn, False)
    try:
        # Ordered by TARGET_SLUGS so that which tenant the open location
        # points at is decided by this file and not by insertion order.
        found = {
            row.slug: row
            for row in conn.execute(
                text(
                    "SELECT id, slug, name FROM restaurants "
                    "WHERE slug = ANY(:slugs) AND deleted_at IS NULL"
                ),
                {"slugs": list(TARGET_SLUGS)},
            ).all()
        }
        targets = [found[slug] for slug in TARGET_SLUGS if slug in found]

        # The front page first, and whether or not there is a tenant behind
        # it: four cards that say where this is going beat an empty picker,
        # and the menu below needs the location row to exist before it can
        # put a photograph on its card.
        counts = _locations(conn, targets[0].id if targets else None)
        print(f"0047: locations -- {counts['created']} created, {counts['updated']} updated")

        if not targets:
            # Expected on a fresh database and in CI, and possible in
            # production if the deploy runs before anyone has onboarded the
            # store. Not a failure: a migration cannot wait for a tenant.
            print(
                "0047: no restaurant with slug "
                + " or ".join(repr(s) for s in TARGET_SLUGS)
                + "; no menu loaded, and Jr's Corner is listed as coming soon."
            )
            print(
                "0047: once the tenant exists, ask for this step again in the "
                "migrate job: alembic downgrade 0046 && alembic upgrade head"
            )
            return

        menu = json.loads(SNAPSHOT.read_text())
        photos = Photos()
        for target in targets:
            print(f"0047: loading the Jr's Corner menu into {target.name} ({target.slug})")
            counts = _load(conn, target.id, menu, photos)
            print(f"0047:   {counts['created']} created, {counts['updated']} updated")
            for name in counts["orphans"]:
                print(f"0047:   left alone, not in the menu file: {name}")
    finally:
        _force_rls(conn, True)

    if photos is None:
        return

    if photos.unavailable:
        print(f"0047: no photographs written -- {photos.unavailable}")
        print(
            "0047: the menu loaded without them, and the home banners were skipped "
            "(a banner is a photograph with words on it, so there is no row to write "
            "without one)."
        )
        print(
            "0047: give the migrate job the images volume and ask for this step "
            "again: alembic downgrade 0046 && alembic upgrade head"
        )
    else:
        print(f"0047: {photos.written} photographs written to {photos.root}")


def upgrade() -> None:
    _apply(op.get_bind())


def downgrade() -> None:
    """Deliberately nothing. See the module docstring: this revision adds no
    schema, and a rollback is not a reason to delete a restaurant's menu."""
