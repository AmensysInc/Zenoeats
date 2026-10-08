"""Menu images: what an upload has to be, where it is kept, and its URL.

Three questions, kept apart so each can change without the others.

WHAT IS ACCEPTED. A JPEG, PNG or WebP photo, decoded by Pillow and written
back out as a fresh WebP. Nothing a manager uploads is ever stored as sent:
re-encoding is what guarantees the file on disk is a picture and only a
picture, whatever its name or declared type said. An SVG, an HTML page
renamed .jpg, or a polyglot that is both an image and a script all fail to
decode as one of the three formats and are refused.

The same pass drops metadata. A phone photo carries the location it was
taken at, and a menu photo taken in the owner's kitchen is a home address
published on the storefront. EXIF orientation is applied first, so a
portrait photo does not turn up on its side once the tag that said "rotate
me" is gone. The colour profile is kept: it says nothing about anyone, and
dropping it shifts the colours of every photo from a recent phone.

WHERE IT IS KEPT. Under a key shaped like

    restaurants/<restaurant id>/<items|options|banners|categories|branding>/<random>.webp

and nothing else. The key is generated here, never taken from the upload's
filename, so a name cannot walk out of the images folder or land on top of
another restaurant's file. The restaurant id at the front is what a save
checks before an item may point at a key, which is the one rule tenancy
needs on files: row-level security protects rows, not a directory.

Rows store the key and never a URL. That is what makes storage movable.

HOW A BROWSER REACHES IT. image_url() appends the key to a public base
setting, "/images" today, which the API serves from its own images folder.
Moving to object storage later means a second storage class with the same
methods and a different base; no row and no screen changes.
"""

import io
import logging
import os
import re
import shutil
import uuid
from enum import Enum
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core import errors
from app.models import (
    Combo, Item, ItemType, ModifierOption, Restaurant, StorefrontBanner, StorefrontShortcut,
)

log = logging.getLogger(__name__)


class ImageKind(str, Enum):
    """What an image is of. Part of the key, so an option's thumbnail cannot
    be attached to an item by pasting its key into the wrong form."""

    ITEMS = "items"
    OPTIONS = "options"
    BANNERS = "banners"
    CATEGORIES = "categories"
    BRANDING = "branding"


# Bytes accepted off the wire. The portal shrinks photos before sending, so a
# real upload is a few hundred kilobytes; this is the ceiling for a browser
# that could not, and it matches the edge's request limit.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

# Decoded size, checked from the header before anything is decoded. A PNG of
# a few kilobytes can declare itself 50,000 pixels square and ask for
# gigabytes of memory to open; this refuses it while it is still a few
# kilobytes. 40 million is a 48 megapixel phone photo with room to spare.
MAX_PIXELS = 40_000_000

# The longest edge stored. A menu row shows a thumbnail and the item sheet a
# phone-width hero; 1600 covers a high-density screen at either size.
MAX_EDGE = 1600

# What each kind is actually displayed at, which is not the same number.
#
# Everything used to be stored at MAX_EDGE regardless, so a category circle
# 65 pixels across downloaded a 1600-pixel photograph -- about fifteen times
# the bytes it could possibly use, on the page a QR code opens, on a phone, on
# restaurant wifi. There is one stored size per image (no srcset, no resizing
# proxy), so that one size has to be chosen per kind rather than once.
#
# Each is roughly three times its largest on-screen size, which covers a
# high-density screen with room for the layout to grow.
EDGE_BY_KIND = {
    # Full-bleed hero, up to a desktop window wide.
    "banners": 2000,
    # The photo column of a menu card, and the hero of the item sheet.
    "items": 900,
    # A 65px circle in the shortcut row, and a category header image.
    "categories": 420,
    # A modifier option's thumbnail: smaller again.
    "options": 320,
    # A logo beside the restaurant's name.
    "branding": 600,
}

WEBP_QUALITY = 82

ACCEPTED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})

