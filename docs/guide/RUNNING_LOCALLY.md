# Running ZenoEats on your machine

The short path to a working local copy. The
[README](../../README.md#running-it) is the full guide.

## 1. Hostnames

Wildcard subdomains have to resolve. In `/etc/hosts`:

```
127.0.0.1  zenoeats.local
127.0.0.1  spicehouse.zenoeats.local
127.0.0.1  admin.zenoeats.local
```

## 2. Settings

```bash
cp .env.example .env
```

The defaults work for development. Nothing needs a real key to start.

## 3. Start it

```bash
# Everything in Docker — closest to production
docker compose --profile app up -d --build

# Or infrastructure only, app native — faster to iterate
docker compose up -d postgres redis-broker redis-runtime
```

Then open **`https://spicehouse.zenoeats.local:8443`**. Always go through nginx
on `:8443`: development is HTTPS only, and `localhost:3000` cannot resolve a
tenant.

The local certificate is self-signed, so the browser will warn. Trusting the
local CA once removes it — and until you do, anything that needs a real
browser session will not work.

## 4. Tests

```bash
# Backend, from the repo root
docker compose run --rm --no-deps -v "$PWD:/repo" -w /repo/backend api \
  pytest tests/ -q --ignore=tests/test_rls_isolation.py

# Tenant isolation, which must pass on its own
docker compose run --rm --no-deps -v "$PWD:/repo" -w /repo/backend api \
  pytest tests/test_rls_isolation.py -q

# Frontend
cd web && npx tsc --noEmit && npm run lint && node --test tests/*.test.mjs
```

Mount the whole repo, not just `backend/` — some tests read files above it.

`test_stripe_webhook.py` fails locally without a Stripe secret. That is
expected and unrelated to any change you are making.

## 5. Migrations

```bash
docker compose run --rm --no-deps -v "$PWD:/repo" -w /repo/backend migrate \
  alembic upgrade head
```

A new migration must reverse. CI checks it, and so should you:

```bash
alembic upgrade head && alembic downgrade -1 && alembic upgrade head
```

## Things that will waste an hour

- **A stale `__pycache__`** inside the container mount will run yesterday's
  code and make a working change look broken. `find backend -name __pycache__
  -type d -exec rm -rf {} +`, or run with `PYTHONDONTWRITEBYTECODE=1`.
- **Frontend changes need the web image rebuilt** when running the full Docker
  profile: `docker compose --profile app up -d --build web`.
- **`localhost:3000/api/...` returns HTML**, not JSON — the web container has
  no API behind it. Go through `:8443`.
- **The table is `menu_items`**, not `items`, if you are writing raw SQL in a
  test.
