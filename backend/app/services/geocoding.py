"""Turning an address into a point, and a point into a distance.

Delivery fees here are charged by distance, so a fee is only ever as right as
the coordinates behind it. That makes three things matter more than the
lookup itself:

  Never guess. A provider that cannot place an address returns nothing and the
  caller refuses the delivery. An address quietly resolved to the middle of a
  city would charge the wrong ring, every time, silently.

  Never store what we may only borrow. Google's terms allow caching a result
  for about thirty days, not keeping it. Coordinates live in Redis with that
  TTL; what an order keeps is the distance and the fee, which are ours.

  Never block a checkout on someone else's outage. The lookup has a short
  timeout, and the caller is expected to treat failure as "we cannot offer
  delivery right now" rather than as a free delivery.

Distance is straight-line (haversine), not driving distance. Driving distance
needs a routing API at a different price, and rings drawn on a map are what a
restaurant means when it says "we deliver within three miles" anyway.
"""

import hashlib
import json
import logging
import math
from dataclasses import dataclass

import httpx
from redis.exceptions import RedisError

from app.config import settings

log = logging.getLogger(__name__)

EARTH_RADIUS_MILES = 3958.7613


@dataclass(frozen=True)
class Point:
    latitude: float
    longitude: float


class GeocodingUnavailable(Exception):
    """The provider could not be reached, or is not configured.

    Distinct from "that address does not exist": one is our problem and the
    customer should be asked to try again, the other is theirs and no retry
    will help.
    """


def provider() -> str:
    return (settings.GEOCODING_PROVIDER or "google").strip().lower()


def configured() -> bool:
    """Whether addresses can be placed at all. Delivery depends on it.

    Google needs a key. Nominatim needs none -- what it needs instead is a
    contact address in the User-Agent, which its policy treats as mandatory
    and which _nominatim refuses to run without.
    """
    if provider() == "nominatim":
        return bool(_contact())
    return bool(settings.GOOGLE_MAPS_API_KEY) and provider() == "google"


def miles_between(origin: Point, destination: Point) -> float:
    """Great-circle distance in miles.

    Exact enough for delivery rings: the error against a proper geodesic
    calculation is under a tenth of a percent at these distances, which is
    centimetres on a three-mile ring.
    """
    lat1, lon1 = math.radians(origin.latitude), math.radians(origin.longitude)
    lat2, lon2 = math.radians(destination.latitude), math.radians(destination.longitude)
    dlat, dlon = lat2 - lat1, lon2 - lon1

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(min(1.0, math.sqrt(a)))


def geocode(address: str) -> Point | None:
    """Place an address, or None if the provider cannot.

    Raises GeocodingUnavailable when the provider is unreachable or
    unconfigured, which a caller must not confuse with a bad address.
    """
    cleaned = " ".join(address.split()).strip()
    if not cleaned:
        return None
    if not configured():
        raise GeocodingUnavailable("No geocoding provider is configured.")

    cached = _cache_get(cleaned)
    if cached is not None:
        # A cached miss is a miss: an address Google could not place an hour
        # ago is not worth asking about again on every keystroke.
        return Point(**cached) if cached else None

    point = _nominatim(cleaned) if provider() == "nominatim" else _google(cleaned)
    _cache_put(cleaned, point)
    return point


def cached_point(address: str) -> Point | None:
    """An address's coordinates if they are already cached, without asking
    the provider. For paths that must not make a network call, such as a
    tracking page's poll; None means unknown, not "does not exist"."""
    cleaned = " ".join(address.split()).strip()
    if not cleaned:
        return None
    cached = _cache_get(cleaned)
    return Point(**cached) if cached else None


# ------------------------------------------------------------- provider ---
#
# Google takes the API key as a query parameter -- it has no header form -- so
# the key is in the request URL whether we like it or not. httpx puts that URL
# into the text of its exceptions, which means the obvious `log.warning("%s",
# exc)` writes the key, and the customer's home address beside it, into the
# application log and on to Sentry.
#
# So no exception from this call is ever logged or chained. What comes out is
# the class name, or a status code, and nothing that was sent.


def _redact(text: str) -> str:
    """Never let the key travel in something we are about to write down."""
    key = settings.GOOGLE_MAPS_API_KEY
    return text.replace(key, "<api-key>") if key else text


def _blame(reason: str) -> None:
    """Fail without saying what was in the request.

    Called after the except block has ended, never inside one. `raise ... from
    None` clears __cause__ but leaves __context__ pointing at the original
    exception, and the URL is still in there for anything that reads it.
    Raising outside the handler is what actually leaves nothing attached.
    """
    log.warning("geocoding request failed: %s", reason)
    raise GeocodingUnavailable("Could not reach the geocoding service.")


def _google(address: str) -> Point | None:
    failure = None
    try:
        response = httpx.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": address, "key": settings.GOOGLE_MAPS_API_KEY},
            timeout=settings.GEOCODE_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPStatusError as exc:
        # Only the number. The exception's own text is the whole request URL.
        failure = f"provider returned HTTP {exc.response.status_code}"
    except (httpx.HTTPError, ValueError) as exc:
        failure = type(exc).__name__
    if failure is not None:
        _blame(failure)

    status = body.get("status")
    if status == "ZERO_RESULTS":
        return None
    if status != "OK":
        # OVER_QUERY_LIMIT, REQUEST_DENIED and INVALID_REQUEST are all our
        # problem rather than the customer's, and all mean the same thing to
        # them: delivery cannot be quoted right now. Whoever has to fix it
        # needs more than the status, though -- REQUEST_DENIED alone does not
        # distinguish an unenabled API from a key restricted the wrong way --
        # so Google's own explanation is logged, with the key scrubbed out of
        # it in case a future message ever quotes what was sent.
        log.error(
            "geocoding provider answered %s: %s",
            status, _redact(str(body.get("error_message") or "no reason given")),
        )
        raise GeocodingUnavailable("The geocoding service refused the request.")

    results = body.get("results") or []
    if not results:
        return None
    location = results[0].get("geometry", {}).get("location", {})
    try:
        return Point(latitude=float(location["lat"]), longitude=float(location["lng"]))
    except (KeyError, TypeError, ValueError):
        log.error("geocoding provider returned an unreadable location")
        raise GeocodingUnavailable(
            "The geocoding service returned nothing usable."
        ) from None