_KEY = re.compile(
    r"restaurants/(?P<restaurant>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"/(?P<kind>items|options|banners|categories|branding)/[0-9a-f]{32}\.webp"
)

_UNREADABLE = "That file is not a photo we can use. Upload a JPEG, PNG or WebP image."


# ------------------------------------------------------------ processing ---

def process(data: bytes, kind: ImageKind = ImageKind.ITEMS) -> bytes:
    """Turn an upload into the WebP that is stored, or refuse it."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise errors.ApiError(
            413, "IMAGE_TOO_LARGE",
            "That photo is larger than 8 MB. Choose a smaller one.",
        )
    if not data:
        raise errors.validation_error(_UNREADABLE)

    # Opening reads only the header, which is enough to know the format and
    # the size it claims without paying to decode it.
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = probe.format
            width, height = probe.size
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise errors.validation_error(_UNREADABLE) from exc

    if fmt not in ACCEPTED_FORMATS:
        raise errors.validation_error(_UNREADABLE)
    if width * height > MAX_PIXELS:
        raise errors.validation_error(
            "That photo has too many pixels to use. Choose one under 40 megapixels."
        )

    try:
        with Image.open(io.BytesIO(data)) as source:
            icc_profile = source.info.get("icc_profile")
            # Applies the orientation tag and removes it, so the picture is
            # the right way up before the rest of the metadata is left behind.
            # An animated WebP or PNG contributes its first frame.
            picture = ImageOps.exif_transpose(source)

        has_alpha = picture.mode in ("RGBA", "LA", "PA") or (
            picture.mode == "P" and "transparency" in picture.info
        )
        picture = picture.convert("RGBA" if has_alpha else "RGB")
        edge = EDGE_BY_KIND.get(kind.value, MAX_EDGE)
        picture.thumbnail((edge, edge), Image.Resampling.LANCZOS)

        out = io.BytesIO()
        save_args: dict = {"quality": WEBP_QUALITY, "method": 4, "exif": b"", "xmp": b""}
        if icc_profile:
            save_args["icc_profile"] = icc_profile
        picture.save(out, "WEBP", **save_args)
    except (Image.DecompressionBombError, OSError, ValueError) as exc:
        # A header that promised a picture and a body that did not deliver one.
        raise errors.validation_error(_UNREADABLE) from exc

    return out.getvalue()


# ------------------------------------------------------------------ keys ---

def new_key(restaurant_id: uuid.UUID, kind: ImageKind) -> str:
    """A fresh key. Random, so a new upload never overwrites anything and a
    browser can cache every image for as long as it likes."""
    return f"restaurants/{restaurant_id}/{kind.value}/{uuid.uuid4().hex}.webp"


def belongs_to(key: str, restaurant_id: uuid.UUID, kind: ImageKind) -> bool:
    """Whether a key is shaped like one of this restaurant's images of this
    kind. Says nothing about whether the file exists."""
    match = _KEY.fullmatch(key)
    return (
        match is not None
        and match["restaurant"] == str(restaurant_id)
        and match["kind"] == kind.value
    )


# --------------------------------------------------------------- storage ---

class LocalImageStorage:
    """Images in a folder on this machine, served by the API at /images.

    These methods are the whole of what the rest of the app knows about
    storage. An object-storage version is the same methods, written against
    a bucket.
    """

    def __init__(self, root: Path, public_base: str):
        self.root = Path(root)
        self.public_base = public_base.rstrip("/")

    def _path(self, key: str) -> Path:
        # Keys are validated by shape before they get here, and the shape has
        # no dots or separators to climb with. Resolved and checked anyway:
        # this is the line between a string and the filesystem.
        if not _KEY.fullmatch(key):
            raise ValueError(f"not an image key: {key!r}")
        root = self.root.resolve()
        path = (root / key).resolve()
        if root not in path.parents:
            raise ValueError(f"image key escapes the images folder: {key!r}")
        return path

    def save(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written aside and moved into place, so a request for the key never
        # reads half a file.
        partial = path.with_name(path.name + ".part")
        partial.write_bytes(data)
        os.replace(partial, path)

    def exists(self, key: str) -> bool:
        try:
            return self._path(key).is_file()
        except ValueError:
            return False

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def delete_restaurant(self, restaurant_id: uuid.UUID) -> None:
        """Every image a restaurant ever uploaded. For the permanent purge.

        The id is parsed as a UUID before it becomes a path, so nothing but
        a restaurant's own folder can be named here."""
        root = self.root.resolve()
        folder = (root / "restaurants" / str(uuid.UUID(str(restaurant_id)))).resolve()
        if root not in folder.parents:
            raise ValueError(f"restaurant folder escapes the images folder: {folder}")
        if folder.exists():
            shutil.rmtree(folder)

    def url(self, key: str) -> str:
        return f"{self.public_base}/{key}"


