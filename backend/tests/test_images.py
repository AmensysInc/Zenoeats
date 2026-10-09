"""Menu images: what an upload has to be, whose it is, and when a file goes.

Three promises, each with its own way of failing quietly.

What is stored is a picture and nothing else. Every upload is decoded and
written back out as WebP, so a file that only claims to be an image never
reaches a customer's browser, and a phone photo's location never reaches
the storefront.

A key belongs to one restaurant. Row-level security protects rows, not a
folder, so the check that one restaurant cannot attach another's picture
lives in code and is pinned here.

A file is deleted only after the change that stopped using it is saved, and
only when nothing else still shows it. The opposite order would leave menus
pointing at pictures that are gone.
"""

import io
import pathlib
import uuid
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.api.v1.restaurant import (
    ItemUpdateIn, ModifierOptionUpdateIn, update_item, update_modifier_option,
    upload_image,
)
from app.config import settings
from app.core import errors
from app.services import images
from app.services.images import ImageKind

RESTAURANT = uuid.uuid4()
ANOTHER = uuid.uuid4()


@pytest.fixture(autouse=True)
def images_dir(tmp_path, monkeypatch):
    """Every test writes into its own folder, never the repository's."""
    monkeypatch.setattr(settings, "IMAGES_DIR", tmp_path)
    monkeypatch.setattr(settings, "IMAGES_PUBLIC_BASE", "/images")
    return tmp_path


def encoded(fmt="JPEG", size=(64, 48), mode="RGB", colour=(200, 40, 20), **save):
    buffer = io.BytesIO()
    Image.new(mode, size, colour).save(buffer, fmt, **save)
    return buffer.getvalue()


def stored(restaurant=RESTAURANT, kind=ImageKind.ITEMS):
    """A key that exists on disk, as if it had just been uploaded."""
    key = images.new_key(restaurant, kind)
    images.storage().save(key, images.process(encoded()))
    return key


# ------------------------------------------------------------ processing ---

@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_every_accepted_format_comes_out_as_webp(fmt):
    out = images.process(encoded(fmt))

    with Image.open(io.BytesIO(out)) as picture:
        assert picture.format == "WEBP"
        assert picture.size == (64, 48)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not an image at all",
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b"<html><script>alert(1)</script></html>",
        encoded("GIF", mode="P", colour=1),
        encoded("BMP"),
        # A real JPEG header with the body cut off.
        encoded("JPEG", size=(400, 400))[:200],
    ],
    ids=["empty", "text", "svg", "html", "gif", "bmp", "truncated-jpeg"],
)
def test_anything_that_is_not_a_readable_jpeg_png_or_webp_is_refused(data):
    with pytest.raises(errors.ApiError) as refused:
        images.process(data)
    assert refused.value.status_code == 422


def test_an_upload_over_the_byte_limit_is_refused_before_it_is_decoded(monkeypatch):
    monkeypatch.setattr(images, "MAX_UPLOAD_BYTES", 10)

    with pytest.raises(errors.ApiError) as refused:
        images.process(encoded())
    assert refused.value.status_code == 413


def test_an_image_declaring_too_many_pixels_is_refused_from_its_header(monkeypatch):
    """A tiny file can claim enormous dimensions and ask for gigabytes to open.
    The claim is read and refused without decoding anything."""
    monkeypatch.setattr(images, "MAX_PIXELS", 64 * 48 - 1)

    with pytest.raises(errors.ApiError):
        images.process(encoded())