# ---------------------------------------------------------------- cache ---
#
# Keyed by a hash of the address rather than the address itself, so a Redis
# instance someone can read is not also a list of customers' homes. Failures
# are swallowed in both directions: a cache that is down should cost money in
# lookups, not turn delivery off.

def _cache_key(address: str) -> str:
    digest = hashlib.sha256(address.lower().encode()).hexdigest()
    return f"geocode:{digest}"


def _cache_get(address: str) -> dict | None:
    try:
        from app.core.ratelimit import runtime_redis

        raw = runtime_redis().get(_cache_key(address))
    except (RedisError, OSError):
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _cache_put(address: str, point: Point | None) -> None:
    try:
        from app.core.ratelimit import runtime_redis

        runtime_redis().setex(
            _cache_key(address),
            settings.GEOCODE_CACHE_TTL_SECONDS,
            json.dumps({"latitude": point.latitude, "longitude": point.longitude}
                       if point else {}),
        )
    except (RedisError, OSError):
        pass


# ------------------------------------------------- OpenStreetMap Nominatim
#
# Free, keyless, and run on donated hardware, which is why its usage policy is
# not a formality: a deployment that ignores it gets blocked, and the block
# lands on the whole IP rather than on one request.
#
# Three obligations, all enforced below rather than written down and hoped for:
#
#   Identify yourself.  A User-Agent naming the application and a way to reach
#                       whoever runs it. Requests with a generic or absent one
#                       are refused, so configured() treats a missing contact
#                       as "not configured" rather than letting checkout
#                       discover it.
#   One request/second. Across the whole deployment, not per process -- so the
#                       gate is a Redis key, not a local variable. Several
#                       API workers behind a load balancer would otherwise
#                       send one each.
#   Cache results.      Already done, for thirty days, by the cache above.
#                       Most lookups never reach here.
#
# No key appears in the URL, so unlike the Google path there is nothing to
# redact -- but the address itself is still a customer's home, so the
# exception text is never logged here either.

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Their stated limit is one request a second. The gate below is a one-second
# window in Redis; a caller that finds it taken waits briefly rather than
# failing, because the alternative is refusing a checkout over somebody else's
# keystroke.
_RATE_KEY = "geocode:nominatim:second"
_RATE_WAIT_SECONDS = 1.1


def _contact() -> str:
    """How Nominatim reaches whoever runs this. Required by their policy."""
    return (settings.NOMINATIM_CONTACT or settings.EMAIL_FROM or "").strip()


def _user_agent() -> str:
    return f"Zenoeats/1.0 ({_contact()})"


def _claim_the_second() -> bool:
    """True when this process may send a request now.

    SET NX EX 1 is the whole gate: the first caller in any given second gets
    the key and the slot, everyone else is refused until it expires. Fails
    open when Redis is down -- a cache outage must not take delivery offline,
    and one unthrottled process is a smaller problem than a dead checkout.
    """
    try:
        from app.core.ratelimit import runtime_redis

        return bool(runtime_redis().set(_RATE_KEY, "1", nx=True, ex=1))
    except (RedisError, OSError):
        return True


def _nominatim(address: str) -> Point | None:
    import time

    if not _contact():
        # configured() already refuses this, so reaching here is a bug rather
        # than a deployment mistake. Still refuse: sending anonymous traffic
        # is what gets an IP blocked for everyone behind it.
        log.error("nominatim needs NOMINATIM_CONTACT (or EMAIL_FROM) set")
        raise GeocodingUnavailable("No geocoding provider is configured.")

    if not _claim_the_second():
        # Wait for the window rather than refusing. Bounded, and shorter than
        # GEOCODE_TIMEOUT_SECONDS, so a queue cannot outlast the caller.
        time.sleep(_RATE_WAIT_SECONDS)
        _claim_the_second()

    failure = None
    try:
        response = httpx.get(
            NOMINATIM_URL,
            params={"q": address, "format": "jsonv2", "limit": 1, "addressdetails": 0},
            headers={"User-Agent": _user_agent()},
            timeout=settings.GEOCODE_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPStatusError as exc:
        failure = f"provider returned HTTP {exc.response.status_code}"
    except (httpx.HTTPError, ValueError) as exc:
        failure = type(exc).__name__
    if failure is not None:
        _blame(failure)

    # An empty list is an address Nominatim cannot place: the customer's
    # problem, not ours, and no retry helps. Distinct from the failures above.
    if not isinstance(body, list) or not body:
        return None

    try:
        return Point(latitude=float(body[0]["lat"]), longitude=float(body[0]["lon"]))
    except (KeyError, IndexError, TypeError, ValueError):
        log.error("nominatim returned an unreadable location")
        raise GeocodingUnavailable(
            "The geocoding service returned nothing usable."
        ) from None
