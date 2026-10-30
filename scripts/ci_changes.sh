#!/usr/bin/env bash
# Which parts of CI a change needs. Reads changed paths on stdin, one per line,
# and prints key=value lines for $GITHUB_OUTPUT. `--all` turns everything on, for
# a release tag, the weekly run, or a push whose base cannot be found.
#
# A path nothing here names (docs, the README) runs nothing but the secret scan.
# When a test starts reading a file outside its own folder, add that file here,
# or a change to it will skip the test that would have caught it.
set -euo pipefail

backend=false   # the backend job
frontend=false  # the frontend job
migrations=false
python_deps=false
web_deps=false
api_image=false
web_image=false

all() {
  backend=true frontend=true migrations=true python_deps=true web_deps=true
  api_image=true web_image=true
}

if [ "${1:-}" = "--all" ]; then
  all
else
  while IFS= read -r path; do
    case "$path" in
      # The pipeline itself: changing it is only proved by running all of it.
      .github/workflows/ci.yml | scripts/ci_changes.sh) all ;;
      backend/tests/*) backend=true ;;
      backend/requirements*.txt) backend=true python_deps=true api_image=true ;;
      backend/alembic/*) backend=true migrations=true api_image=true ;;
      backend/*) backend=true api_image=true ;;
      infra/postgres/*) backend=true migrations=true ;;
      # Read by backend tests: test_customer_accounts.py checks the password
      # length, and test_startup_checks.py the published CI secrets.
      web/login/customer-shared.ts) backend=true frontend=true web_image=true ;;
      scripts/verify_redesign.py) backend=true ;;
      web/tests/* | web/qa/*) frontend=true ;;
      web/package.json | web/package-lock.json) frontend=true web_deps=true web_image=true ;;
      web/*) frontend=true web_image=true ;;
    esac
  done
fi

printf '%s\n' \
  "backend=$backend" "frontend=$frontend" "migrations=$migrations" \
  "python_deps=$python_deps" "web_deps=$web_deps" \
  "api_image=$api_image" "web_image=$web_image"