def test_a_large_photo_is_shrunk_to_the_longest_edge_keeping_its_shape():
    edge = images.EDGE_BY_KIND["items"]
    out = images.process(encoded(size=(3200, 1600)))

    with Image.open(io.BytesIO(out)) as picture:
        assert picture.size == (edge, edge // 2)


@pytest.mark.parametrize("kind", list(images.ImageKind))
def test_every_kind_is_stored_at_the_size_it_is_shown_at(kind):
    """One stored size per image, so it has to be the right one per kind.

    A category circle is 65 pixels across and a banner is the width of the
    window. Storing both at the same edge meant the circle downloaded a
    photograph fifteen times larger than it could use -- on the page a QR
    code opens, on a phone, on restaurant wifi.
    """
    edge = images.EDGE_BY_KIND[kind.value]
    out = images.process(encoded(size=(4000, 4000)), kind)

    with Image.open(io.BytesIO(out)) as picture:
        assert max(picture.size) == edge, f"{kind.value} stored at the wrong size"


def test_a_thumbnail_kind_is_stored_smaller_than_a_hero_kind():
    """The ordering is the point, and it is what a future edit could break."""
    by_kind = images.EDGE_BY_KIND
    assert by_kind["options"] <= by_kind["categories"] < by_kind["items"] < by_kind["banners"]


def test_metadata_is_dropped_including_where_the_photo_was_taken():
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"          # Make
    gps = exif.get_ifd(0x8825)
    gps[2] = (51.0, 30.0, 0.0)           # GPSLatitude
    gps[4] = (0.0, 7.0, 0.0)             # GPSLongitude
    source = encoded(exif=exif.tobytes())
    with Image.open(io.BytesIO(source)) as check:
        assert check.getexif()           # the fixture really does carry some

    out = images.process(source)

    with Image.open(io.BytesIO(out)) as picture:
        assert not picture.getexif()
        assert "exif" not in picture.info
        assert "xmp" not in picture.info


def test_a_sideways_phone_photo_is_turned_upright_before_its_tag_is_dropped():
    """Orientation 6 means "rotate 90 degrees to display". Dropping the tag
    without applying it would publish every portrait photo on its side."""
    exif = Image.Exif()
    exif[0x0112] = 6
    out = images.process(encoded(size=(80, 40), exif=exif.tobytes()))

    with Image.open(io.BytesIO(out)) as picture:
        assert picture.size == (40, 80)


def test_transparency_survives():
    out = images.process(encoded("PNG", mode="RGBA", colour=(0, 0, 0, 0)))

    with Image.open(io.BytesIO(out)) as picture:
        assert picture.mode == "RGBA"
        assert picture.getpixel((0, 0))[3] == 0


# ------------------------------------------------------------------ keys ---

def test_a_new_key_belongs_to_its_restaurant_and_kind_only():
    key = images.new_key(RESTAURANT, ImageKind.ITEMS)

    assert key.startswith(f"restaurants/{RESTAURANT}/items/")
    assert images.belongs_to(key, RESTAURANT, ImageKind.ITEMS)
    assert not images.belongs_to(key, ANOTHER, ImageKind.ITEMS)
    assert not images.belongs_to(key, RESTAURANT, ImageKind.OPTIONS)


def test_two_uploads_never_share_a_key():
    assert images.new_key(RESTAURANT, ImageKind.ITEMS) != images.new_key(
        RESTAURANT, ImageKind.ITEMS
    )


@pytest.mark.parametrize(
    "key",
    [
        f"restaurants/{RESTAURANT}/items/../../{ANOTHER}/items/{'a' * 32}.webp",
        f"restaurants/{RESTAURANT}/items/{'a' * 32}.webp/../../../../secret",
        f"/restaurants/{RESTAURANT}/items/{'a' * 32}.webp",
        f"restaurants/{RESTAURANT}/items/{'a' * 32}.svg",
        f"restaurants/{RESTAURANT}/items/{'A' * 32}.webp",
        f"restaurants/{RESTAURANT}/items/{'a' * 32}.webp\n",
        f"restaurants/{RESTAURANT}/items/photo.webp",
        "https://evil.example/picture.webp",
    ],
    ids=["climb", "suffix-climb", "absolute", "svg", "uppercase", "newline", "named", "url"],
)
def test_anything_not_shaped_exactly_like_a_key_is_not_one(key):
    assert not images.belongs_to(key, RESTAURANT, ImageKind.ITEMS)
    assert not images.storage().exists(key)
    with pytest.raises(ValueError):
        images.storage().save(key, b"x")


# --------------------------------------------------------------- storage ---

def test_storage_saves_serves_and_deletes_under_the_images_folder(images_dir):
    key = images.new_key(RESTAURANT, ImageKind.OPTIONS)
    store = images.storage()

    store.save(key, b"picture")

    assert (images_dir / key).read_bytes() == b"picture"
    assert store.exists(key)
    assert images.image_url(key) == f"/images/{key}"
    assert not list(images_dir.rglob("*.part"))

    store.delete(key)
    assert not store.exists(key)
    store.delete(key)  # already gone is not an error


def test_the_public_base_is_what_moves_when_storage_does(monkeypatch):
    monkeypatch.setattr(settings, "IMAGES_PUBLIC_BASE", "https://images.example.com/")
    key = images.new_key(RESTAURANT, ImageKind.ITEMS)

    assert images.image_url(key) == f"https://images.example.com/{key}"
    assert images.image_url(None) is None


def test_purging_a_restaurants_images_leaves_everyone_elses():
    mine = stored(RESTAURANT)
    theirs = stored(ANOTHER)

    images.storage().delete_restaurant(RESTAURANT)

    assert not images.storage().exists(mine)
    assert images.storage().exists(theirs)
    images.storage().delete_restaurant(RESTAURANT)  # nothing left is not an error


# --------------------------------------------------------------- attaching ---

def test_null_is_accepted_because_it_is_how_a_picture_is_taken_off():
    assert images.accept(None, RESTAURANT, ImageKind.ITEMS) is None


def test_a_key_that_was_never_uploaded_is_refused():
    with pytest.raises(errors.ApiError):
        images.accept(images.new_key(RESTAURANT, ImageKind.ITEMS), RESTAURANT, ImageKind.ITEMS)


def test_another_restaurants_real_image_is_refused_like_a_missing_one():
    theirs = stored(ANOTHER)

    with pytest.raises(errors.ApiError) as refused:
        images.accept(theirs, RESTAURANT, ImageKind.ITEMS)
    assert "could not be found" in refused.value.detail["message"]


def test_an_option_image_cannot_be_attached_to_an_item():
    option_picture = stored(kind=ImageKind.OPTIONS)

    with pytest.raises(errors.ApiError):
        images.accept(option_picture, RESTAURANT, ImageKind.ITEMS)


# --------------------------------------------------------------- releasing ---

class CountingSession(Session):
    """A real Session, so its commit and rollback fire the real events, with
    the reference count answered rather than queried.

    Begun explicitly, as the request session is: a rollback with no
    transaction open has nothing to roll back and fires nothing, which is not
    a situation an endpoint is ever in."""

    def __init__(self, held):
        super().__init__()
        self.held = held
        self.begin()

    def execute(self, statement, *args, **kwargs):
        return SimpleNamespace(scalar_one=lambda: self.held)


def test_a_released_image_is_deleted_only_once_the_change_is_committed():
    key = stored()
    db = CountingSession(held=0)

    images.release(db, key)
    assert images.storage().exists(key), "deleted before the change was saved"

    db.commit()
    assert not images.storage().exists(key)


def test_a_released_image_survives_a_rolled_back_change():
    key = stored()
    db = CountingSession(held=0)

    images.release(db, key)
    db.rollback()
    db.commit()

    assert images.storage().exists(key)


def test_an_image_another_row_still_shows_is_never_deleted():
    key = stored()
    db = CountingSession(held=1)

    images.release(db, key)
    db.commit()

    assert images.storage().exists(key)


# --------------------------------------------------------------- endpoints ---

class FakeItem:
    def __init__(self, image_path=None):
        self.id = uuid.uuid4()
        self.name = "Smash Burger"
        self.item_type_id = uuid.uuid4()
        self.description = None
        self.calories = None
        self.base_price_minor = 1095
        self.tax_exempt = False
        self.image_path = image_path
        self.price_delta_minor = 0
        self.calories_delta = None
        self.deleted_at = None


class FakeDb:
    def __init__(self, row):
        self.row = row

    def get(self, _model, row_id):
        return self.row if row_id == self.row.id else None


@pytest.fixture
def released(monkeypatch):
    """What each edit hands to release, in order."""
    calls = []
    monkeypatch.setattr(images, "release", lambda _db, key: calls.append(key))
    return calls


RESTAURANT_ROW = SimpleNamespace(id=RESTAURANT)


def test_uploading_stores_a_webp_under_the_restaurant_and_returns_its_key():
    upload = UploadFile(file=io.BytesIO(encoded("PNG")), filename="../../evil.svg")

    result = upload_image(ImageKind.ITEMS, upload, restaurant=RESTAURANT_ROW)

    key = result["image_path"]
    assert images.belongs_to(key, RESTAURANT, ImageKind.ITEMS)
    assert result["image_url"] == f"/images/{key}"
    with Image.open(settings.IMAGES_DIR / key) as picture:
        assert picture.format == "WEBP"


def test_uploading_something_that_is_not_a_photo_stores_nothing(images_dir):
    upload = UploadFile(file=io.BytesIO(b"<svg/>"), filename="menu.jpg")

    with pytest.raises(errors.ApiError):
        upload_image(ImageKind.ITEMS, upload, restaurant=RESTAURANT_ROW)
    assert not [p for p in images_dir.rglob("*") if p.is_file()]


def test_setting_an_items_picture_releases_the_one_it_replaces(released):
    old, new = stored(), stored()
    item = FakeItem(image_path=old)

    update_item(
        item.id, ItemUpdateIn(image_path=new), restaurant=RESTAURANT_ROW, db=FakeDb(item)
    )

    assert item.image_path == new
    assert released == [old]


def test_null_takes_an_items_picture_off(released):
    old = stored()
    item = FakeItem(image_path=old)

    update_item(
        item.id, ItemUpdateIn(image_path=None), restaurant=RESTAURANT_ROW, db=FakeDb(item)
    )

    assert item.image_path is None
    assert released == [old]


def test_an_edit_that_does_not_mention_the_picture_leaves_it_alone(released):
    """The partial-update rule, for the one field where getting it wrong
    deletes a file: a price edit must not read as "no picture"."""
    old = stored()
    item = FakeItem(image_path=old)

    update_item(
        item.id, ItemUpdateIn(base_price_minor=1195), restaurant=RESTAURANT_ROW,
        db=FakeDb(item),
    )

    assert item.image_path == old
    assert released == []


def test_resending_the_same_picture_releases_nothing(released):
    same = stored()
    item = FakeItem(image_path=same)

    update_item(
        item.id, ItemUpdateIn(image_path=same), restaurant=RESTAURANT_ROW, db=FakeDb(item)
    )

    assert released == []


def test_an_item_cannot_be_given_another_restaurants_picture(released):
    theirs = stored(ANOTHER)
    item = FakeItem()

    with pytest.raises(errors.ApiError):
        update_item(
            item.id, ItemUpdateIn(image_path=theirs), restaurant=RESTAURANT_ROW,
            db=FakeDb(item),
        )

    assert item.image_path is None
    assert released == []


def test_an_options_picture_follows_the_same_rules(released):
    old, new = stored(kind=ImageKind.OPTIONS), stored(kind=ImageKind.OPTIONS)
    option = FakeItem(image_path=old)

    update_modifier_option(
        option.id, ModifierOptionUpdateIn(image_path=new), restaurant=RESTAURANT_ROW,
        db=FakeDb(option),
    )

    assert option.image_path == new
    assert released == [old]

    with pytest.raises(errors.ApiError):
        update_modifier_option(
            option.id, ModifierOptionUpdateIn(image_path=stored()),  # an item's picture
            restaurant=RESTAURANT_ROW, db=FakeDb(option),
        )


# ----------------------------------------------------------- serving ---

@pytest.mark.integration
def test_a_served_image_may_be_kept_for_a_year():
    """The header, not the ETag, is what stops the asking.

    Without a Cache-Control the answer still carried an ETag, so a browser
    asked about every photograph on every view and was told 304 each time. A
    menu with thirty pictures paid thirty round trips to learn nothing had
    changed. These files are immutable by construction -- a random key per
    upload, written once -- so there is nothing to revalidate.

    Writes into the folder the mounted app is actually serving rather than
    the fixture's temporary one. The mount reads IMAGES_DIR once, when
    app.main is first imported, which in a full run is long before this test
    patches it.
    """
    from fastapi.testclient import TestClient

    from app.main import IMAGE_CACHE_CONTROL, app

    served = next(
        route.app.directory for route in app.routes
        if getattr(route, "name", None) == "images"
    )
    path = pathlib.Path(served) / images.new_key(RESTAURANT, ImageKind.ITEMS)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(images.process(encoded()))
    url = "/images/" + path.relative_to(served).as_posix()

    try:
        with TestClient(app) as client:
            first = client.get(url)
            assert first.status_code == 200, first.text
            assert first.headers["content-type"] == "image/webp"
            assert first.headers["cache-control"] == IMAGE_CACHE_CONTROL

            # And a conditional request still says so, rather than dropping
            # the instruction on the way through the 304.
            again = client.get(url, headers={"If-None-Match": first.headers["etag"]})
            assert again.status_code == 304
            assert again.headers["cache-control"] == IMAGE_CACHE_CONTROL
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.integration
def test_a_missing_image_is_not_cached_as_one():
    """A 404 must not be kept for a year: an image uploaded a moment later
    would be invisible until the cache expired."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        missing = client.get(f"/images/{images.new_key(RESTAURANT, ImageKind.ITEMS)}")
        assert missing.status_code == 404
        assert "cache-control" not in missing.headers
