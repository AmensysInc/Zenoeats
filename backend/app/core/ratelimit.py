"""Fixed-window rate limiting on the redis-runtime instance.

Section 23: this is the cache/limiter instance, deliberately separate from
the Celery broker. It runs allkeys-lru and holds nothing durable, so cache
pressure here can never evict a queued payment webhook.

Design notes:

  * Fixed window, not a token bucket. A window boundary lets through at most
    2x the limit in the worst case, which is fine for abuse control and costs
    one round trip instead of a Lua script.

  * FAIL OPEN. If Redis is unreachable the limiter logs and allows the
    request. A limiter outage must not stop a restaurant taking orders --
    losing revenue to protect against hypothetical abuse is the wrong trade
    for this business. The log line is the signal to go fix Redis.

  * Keys carry the window start, so they expire on their own and no sweeper
    is needed.
"""

import hashlib
import hmac
import ipaddress
import logging
import time
from functools import lru_cache
from typing import Callable

from fastapi import Depends, Request
from redis import Redis
from redis.exceptions import RedisError

from app.api.deps import current_staff_user, get_current_user
from app.config import settings
from app.core import errors
from app.models import User

log = logging.getLogger(__name__)

_client: Redis | None = None


def runtime_redis() -> Redis:
    """Lazy singleton. Built on first use so importing this module never
    opens a socket (tests and Alembic import the app without Redis)."""
    global _client
    if _client is None:
        _client = Redis.from_url(
            settings.REDIS_RUNTIME_URL,
            socket_timeout=0.25,
            socket_connect_timeout=0.25,
            decode_responses=True,
        )
    return _client


@lru_cache(maxsize=1)
def _trusted_proxies(raw: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    networks = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            log.error("TRUSTED_PROXY_CIDRS entry %r is not a network; ignored", part)
    return tuple(networks)


def _parse_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def _is_trusted_proxy(address: str) -> bool:
    ip = _parse_ip(address)
    if ip is None:
        return False
    parsed = ipaddress.ip_address(ip)
    return any(parsed in net for net in _trusted_proxies(settings.TRUSTED_PROXY_CIDRS))


def _client_ip(request: Request) -> str:
    """The address a request really came from, as far as it can be proven.

    Forwarding headers are claims. Anyone can send
    "X-Forwarded-For: 1.2.3.4", and every proxy on the way appends to that
    header rather than replacing it -- so its left-most entry, which this used
    to read, is whatever the attacker typed, and a per-IP limit keyed on it
    was no limit at all.

    So a header is believed only when the connection itself comes from a
    proxy we run (TRUSTED_PROXY_CIDRS). From such a peer, X-Real-IP -- which
    our nginx overwrites with the address it saw -- is taken first. Failing
    that, X-Forwarded-For is walked from the right, skipping our own proxies,
    and the first address that is not one of ours is the client. Anything
    else, including a request reaching the API directly, is keyed on the
    socket address, which cannot be forged.
    """
    peer = request.client.host if request.client else None

    if peer and _is_trusted_proxy(peer):
        real_ip = _parse_ip(request.headers.get("x-real-ip"))
        if real_ip:
            return real_ip

        forwarded = request.headers.get("x-forwarded-for") or ""
        for hop in reversed([h.strip() for h in forwarded.split(",") if h.strip()]):
            ip = _parse_ip(hop)
            if ip is None:
                break
            if not _is_trusted_proxy(ip):
                return ip

    return _parse_ip(peer) or "unknown"


def _consume(bucket: str, limit: int, window_seconds: int) -> None:
    now = int(time.time())
    key = f"rl:{bucket}:{now // window_seconds}"
    try:
        redis = runtime_redis()
        pipe = redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds)
        count, _ = pipe.execute()
    except RedisError:
        log.warning("rate limiter unavailable, allowing request on %s", bucket, exc_info=True)
        return

    if count > limit:
        log.info("rate limit hit on %s (%s/%s)", bucket, count, limit)
        raise errors.ApiError(
            429, "RATE_LIMITED", "Too many requests. Wait a moment and try again."
        )


def consume(bucket: str, limit: int, window_seconds: int = 60) -> None:
    """Count one use of a named budget, for callers that are not a request
    dependency -- a per-restaurant cap on billable Stripe calls, say. Raises
    the usual 429 when the budget is spent; fails open like everything here."""
    _consume(bucket, limit, window_seconds)


