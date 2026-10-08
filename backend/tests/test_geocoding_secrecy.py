"""The API key stays in .env, and the customer's address stays between us.

Google takes its key as a query parameter -- there is no header form -- so the
key is in the request URL whether we like it or not, and the address the
customer typed is in there beside it.

httpx puts that URL into the text of its exceptions. So the obvious way to
report a failed lookup, `log.warning("%s", exc)`, writes both into the
application log, and `raise ... from exc` carries both to whatever catches it,
including the error reporter that sends them off the machine.

These tests fail if either ever happens again. They use a key that looks
nothing like a real one and then look for it everywhere it could surface.
"""

import logging

import httpx
import pytest

from app.services import geocoding

SECRET = "AIza-TEST-DO-NOT-USE-0123456789"
HOME = "12 Oak Street, Chicago IL 60614"


@pytest.fixture
def armed(monkeypatch):
    """A configured provider whose key we can then hunt for."""
    from app.config import settings

    monkeypatch.setattr(settings, "GOOGLE_MAPS_API_KEY", SECRET)
    monkeypatch.setattr(settings, "GEOCODING_PROVIDER", "google")
    # No cache: a cached answer would skip the request entirely.
    monkeypatch.setattr(geocoding, "_cache_get", lambda address: None)
    monkeypatch.setattr(geocoding, "_cache_put", lambda address, point: None)


def _fail_with(monkeypatch, exc):
    def boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(geocoding.httpx, "get", boom)


def test_the_key_is_read_from_settings_and_not_baked_in(armed):
    """Change the setting, and the request changes with it. A hardcoded key
    would ignore this."""
    seen = {}

    def capture(url, params=None, timeout=None):
        seen.update(params or {})
        return httpx.Response(
            200, json={"status": "OK", "results": [
                {"geometry": {"location": {"lat": 41.9, "lng": -87.6}}}
            ]},
            request=httpx.Request("GET", url),
        )

    import pytest as _pytest

    _pytest.MonkeyPatch().setattr(geocoding.httpx, "get", capture)
    try:
        geocoding.geocode(HOME)
    finally:
        pass
    assert seen.get("key") == SECRET


def test_a_provider_error_does_not_write_the_key_into_the_log(armed, monkeypatch, caplog):
    """The failure mode that started this: httpx's HTTPStatusError stringifies
    to the whole request URL, key and address included."""
    request = httpx.Request(
        "GET", f"https://maps.googleapis.com/maps/api/geocode/json?address={HOME}&key={SECRET}"
    )
    _fail_with(monkeypatch, httpx.HTTPStatusError(
        f"Client error '403 Forbidden' for url '{request.url}'",
        request=request,
        response=httpx.Response(403, request=request),
    ))

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(geocoding.GeocodingUnavailable):
            geocoding.geocode(HOME)

    written = caplog.text
    assert SECRET not in written
    assert "Oak Street" not in written
    # Still says something useful.
    assert "403" in written


def test_a_transport_error_does_not_write_the_key_either(armed, monkeypatch, caplog):
    request = httpx.Request(
        "GET", f"https://maps.googleapis.com/maps/api/geocode/json?key={SECRET}"
    )
    _fail_with(monkeypatch, httpx.ConnectError(f"failed to connect to {request.url}", request=request))

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(geocoding.GeocodingUnavailable):
            geocoding.geocode(HOME)

    assert SECRET not in caplog.text
    assert "ConnectError" in caplog.text


def test_the_raised_error_carries_no_cause_to_hand_to_sentry(armed, monkeypatch):
    """`raise ... from exc` would attach the original exception, and the URL
    inside it, to whatever catches this -- the error reporter included."""
    request = httpx.Request(
        "GET", f"https://maps.googleapis.com/maps/api/geocode/json?key={SECRET}"
    )
    _fail_with(monkeypatch, httpx.ConnectError(f"failed: {request.url}", request=request))

    with pytest.raises(geocoding.GeocodingUnavailable) as raised:
        geocoding.geocode(HOME)

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None or SECRET not in str(raised.value.__context__)
    assert SECRET not in str(raised.value)


def test_nothing_the_customer_sees_mentions_the_provider_at_all(armed, monkeypatch):
    """The message a customer reads is about their delivery, not our vendor."""
    request = httpx.Request("GET", "https://maps.googleapis.com/maps/api/geocode/json")
    _fail_with(monkeypatch, httpx.ConnectError("nope", request=request))

    with pytest.raises(geocoding.GeocodingUnavailable) as raised:
        geocoding.geocode(HOME)

    assert "google" not in str(raised.value).lower()
    assert SECRET not in str(raised.value)


def test_without_a_key_nothing_is_requested_at_all(monkeypatch):
    """An empty key is not a reason to call Google anonymously and find out.

    The provider is pinned rather than inherited: this is a statement about
    the Google path, and a deployment running Nominatim has no key by design.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "GEOCODING_PROVIDER", "google")
    monkeypatch.setattr(settings, "GOOGLE_MAPS_API_KEY", "")

    def boom(*args, **kwargs):
        raise AssertionError("no request should be made without a key")

    monkeypatch.setattr(geocoding.httpx, "get", boom)

    assert geocoding.configured() is False
    with pytest.raises(geocoding.GeocodingUnavailable):
        geocoding.geocode(HOME)


def test_without_a_contact_nominatim_is_not_called_either(monkeypatch):
    """The same rule for the keyless provider.

    Nominatim needs no key, so "unconfigured" means something else there: no
    way to identify who is calling. Sending anonymous traffic anyway is what
    gets an IP blocked for everyone behind it, so it must be refused in the
    same place and in the same way.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "GEOCODING_PROVIDER", "nominatim")
    monkeypatch.setattr(settings, "NOMINATIM_CONTACT", "")
    monkeypatch.setattr(settings, "EMAIL_FROM", "")

    def boom(*args, **kwargs):
        raise AssertionError("no request should be made without a contact")

    monkeypatch.setattr(geocoding.httpx, "get", boom)

    assert geocoding.configured() is False
    with pytest.raises(geocoding.GeocodingUnavailable):
        geocoding.geocode(HOME)


def test_the_cache_key_is_not_the_address_in_the_clear(armed):
    """Redis is another place a customer's home could sit in plain text."""
    key = geocoding._cache_key(HOME)

    assert "Oak Street" not in key
    assert HOME.lower() not in key
    assert key.startswith("geocode:")
