"""Backend verification against disposable local services only.
Requires PostgreSQL 127.0.0.1:5544 with infra/postgres roles, pgcrypto and citext,
and Redis 127.0.0.1:6391. External provider credentials are disabled.
Usage: .venv/Scripts/python.exe scripts/verify_redesign.py [pytest arguments]
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from cryptography.fernet import Fernet

root = Path(__file__).resolve().parents[1]
env = os.environ.copy()
env.update(
    ENV="development", ROOT_DOMAIN="zenoeats.local",
    SESSION_SECRET="redesign-isolated-tests-only-no-production-access",
    FIELD_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    REDIS_RUNTIME_URL="redis://127.0.0.1:6391/0",
    CELERY_BROKER_URL="redis://127.0.0.1:6391/1",
    CELERY_RESULT_BACKEND="redis://127.0.0.1:6391/2",
)
for role, password in [("app", "app_dev_pw"), ("system", "system_dev_pw"), ("migrate", "migrate_dev_pw")]:
    env["DATABASE_URL_" + role.upper()] = (
        "postgresql+psycopg2://zenoeats_" + role + ":" + password + "@127.0.0.1:5544/zenoeats"
    )
for key in (
    "STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY", "STRIPE_CONNECT_WEBHOOK_SECRET",
    "GOOGLE_MAPS_API_KEY", "GOOGLE_MAPS_BROWSER_KEY", "RESEND_API_KEY", "SENTRY_DSN", "ADMIN_USERS",
):
    env[key] = ""
# A test-only signing secret lets webhook tests exercise signature verification.
# API credentials stay blank, so no real payment operation can occur.
env["STRIPE_CONNECT_WEBHOOK_SECRET"] = "whsec_redesign_qa_only"
with tempfile.TemporaryDirectory(prefix="zenoeats-qa-images-") as images:
    env["IMAGES_DIR"] = images
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=root / "backend", env=env, check=True)
    # Some existing integration tests require the local demo tenant.
    # This runner pins every database URL to the disposable port above.
    subprocess.run([sys.executable, "scripts/seed.py"], cwd=root / "backend", env=env,
                   stdout=subprocess.DEVNULL, check=True)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *(sys.argv[1:] or ["-q", "--tb=short"])],
        cwd=root / "backend", env=env,
    )
sys.exit(result.returncode)
