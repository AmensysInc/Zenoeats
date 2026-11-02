"""A production API refuses to start on a configuration that is unsafe.

Each case is one thing that, left wrong, is an incident rather than a failed
deploy: forgeable operator sessions, customers who cannot sign in, orders
that can never be confirmed.
"""

import pytest
from cryptography.fernet import Fernet

from app.config import Settings
from app.core import startup_checks


def _production(**overrides) -> Settings:
    values = dict(
        ENV="production",
        ROOT_DOMAIN="zenoeats.com",
        DATABASE_URL_APP="postgresql://a",
        DATABASE_URL_SYSTEM="postgresql://s",
        DATABASE_URL_MIGRATE="",
        FIELD_ENCRYPTION_KEY=Fernet.generate_key().decode(),
        SESSION_SECRET="x" * 44,
        STRIPE_SECRET_KEY="sk_live_abc",
        STRIPE_PUBLISHABLE_KEY="pk_live_abc",
        ALLOW_TEST_KEYS=False,
        STRIPE_CONNECT_WEBHOOK_SECRET="whsec_abc",
        ADMIN_USERS="ops@zenoeats.com:aGFzaA==",
    )
    values.update(overrides)
    # _env_file=None: the developer's own .env must not leak into these cases.
    return Settings(_env_file=None, **values)


def test_a_complete_production_configuration_starts():
    assert startup_checks.configuration_problems(_production()) == []
    startup_checks.enforce(_production())


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"SESSION_SECRET": ""}, "SESSION_SECRET"),
        ({"SESSION_SECRET": "changeme"}, "SESSION_SECRET"),
        ({"FIELD_ENCRYPTION_KEY": "replace_me_with_a_real_fernet_key"}, "FIELD_ENCRYPTION_KEY"),
        ({"APPLE_OAUTH_CLIENT_ID": "com.zenoeats.web"}, "APPLE_OAUTH_TEAM_ID"),
        ({"STRIPE_SECRET_KEY": "sk_test_replace_me"}, "STRIPE_SECRET_KEY"),
        ({"STRIPE_CONNECT_WEBHOOK_SECRET": ""}, "STRIPE_CONNECT_WEBHOOK_SECRET"),
        ({"ADMIN_USERS": ""}, "ADMIN_USERS"),
        ({"ROOT_DOMAIN": "zenoeats.local"}, "ROOT_DOMAIN"),
        ({"DATABASE_URL_MIGRATE": "postgresql://owner"}, "DATABASE_URL_MIGRATE"),
    ],
)
def test_production_refuses_to_start(overrides, fragment):
    with pytest.raises(RuntimeError) as caught:
        startup_checks.enforce(_production(**overrides))
    assert fragment in str(caught.value)


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"STRIPE_SECRET_KEY": "sk_test_abc"}, "STRIPE_SECRET_KEY is a test key"),
        ({"STRIPE_SECRET_KEY": "rk_test_abc"}, "STRIPE_SECRET_KEY is a test key"),
        ({"STRIPE_PUBLISHABLE_KEY": "pk_test_abc", "STRIPE_SECRET_KEY": "sk_test_abc"},
         "STRIPE_PUBLISHABLE_KEY, STRIPE_SECRET_KEY are test keys"),
    ],
)
def test_production_refuses_test_keys(overrides, fragment):
    """Live everywhere but one key still takes orders and collects nothing."""
    with pytest.raises(RuntimeError) as caught:
        startup_checks.enforce(_production(**overrides))
    assert fragment in str(caught.value)


def test_staging_may_run_on_test_keys_when_it_says_so():
    staging = _production(
        ALLOW_TEST_KEYS=True,
        STRIPE_SECRET_KEY="sk_test_abc",
        STRIPE_PUBLISHABLE_KEY="pk_test_abc",
    )
    assert startup_checks.configuration_problems(staging) == []


@pytest.mark.parametrize("allow_test_keys", [False, True])
def test_stripe_keys_from_different_modes_are_refused(allow_test_keys):
    """The browser and the server would be talking to different Stripes."""
    problems = startup_checks.configuration_problems(
        _production(
            ALLOW_TEST_KEYS=allow_test_keys,
            STRIPE_SECRET_KEY="sk_live_abc",
            STRIPE_PUBLISHABLE_KEY="pk_test_abc",
        )
    )
    assert any("different modes" in p for p in problems)


@pytest.mark.parametrize("secret", sorted(startup_checks.PUBLISHED_SECRETS))
def test_a_secret_this_repository_publishes_is_refused(secret):
    """Each is readable by anyone; a copy into production is no secret."""
    name = "FIELD_ENCRYPTION_KEY" if secret.endswith("=") else "SESSION_SECRET"
    with pytest.raises(RuntimeError) as caught:
        startup_checks.enforce(_production(**{name: secret}))
    assert f"{name} is a value published in this repository" in str(caught.value)


def test_the_published_secrets_are_the_ones_actually_published():
    """So the list cannot drift from the files it describes."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    published = "".join(
        (root / f).read_text(encoding="utf-8")
        for f in (".github/workflows/ci.yml", "scripts/verify_redesign.py")
        if (root / f).exists()
    )
    if not published:
        pytest.skip("repository files are not mounted here")
    for secret in startup_checks.PUBLISHED_SECRETS:
        assert secret in published


@pytest.mark.parametrize("name", ["DATABASE_URL_APP", "DATABASE_URL_SYSTEM"])
def test_a_development_database_password_is_refused(name):
    url = "postgresql+psycopg2://zenoeats_app:app_dev_pw@postgres:5432/zenoeats"
    with pytest.raises(RuntimeError) as caught:
        startup_checks.enforce(_production(**{name: url}))
    assert "development password" in str(caught.value)


def test_every_problem_is_reported_at_once():
    """Fixing one variable per failed deploy is a slow way to find five."""
    problems = startup_checks.configuration_problems(
        _production(SESSION_SECRET="", STRIPE_SECRET_KEY="", ADMIN_USERS="")
    )
    assert len(problems) == 3


def test_development_only_logs():
    startup_checks.enforce(_production(ENV="development", SESSION_SECRET=""))
