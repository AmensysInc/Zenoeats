"""The keyless geocoder, and the policy it has to keep.

Nominatim is free and runs on donated hardware, so its usage policy is an
obligation rather than advice: a deployment that ignores it gets its IP
blocked, and the block lands on everyone behind that address. These tests
are mostly about the obligations, because those are the part that is easy to
regress silently -- a wrong fee shows up in testing, an absent User-Agent
does not.

The provider itself is never called here. What is tested is what we send,
what we do with what comes back, and what we refuse to do at all.
"""

import pytest

from app.services import geocoding


@pytest.fixture(autouse=True)
def nominatim(monkeypatch):
    monkeypatch.setattr(geocoding.settings, "GEOCODING_PROVIDER", "nominatim")
    monkeypatch.setattr(geocoding.settings, "NOMINATIM_CONTACT", "ops@example.com")


class _Answer:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_it_needs_no_api_key():
    """The whole point: no key, no account, no card."""
    geocoding.settings.GOOGLE_MAPS_API_KEY = ""
    assert geocoding.configured() is True


def test_it_refuses_to_run_without_a_contact(monkeypatch):
    """Nominatim's policy requires identifying who is calling.

    Treated as "not configured" rather than discovered at checkout: sending
    anonymous traffic is what gets an address blocked for everybody on it.
    """
    monkeypatch.setattr(geocoding.settings, "NOMINATIM_CONTACT", "")
    monkeypatch.setattr(geocoding.settings, "EMAIL_FROM", "")
    assert geocoding.configured() is False


def test_every_request_identifies_this_deployment(monkeypatch):
    """A generic User-Agent is refused by Nominatim, so assert on ours."""
    seen = {}

    def fake_get(url, **kwargs):
        seen.update(kwargs)
        seen["url"] = url
        return _Answer([{"lat": "32.81", "lon": "-96.77"}])

    monkeypatch.setattr(geocoding.httpx, "get", fake_get)
    monkeypatch.setattr(geocoding, "_claim_the_second", lambda: True)

    geocoding._nominatim("1800 Greenville Ave, Dallas, TX")

    agent = seen["headers"]["User-Agent"]
    assert "Zenoeats" in agent
    assert "ops@example.com" in agent, "their policy wants a way to reach us"
    assert seen["url"].startswith("https://nominatim.openstreetmap.org/")


def test_a_lookup_waits_for_its_turn_rather_than_stampeding(monkeypatch):
    """One request a second, across every worker.

    The gate is a Redis key rather than a local variable precisely because
    several API processes would otherwise each send one per second.
    """
    calls = []
    monkeypatch.setattr(geocoding, "_claim_the_second", lambda: calls.append(1) or len(calls) > 1)
    monkeypatch.setattr(geocoding.httpx, "get", lambda *a, **k: _Answer([{"lat": "1", "lon": "2"}]))

    slept = []
    import time as real_time
    monkeypatch.setattr(real_time, "sleep", lambda s: slept.append(s))

    geocoding._nominatim("somewhere")

    assert slept, "a caller that lost the second must wait, not send anyway"
    assert slept[0] <= geocoding.settings.GEOCODE_TIMEOUT_SECONDS, (
        "the wait must not outlast the caller's own timeout"
    )


def test_an_address_it_cannot_place_is_a_miss_not_a_failure(monkeypatch):
    """Distinct from an outage: no retry helps, and the customer must be told
    to check the address rather than to try again later."""
    monkeypatch.setattr(geocoding.httpx, "get", lambda *a, **k: _Answer([]))
    monkeypatch.setattr(geocoding, "_claim_the_second", lambda: True)

    assert geocoding._nominatim("qqqzzz not a real place") is None


def test_an_unreadable_answer_is_an_outage_not_a_miss(monkeypatch):
    """A reply we cannot parse is our problem. Returning None would quietly
    tell the customer their address does not exist."""
    monkeypatch.setattr(geocoding.httpx, "get", lambda *a, **k: _Answer([{"lat": "north"}]))
    monkeypatch.setattr(geocoding, "_claim_the_second", lambda: True)

    with pytest.raises(geocoding.GeocodingUnavailable):
        geocoding._nominatim("somewhere")


def test_coordinates_are_read_as_numbers(monkeypatch):
    """Nominatim returns lat and lon as strings; a fee computed from strings
    is a TypeError at checkout."""
    monkeypatch.setattr(
        geocoding.httpx, "get",
        lambda *a, **k: _Answer([{"lat": "32.8120", "lon": "-96.7700"}]),
    )
    monkeypatch.setattr(geocoding, "_claim_the_second", lambda: True)

    point = geocoding._nominatim("1800 Greenville Ave, Dallas, TX")

    assert isinstance(point.latitude, float) and isinstance(point.longitude, float)
    assert round(point.latitude, 3) == 32.812


def test_the_rate_gate_fails_open_when_redis_is_down(monkeypatch):
    """A cache outage must cost accuracy, never delivery.

    One unthrottled process is a smaller problem than every checkout
    refusing to quote.
    """
    import app.core.ratelimit as rl

    def no_redis():
        raise OSError("redis is down")

    monkeypatch.setattr(rl, "runtime_redis", no_redis)
    assert geocoding._claim_the_second() is True
