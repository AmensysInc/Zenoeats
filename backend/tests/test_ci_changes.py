"""Which parts of CI a change runs (scripts/ci_changes.sh).

The failure this guards against is silent: a filter that misses a path skips the
tests that would have caught the change, and the build is green.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EVERYTHING = {
    "backend", "frontend", "migrations", "python_deps", "web_deps", "api_image", "web_image",
}

# The full path: on Windows a bare "bash" can start WSL's instead of the one found.
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(not BASH, reason="needs bash")


def _runs(*args: str, stdin: str = "") -> set[str]:
    done = subprocess.run(
        [BASH, "scripts/ci_changes.sh", *args],
        cwd=ROOT, input=stdin, capture_output=True, text=True, check=True,
    )
    pairs = dict(line.split("=") for line in done.stdout.split())
    assert set(pairs) == EVERYTHING
    return {name for name, value in pairs.items() if value == "true"}


@pytest.mark.parametrize(
    "path, expected",
    [
        ("README.md", set()),
        ("docs/operations/STEPS_BEFORE_PRODUCTION.md", set()),
        (".env.example", set()),
        ("backend/app/api/deps.py", {"backend", "api_image"}),
        ("backend/tests/test_terms_acceptance.py", {"backend"}),
        ("backend/requirements.txt", {"backend", "python_deps", "api_image"}),
        ("backend/alembic/versions/0057_x.py", {"backend", "migrations", "api_image"}),
        ("infra/postgres/01-roles.sql", {"backend", "migrations"}),
        ("web/src/types.ts", {"frontend", "web_image"}),
        ("web/tests/phone.test.mjs", {"frontend"}),
        ("web/package-lock.json", {"frontend", "web_deps", "web_image"}),
        # Read by backend tests, so the backend has to run for them too.
        ("web/login/customer-shared.ts", {"backend", "frontend", "web_image"}),
        ("scripts/verify_redesign.py", {"backend"}),
        # The pipeline proves itself only by running all of it.
        (".github/workflows/ci.yml", EVERYTHING),
        ("scripts/ci_changes.sh", EVERYTHING),
    ],
)
def test_a_change_runs_what_it_needs_and_no_more(path, expected):
    assert _runs(stdin=f"{path}\n") == expected


def test_several_changes_add_up():
    assert _runs(stdin="README.md\nbackend/tests/test_x.py\nweb/tests/y.test.mjs\n") == {
        "backend", "frontend",
    }


def test_all_turns_everything_on():
    assert _runs("--all") == EVERYTHING