def failures_exhausted(bucket: str, limit: int, window_seconds: int) -> bool:
    """Whether a budget that counts only failures is already spent.

    For endpoints where success must never be throttled -- a cashier handing
    over order after order at the lunch rush -- but wrong answers should be.
    Checked before the answer is looked at, so a throttled caller cannot keep
    guessing; record_failure() spends from the budget afterwards. Fails open.
    """
    key = f"rl:{bucket}:{int(time.time()) // window_seconds}"
    try:
        count = runtime_redis().get(key)
    except RedisError:
        log.warning("rate limiter unavailable, allowing request on %s", bucket, exc_info=True)
        return False
    return count is not None and int(count) >= limit


def record_failure(bucket: str, window_seconds: int) -> None:
    """Spend one from a failures-only budget. See failures_exhausted."""
    key = f"rl:{bucket}:{int(time.time()) // window_seconds}"
    try:
        pipe = runtime_redis().pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds)
        pipe.execute()
    except RedisError:
        log.warning("rate limiter unavailable, not counting failure on %s", bucket, exc_info=True)


# --- per account ------------------------------------------------------------
#
# The sign-in limits are per address, and an address is cheap: spread a
# password list over a few hundred IPs and one owner's account can be guessed
# at without end. So wrong answers are also counted against the account they
# were aimed at.
#
# Generous on purpose. Anyone who knows an owner's email can spend this budget
# with wrong passwords, and a tight one (five, ten) would let them lock the
# owner out of their own restaurant at will. Fifty in fifteen minutes stops a
# distributed guesser at a few hundred tries an hour -- against a twelve-
# character minimum -- while making a lockout cost more than five addresses
# every quarter of an hour. Sessions already signed in are untouched; only new
# sign-ins wait. Unknown addresses are counted exactly like real ones, so the
# limit says nothing about which accounts exist.
SIGN_IN_FAILURES_PER_ACCOUNT = 50
SIGN_IN_FAILURE_WINDOW_SECONDS = 900


def _account_bucket(name: str, email: str) -> str:
    # Keyed, so Redis never holds the address and the key cannot be reversed
    # by anyone reading it.
    key = (settings.SESSION_SECRET or "zenoeats-ratelimit").encode()
    digest = hmac.new(key, email.strip().lower().encode(), hashlib.sha256).hexdigest()[:32]
    return f"{name}:account:{digest}"


def sign_in_blocked(name: str, email: str) -> bool:
    """Whether this account's budget of wrong passwords is spent. Fails open."""
    return failures_exhausted(
        _account_bucket(name, email), SIGN_IN_FAILURES_PER_ACCOUNT, SIGN_IN_FAILURE_WINDOW_SECONDS
    )


def sign_in_failed(name: str, email: str) -> None:
    record_failure(_account_bucket(name, email), SIGN_IN_FAILURE_WINDOW_SECONDS)


def sign_in_throttled() -> errors.ApiError:
    return errors.ApiError(
        429, "RATE_LIMITED",
        "Too many sign-in attempts for this account. Wait 15 minutes and try again.",
    )


def per_ip(name: str, limit: int, window_seconds: int = 60) -> Callable:
    """Limit by client address. For endpoints reachable without a session."""

    def dependency(request: Request) -> None:
        _consume(f"{name}:ip:{_client_ip(request)}", limit, window_seconds)

    return dependency


def per_user(name: str, limit: int, window_seconds: int = 60) -> Callable:
    """Limit by authenticated user.

    Keyed on the verified user id, so it cannot be shaken off by
    rotating IPs, and one abusive account cannot exhaust the budget of
    everyone behind the same NAT.
    """

    def dependency(user: User = Depends(get_current_user)) -> None:
        _consume(f"{name}:user:{user.id}", limit, window_seconds)

    return dependency


def per_staff_user(name: str, limit: int, window_seconds: int = 60) -> Callable:
    """Limit by signed-in restaurant staff member.

    The same idea as per_user, for the portal. Staff authenticate with a
    session cookie rather than a customer's, so the customer dependency
    would refuse every one of them before counting anything.
    """

    def dependency(user: User = Depends(current_staff_user)) -> None:
        _consume(f"{name}:staff:{user.id}", limit, window_seconds)

    return dependency
