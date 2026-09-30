import asyncio
import logging
import mimetypes
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.v1.router import api_router
from app.config import settings
from app.core.tenant import allowed_origin_roots
from app.core import errors, logsafe, observability, stall_watch, startup_checks
from app.db.session import app_engine, system_engine
from app.services import ops_health

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("zenoeats")
# uvicorn has configured its loggers by the time this module is imported, so
# the filter attaches to the logger it already built.
logging.getLogger("uvicorn.access").addFilter(logsafe.DropQueryStrings())

startup_checks.enforce(settings)
observability.init_error_tracking("api")


def _warm() -> None:
    """Pay the first-connection costs at startup instead of on a user.

    A cold process answered its first real request in roughly three seconds
    and every one after that in forty milliseconds. None of that gap was the
    request: it was opening the first pooled connection to each database,
    negotiating the first Redis socket, and letting SQLAlchemy build its
    machinery on first use. Under --reload that bill is presented again after
    every single edit, which is what made the server feel slow to work with.

    Every step fails soft. A warm-up that could stop the API booting would be
    a worse problem than the latency it removes -- the readiness probe is what
    reports on infrastructure, not this.
    """
    started = time.perf_counter()
    for name, engine in (("app", app_engine()), ("system", system_engine())):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1")).scalar_one()
        except Exception:
            log.warning("could not warm the %s database pool", name, exc_info=True)

    try:
        # Imported here rather than at module scope: importing it opens no
        # socket, but calling it does, and that is the point of doing it now.
        from app.core.ratelimit import runtime_redis

        runtime_redis().ping()
    except Exception:
        log.warning("could not warm the redis connection", exc_info=True)

    log.info("warm-up finished in %.0fms", (time.perf_counter() - started) * 1000)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _warm()
    watching = stall_watch.start(asyncio.get_running_loop())
    try:
        yield
    finally:
        watching.set()


app = FastAPI(
    title="Zenoeats API",
    version="1.0.0-mvp",
    # Production Swagger is restricted (section 16.3).
    docs_url=None if settings.ENV == "production" else "/docs",
    redoc_url=None,
    openapi_url=None if settings.ENV == "production" else "/openapi.json",
    lifespan=lifespan,
)

# Cross-origin access, and why production has none.
#
# Every page that calls this API -- storefront, staff portal, admin portal --
# is served by nginx on the same hostname as the API itself, so the calls are
# same-origin and CORS never applies to them. The rule that used to sit here
# allowed any subdomain of the root domain to call any endpoint with cookies
# attached, which granted every storefront origin credentialed access to the
# operator APIs it shares a site with. Nothing needed it.
#
# Outside production the middleware stays, because a developer may run the SPA
# and the API on different ports.
if settings.ENV != "production":
    _origins = "|".join(re.escape(root) for root in allowed_origin_roots())
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=(
            r"https?://([a-z0-9-]+\.)*(" + _origins + r")(:\d+)?$"
        ),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    """A malformed request, answered in the same envelope as everything else.

    FastAPI's default is a list of error objects under `detail`, which is not
    the {code, message} shape every other error on this API uses. Clients read
    the shape, not the status, so an unreadable body became a generic failure
    message with nothing in it to act on -- the caller could not tell a blank
    required field from the server falling over.

    This is the safety net, not the first line: a form should say which of its
    own fields is wrong before sending anything. What it guarantees is that
    nothing reaching a user is less specific than the field that caused it.
    """
    return JSONResponse(
        status_code=422,
        content={
            "code": "VALIDATION_ERROR",
            "message": errors.request_validation_message(exc.errors()),
        },
    )


