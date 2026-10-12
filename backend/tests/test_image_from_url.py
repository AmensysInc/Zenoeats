"""Storing a picture from a link the restaurant pasted in.

Two halves, and the second is the one that matters. The first is that a real
image URL becomes one of our own WebP files under one of our own keys --
indistinguishable from an upload, which is the point.

The second is everything this refuses to fetch. An endpoint that requests a
caller's URL is a request the *server* makes, and it can be asked for things
the caller could never reach: a cloud instance's metadata service, an admin
page bound to localhost, a console on the private network. Every test below
that ends in a refusal is one of those.
"""

import io
import uuid

import pytest

from app.core import errors
from app.services import image_fetch

from tests.test_admin_restaurants import (  # noqa: F401  (fixtures)
    _create,
    _email,
    _owner,
    _set_own_password,
    _staff_client,
    admin_user,
    cleanup,
)
from tests.test_staff_removal import team  # noqa: F401  (fixture)

FROM_URL = "/api/v1/restaurant/images/from-url?kind=items"


@pytest.fixture(autouse=True)
def no_rate_limits(monkeypatch):
    from app.core import ratelimit

    monkeypatch.setattr(ratelimit, "_consume", lambda *a, **k: None)


def _png(size=(40, 40)) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", size, (200, 60, 40)).save(out, "PNG")
    return out.getvalue()


# ---------------------------------------------------- what it will not fetch ---
#
# These never reach the network: the refusal is decided from the URL and what
# its hostname resolves to, which is why they are safe to run anywhere.


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://example.com/",
        "ftp://example.com/logo.png",
        # No scheme at all: urlparse is happy and the client would guess.
        "example.com/logo.png",
    ],
)
def test_only_http_and_https_are_spoken(url):
    with pytest.raises(errors.ApiError) as refused:
        image_fetch.fetch(url, image_fetch.ImageKind.ITEMS, uuid.uuid4())
    assert refused.value.status_code == 422


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://localhost:8000/health",
        "http://[::1]/",
        # The cloud metadata service, which is the reason this check exists.
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://172.16.0.9/",
        # Resolves to 127.0.0.1 by design. A hostname check that only looked
        # at the string would let this through.
        "http://localtest.me/",
    ],
)
def test_the_private_network_is_not_reachable_through_a_link(url):
    with pytest.raises(errors.ApiError) as refused:
        image_fetch.fetch(url, image_fetch.ImageKind.ITEMS, uuid.uuid4())
    assert refused.value.status_code == 422
    # The same sentence for every one of them: which internal hosts exist is
    # not something this endpoint should be willing to say.
    assert refused.value.detail["message"] == image_fetch.UNREACHABLE


def test_credentials_in_the_link_are_refused():
    """A URL carrying a password was not meant to be shared, and we would
    put it in a request."""
    with pytest.raises(errors.ApiError):
        image_fetch.fetch(
            "http://user:secret@example.com/logo.png",
            image_fetch.ImageKind.ITEMS,
            uuid.uuid4(),
        )


# -------------------------------------------------------- what it does fetch ---
#
# The network is faked from here on: a test suite must not depend on
# somebody else's image host being up.


class _FakeResponse:
    def __init__(self, body=b"", status=200, headers=None, url="https://pics.test/a.png"):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        import httpx

        self.url = httpx.URL(url)

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308)

    def iter_bytes(self):
        # In pieces, so the cap is exercised as a cap on reading.
        for start in range(0, max(len(self._body), 1), 8192):
            yield self._body[start:start + 8192]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _client(responses, seen=None):
    """A stand-in httpx.Client answering a queue of responses."""

    class FakeClient:
        def __init__(self, *_, **__):
            self._left = list(responses)

        def stream(self, _method, url, **__):
            if seen is not None:
                seen.append(url)
            return self._left.pop(0)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    return FakeClient


@pytest.fixture
def allow_any_host(monkeypatch):
    """Skip the DNS check. The refusals above are what tests it; these tests
    are about what happens to the bytes."""
    monkeypatch.setattr(image_fetch, "_public", lambda host: None)


def test_a_linked_picture_becomes_one_of_our_own_webp_files(monkeypatch, allow_any_host, tmp_path):
    from app.config import settings

    monkeypatch.setattr(settings, "IMAGES_DIR", tmp_path)
    monkeypatch.setattr(image_fetch.httpx, "Client", _client([_FakeResponse(_png())]))

    rid = uuid.uuid4()
    stored = image_fetch.fetch("https://pics.test/a.png", image_fetch.ImageKind.ITEMS, rid)

    # Our key, our folder, our format -- nothing of the remote file's name
    # or type survives.
    assert stored["image_path"].startswith(f"restaurants/{rid}/items/")
    assert stored["image_path"].endswith(".webp")
    written = tmp_path / stored["image_path"]
    assert written.is_file()
    assert written.read_bytes()[:4] == b"RIFF"