def storage() -> LocalImageStorage:
    """The configured storage. Built per call: it holds a path and a string,
    and reading settings each time is what lets a test point it elsewhere."""
    return LocalImageStorage(settings.IMAGES_DIR, settings.IMAGES_PUBLIC_BASE)


def image_url(key: str | None) -> str | None:
    """The URL a browser loads, or None for a row without an image."""
    return storage().url(key) if key else None


# ------------------------------------------------ attaching and releasing ---

def accept(key: str | None, restaurant_id: uuid.UUID, kind: ImageKind) -> str | None:
    """The key an item or option may store, or a refusal.

    None is a real value: it is how an image is taken off. Anything else has
    to be this restaurant's, of this kind, and actually uploaded. A key from
    another restaurant is refused in the same words as one that does not
    exist, so the answer says nothing about what anyone else has stored.
    """
    if key is None:
        return None
    if not belongs_to(key, restaurant_id, kind) or not storage().exists(key):
        raise errors.validation_error("That image could not be found. Upload it again.")
    return key


def release(db: Session, key: str | None) -> None:
    """Delete an image nothing refers to any more, once the change is saved.

    After the commit, never before. Deleting first and then failing to commit
    would leave the row pointing at a file that is gone. Deleting after means
    the worst a crash between the two can do is leave an unused file behind,
    which costs disk and nothing else.

    Checked against every row that could hold the key, deleted or not. A key
    is normally on one row only, but nothing stops a form sending the same
    one twice, and taking a picture off one item must not take it off another.
    """
    if not key:
        return

    db.flush()
    held = sum(
        db.execute(
            select(func.count()).select_from(model).where(model.image_path == key)
        ).scalar_one()
        for model in (Item, ModifierOption, ItemType, StorefrontBanner, StorefrontShortcut, Combo)
    )
    # The two brand images live on the restaurant row itself.
    held += db.execute(
        select(func.count()).select_from(Restaurant).where(
            (Restaurant.logo_path == key) | (Restaurant.brand_name_image_path == key)
        )
    ).scalar_one()
    if held:
        return

    # Queued on the session and settled by whichever comes first, a commit or
    # a rollback. A listener that simply waited for "the next commit" would
    # outlive a rolled-back change and delete the file on some later, unrelated
    # commit of the same session.
    if not db.info.get(_WATCHING):
        db.info[_WATCHING] = True
        db.info[_PENDING] = set()
        event.listen(db, "after_commit", _delete_pending)
        event.listen(db, "after_soft_rollback", _forget_pending)
    db.info[_PENDING].add(key)


_WATCHING = "images.watching"
_PENDING = "images.pending_release"


def _delete_pending(session: Session) -> None:
    keys = list(session.info.get(_PENDING, ()))
    session.info.get(_PENDING, set()).clear()
    for key in keys:
        try:
            storage().delete(key)
        except (OSError, ValueError):
            log.warning("could not delete released image %s", key, exc_info=True)


def _forget_pending(session: Session, _previous_transaction) -> None:
    session.info.get(_PENDING, set()).clear()