# How much of a fault to quote back in development. A SQLAlchemy error
# carries the whole statement and its parameters, which is a wall of text in a
# banner meant for one line.
_FAULT_CHARS = 300


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    """A fault nobody anticipated, reported so it can actually be chased.

    Two things were wrong with answering every one of these with the bare
    words "Something went wrong". Nothing tied the message on screen to the
    line in the log, so a report of it could not be looked up. And in
    development, where the person reading the banner is the person who broke
    it, refusing to say what happened turns a one-line fix into a hunt
    through a terminal -- a menu screen that failed because the API was still
    running pre-migration code looked exactly like one that failed for any
    other reason.

    So every fault gets a short reference that appears in both places, and in
    development only, the fault itself is quoted. Production keeps its mouth
    shut: an exception can carry a query, a path, or a value someone typed,
    and none of that belongs in a response. The check is against
    "development" by name rather than "not production", so an ENV nobody
    recognises stays silent instead of leaking.
    """
    reference = uuid.uuid4().hex[:8]
    log.exception(
        "unhandled error [%s] on %s %s", reference, request.method, request.url.path
    )
    observability.capture(exc, reference=reference)

    if settings.ENV == "development":
        fault = f"{type(exc).__name__}: {exc}".replace("\n", " ")
        if len(fault) > _FAULT_CHARS:
            fault = fault[:_FAULT_CHARS] + "…"
        message = f"{fault} (reference {reference})"
    else:
        message = f"Something went wrong. Reference {reference}."

    return JSONResponse(
        status_code=500,
        content={"code": "INTERNAL_ERROR", "message": message, "reference": reference},
    )


@app.get("/", include_in_schema=False)
def root():
    """Signpost, not an endpoint.

    Nothing customer-facing is served from this port: the storefront is the
    web app on the restaurant's subdomain. Landing here means the wrong host
    was typed, so say where to go instead of a bare 404.
    """
    body = {
        "service": "zenoeats-api",
        "version": app.version,
        "env": settings.ENV,
        "health": "/health",
        "api": api_router.prefix,
        "storefront": f"a restaurant subdomain of {settings.ROOT_DOMAIN}, served by the web app",
    }
    if settings.ENV != "production":
        body["docs"] = "/docs"
    return body


@app.get("/health")
def health():
    return {"status": "ok", "env": settings.ENV}


@app.get("/health/ready")
def ready():
    """Readiness: can we actually reach the database as the app role?"""
    try:
        with app_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception:
        log.exception("readiness check failed")
        return JSONResponse(status_code=503, content={"status": "not-ready"})


@app.get("/health/operations")
def operations():
    """Workers, beat, webhooks and Redis: the parts that fail without an error.

    For an uptime monitor, beside /health/ready. 503 names what is failing
    and nothing more; see services/ops_health.
    """
    problems = ops_health.failing()
    if problems:
        return JSONResponse(status_code=503, content={"status": "degraded", "failing": problems})
    return {"status": "ok"}


# Uploaded menu images, served straight off the disk they were written to.
#
# No nginx rule is involved. A browser asks for /images on the origin it
# loaded the page from; in development the Vite dev server forwards that here,
# the same way it forwards /api. Serving them from this process keeps the
# whole feature in one place and one bind mount, which is all a single node
# needs. Past one API host the answer is object storage, not a second copy of
# this folder -- see services/images, where that move is one class and one
# setting.
#
# The mkdir fails soft and the mount does not check the directory: a missing
# images folder should 404 a picture, never stop the API from booting.
# Every stored image is WebP, and the static handler names its type by asking
# Python's mimetypes, which on Windows reads the registry first. A registry
# that knows .webp as something else, or not at all, had these served as
# text/plain. Said once here, so the answer no longer depends on the machine.
mimetypes.add_type("image/webp", ".webp")

try:
    settings.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    log.warning(
        "could not create the images directory at %s", settings.IMAGES_DIR, exc_info=True
    )


# How long a browser, and anything caching in front of us, may keep an image
# without asking again. A year, and "immutable" on top of it, because these
# files genuinely are: services/images mints a random key per upload and
# writes it once, and a key is released only when no row refers to it any
# more. The bytes at a key never change, so there is nothing to revalidate.
#
# Without this the answer carried an ETag and no Cache-Control, which is the
# weakest useful form of caching: the browser still asks every time and is
# told 304. A menu with thirty photographs therefore cost thirty round trips
# on every view -- from a phone, on restaurant wifi -- to be told nothing had
# changed, and a CDN in front had no instruction to answer them instead.
IMAGE_CACHE_CONTROL = "public, max-age=31536000, immutable"


class ImmutableImages(StaticFiles):
    """StaticFiles that says how long its files keep.

    Starlette's NotModifiedResponse carries cache-control through, so setting
    it here covers the 304 as well as the 200.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = IMAGE_CACHE_CONTROL
        return response


app.mount(
    "/images",
    ImmutableImages(directory=settings.IMAGES_DIR, check_dir=False),
    name="images",
)

app.include_router(api_router)