def test_something_that_is_not_a_picture_is_refused(monkeypatch, allow_any_host, tmp_path):
    """An HTML error page served with a .png in the URL is the common case,
    and it is refused by the same decode an upload goes through."""
    from app.config import settings

    monkeypatch.setattr(settings, "IMAGES_DIR", tmp_path)
    monkeypatch.setattr(
        image_fetch.httpx, "Client",
        _client([_FakeResponse(b"<!doctype html><title>Not found</title>")]),
    )

    with pytest.raises(errors.ApiError):
        image_fetch.fetch("https://pics.test/a.png", image_fetch.ImageKind.ITEMS, uuid.uuid4())


def test_a_redirect_is_followed_and_re_checked(monkeypatch, tmp_path):
    """The check has to happen again on the new host. Following redirects
    automatically would check the URL that was typed and fetch the one it
    pointed at."""
    from app.config import settings

    monkeypatch.setattr(settings, "IMAGES_DIR", tmp_path)
    checked: list[str] = []
    monkeypatch.setattr(image_fetch, "_public", lambda host: checked.append(host))
    monkeypatch.setattr(
        image_fetch.httpx, "Client",
        _client([
            _FakeResponse(status=302, headers={"location": "https://cdn.test/real.png"}),
            _FakeResponse(_png()),
        ]),
    )

    image_fetch.fetch("https://pics.test/a.png", image_fetch.ImageKind.ITEMS, uuid.uuid4())
    assert checked == ["pics.test", "cdn.test"]


def test_a_redirect_to_the_private_network_is_refused(monkeypatch, tmp_path):
    """The attack the manual hop exists to stop: a public URL that bounces
    to localhost."""
    from app.config import settings

    monkeypatch.setattr(settings, "IMAGES_DIR", tmp_path)
    monkeypatch.setattr(
        image_fetch.httpx, "Client",
        _client([_FakeResponse(status=302, headers={"location": "http://127.0.0.1/admin"})]),
    )

    with pytest.raises(errors.ApiError) as refused:
        image_fetch.fetch("https://pics.test/a.png", image_fetch.ImageKind.ITEMS, uuid.uuid4())
    assert refused.value.detail["message"] == image_fetch.UNREACHABLE


def test_an_endless_body_is_cut_off_at_the_cap(monkeypatch, allow_any_host, tmp_path):
    from app.config import settings

    monkeypatch.setattr(settings, "IMAGES_DIR", tmp_path)
    monkeypatch.setattr(image_fetch, "MAX_BYTES", 16_384)
    monkeypatch.setattr(
        image_fetch.httpx, "Client", _client([_FakeResponse(b"x" * 200_000)])
    )

    with pytest.raises(errors.ApiError) as refused:
        image_fetch.fetch("https://pics.test/a.png", image_fetch.ImageKind.ITEMS, uuid.uuid4())
    assert refused.value.status_code == 413


def test_a_chain_of_redirects_ends(monkeypatch, allow_any_host, tmp_path):
    from app.config import settings

    monkeypatch.setattr(settings, "IMAGES_DIR", tmp_path)
    hop = _FakeResponse(status=302, headers={"location": "https://pics.test/next.png"})
    monkeypatch.setattr(image_fetch.httpx, "Client", _client([hop] * 9))

    with pytest.raises(errors.ApiError):
        image_fetch.fetch("https://pics.test/a.png", image_fetch.ImageKind.ITEMS, uuid.uuid4())


# ------------------------------------------------------------ the endpoint ---

pytestmark = pytest.mark.integration


def test_the_endpoint_answers_exactly_like_an_upload(team, monkeypatch, tmp_path):
    """Same shape, so every picker in the portal already reads it."""
    from app.config import settings

    monkeypatch.setattr(settings, "IMAGES_DIR", tmp_path)
    monkeypatch.setattr(image_fetch, "_public", lambda host: None)
    monkeypatch.setattr(image_fetch.httpx, "Client", _client([_FakeResponse(_png())]))

    res = team.owner.post(FROM_URL, json={"url": "https://pics.test/a.png"})
    assert res.status_code == 201, res.text
    body = res.json()
    assert set(body) == {"image_path", "image_url"}
    assert body["image_url"].endswith(body["image_path"])


def test_kitchen_cannot_pull_images_in(team):
    user_id, _ = team.member("KITCHEN")
    res = team.client(user_id).post(FROM_URL, json={"url": "https://pics.test/a.png"})
    assert res.status_code == 403


def test_support_may_upload_a_banner_and_may_not_link_one(team):
    """The deliberate difference between this endpoint and the upload beside
    it. They look like the same capability: one hands us bytes, the other
    makes our server fetch an address somebody else chose, and the role that
    works on restaurants from outside is the last one that should have the
    second."""
    user_id, _ = team.member("IT_SUPPORT")
    support = team.client(user_id)

    assert support.post(FROM_URL, json={"url": "https://pics.test/a.png"}).status_code == 403
    # Not 403: the upload refuses this one for want of a file, which is as
    # far as the role check lets it get.
    assert support.post("/api/v1/restaurant/images?kind=items").status_code == 422
