"""Is the machinery behind the requests still turning?

/health/ready says the API can reach its database. It says nothing about
the parts that fail quietly: a stopped worker leaves every payment webhook
unprocessed, a stopped beat leaves abandoned checkouts holding stock forever,
a Redis outage switches the rate limiter off without an error anywhere, and
a full disk stops Postgres mid-order.
Customers notice each of these long before an operator would.

This module answers with the names of what is wrong, never with counts or
ids: /health/operations is public, so an uptime monitor can reach it, and
order volume is nobody else's business.
"""

import logging
import shutil
import time
from functools import lru_cache

from redis import Redis
from sqlalchemy import text

from app.config import settings
from app.core.ratelimit import runtime_redis
from app.db.session import system_session

log = logging.getLogger(__name__)

# Written by the heartbeat task: beat schedules it every minute and a worker
# runs it, so a fresh value proves both are alive and talking to the broker.
# Kept on the broker, which never evicts, rather than on the runtime Redis,
# where memory pressure could evict it and raise a false alarm.
HEARTBEAT_KEY = "zenoeats:ops:heartbeat"
# Five missed beats. Long enough that a worker busy with a slow task, or a
# restart, does not page anyone.
HEARTBEAT_STALE_SECONDS = 300
# A webhook is normally processed within seconds of arriving.
WEBHOOK_STUCK_MINUTES = 10
# A failed delivery stays in its table for someone to look at, forever. The
# alarm is for new ones, so it clears itself a day later rather than never.
WEBHOOK_FAILED_WINDOW_HOURS = 24
# The expiry sweep runs every five minutes. Three missed sweeps.
STALE_CHECKOUT_GRACE_MINUTES = 15
# Free space on the disk holding the menu images -- on a single VM, the same
# disk as the database. Whichever is larger: a small disk alarms at 2 GB, a
# large one at a tenth, both early enough to act before Postgres stops.
DISK_MIN_FREE_BYTES = 2 * 1024**3
DISK_MIN_FREE_RATIO = 0.10


@lru_cache(maxsize=1)
def broker() -> Redis:
    return Redis.from_url(
        settings.CELERY_BROKER_URL,
        socket_timeout=0.5,
        socket_connect_timeout=0.5,
        decode_responses=True,
    )


def beat() -> None:
    """Record that a worker just ran a task beat scheduled."""
    broker().set(HEARTBEAT_KEY, str(time.time()))


_DATABASE_CHECKS = text(
    """
    SELECT
      EXISTS (
        SELECT 1 FROM orders
        WHERE status = 'PENDING_PAYMENT'
          AND expires_at < now() - make_interval(mins => :grace)
      ) AS stale_checkouts,
      EXISTS (
        SELECT 1 FROM stripe_events
        WHERE status = 'RECEIVED' AND received_at < now() - make_interval(mins => :stuck)
      ) AS webhooks_stuck,
      EXISTS (
        SELECT 1 FROM stripe_events
        WHERE status = 'FAILED' AND received_at > now() - make_interval(hours => :window)
      ) AS webhooks_failed
    """
)


def failing() -> list[str]:
    """Names of every check that is failing; empty when all is well."""
    problems: list[str] = []

    try:
        with system_session() as session:
            row = session.execute(
                _DATABASE_CHECKS,
                {
                    "grace": STALE_CHECKOUT_GRACE_MINUTES,
                    "stuck": WEBHOOK_STUCK_MINUTES,
                    "window": WEBHOOK_FAILED_WINDOW_HOURS,
                },
            ).one()
        problems += [name for name in ("stale_checkouts", "webhooks_stuck", "webhooks_failed")
                     if getattr(row, name)]
    except Exception:
        log.exception("operations check could not read the database")
        problems.append("database")

    try:
        runtime_redis().ping()
    except Exception:
        # The rate limiter fails open without it: sign-in and PIN guessing
        # are unlimited until it is back.
        problems.append("redis_runtime")

    try:
        last = broker().get(HEARTBEAT_KEY)
    except Exception:
        problems.append("redis_broker")
    else:
        if last is None or time.time() - float(last) > HEARTBEAT_STALE_SECONDS:
            problems.append("worker_heartbeat")

    try:
        disk = shutil.disk_usage(settings.IMAGES_DIR)
    except OSError:
        problems.append("disk")
    else:
        if disk.free < max(DISK_MIN_FREE_BYTES, disk.total * DISK_MIN_FREE_RATIO):
            problems.append("disk")

    if problems:
        log.warning("operations check failing: %s", ", ".join(problems))
    return problems
