# Security test matrix — Zenoeats

Each risk mapped to the tests that cover it, how to run them, what was
observed on 25 September 2026, and what is still not covered. See
`SECURITY_AUDIT_REPORT.md` for the findings themselves.

## How to run

| Suite | Command | Observed |
|---|---|---|
| Backend (all) | `docker compose --profile app run --rm --no-deps -e ROOT_DOMAIN=zenoeats.local -e IMAGES_DIR=/tmp/images -e REDIS_RUNTIME_URL=redis://redis-runtime:6379/0 -e CELERY_BROKER_URL=redis://redis-broker:6379/0 -v "$PWD/backend:/srv" migrate sh -c "mkdir -p /tmp/images && python -m pytest tests -q"` | 890 passed, 1 skipped |
| Tenant isolation gates | `make rls` (or `pytest tests/test_rls_isolation.py -v`) | 7 passed (in the full run) |
| Web | `cd web && node --test tests/*.test.mjs` | 35/35 |
| Web static | `npm run lint && npx tsc --noEmit && npm run build` | pass |
| Secret scan (as CI) | `docker run --rm -v "$PWD:/src:ro" aquasec/trivy:0.74.0 fs --scanners secret --severity HIGH,CRITICAL --exit-code 1 /src` | exit 0 |
| Dependencies | `trivy fs --scanners vuln .`; CI: `pip-audit -r requirements.txt`, `npm audit --omit=dev --audit-level=high` | 0 findings |
| Static analysis | `bandit -r backend/app` (throwaway container) | 0 real medium/high |
| nginx | `nginx -t` on each config | pass |
| Workflow | `actionlint .github/workflows/ci.yml` | pass |

The skipped test is `test_the_published_secrets_are_the_ones_actually_published`.
It needs the whole repository mounted, which CI has. Run with the repo
mounted, it passed.

## Risk → tests

| Risk | Test cases (file::test) | Kind | Observed | Gap |
|---|---|---|---|---|
| **F-01** order token in logs (and `?t=` refused since e1862c9) | `test_notifications.py::test_a_guest_link_keeps_its_token_out_of_every_server_log`; `test_payment_reconciliation.py::test_an_order_token_works_from_a_header_and_opens_only_its_own_order`; `test_logsafe.py::test_the_access_log_never_records_a_query_string`, `::test_a_path_without_a_query_is_left_alone`, `::test_the_filter_is_on_uvicorns_access_logger`; `web/tests/ordertoken.test.mjs` (6) | unit, integration, authz-negative | pass; live probe: token absent from API and nginx logs; Chromium email-link flow OK | Production nginx log format proven by `nginx -t` and code only, not by a live production log |
| **F-02** open redirect | `web/tests/safenext.test.mjs` (3 tests, 11 bypass spellings) | unit | pass; also run in Chromium on the live site | Staff and admin sign-in not driven end to end in a browser (no known passwords); covered by the shared function |
| **F-04** brute force | `test_sign_in_throttling.py` (6): spent account refuses the right password; successes never counted; per-account isolation; no enumeration; HMAC key; admin budget | integration, negative | pass | Real distributed attack not simulated; limiter fail-open under Redis loss covered only by `/health/operations` |
| Per-IP limits, forged IPs | `test_client_ip.py`; live probe with forged `X-Forwarded-For`/`X-Real-IP` | integration, live | 429 after 10 | — |
| **F-08** CSV injection | `test_reports_csv.py` (6 payloads + 1 ordinary) | integration, negative | pass; payloads failed before the fix | Only the one CSV export exists |
| **F-03** bounds | `test_admin_restaurants.py::test_a_value_the_database_cannot_hold_is_a_422_not_a_500` (4) | integration, negative | pass; failed before the fix | — |
| **F-11** CSRF / origin | `test_origin_pinning.py::test_a_guest_cookie_is_refused_from_another_origin`, `::test_a_cross_site_form_cannot_deliver_an_order_body` + 6 existing | integration, negative | pass; cross-origin 200 before the fix → 403 | — |
| **F-10** published secrets | `test_startup_checks.py::test_a_secret_this_repository_publishes_is_refused` (3), `::test_a_development_database_password_is_refused` (2), `::test_the_published_secrets_are_the_ones_actually_published` | unit | pass | — |
| Test keys / unsafe prod config | `test_startup_checks.py` (27 total) | unit | pass | — |
| **F-05** non-root web | Build and run: `id -un` = nginx; Trivy DS-0002 cleared | manual, scanner, CI | pass | Asserted in CI since 42205b2 (the built image must not run as root; a root image was checked to fail) |
| **F-06** CI | `actionlint`; SHA-to-release check via GitHub API | manual | pass | Takes effect on the first CI run of the branch |
| **F-09** version disclosure | Live `curl -I` → `Server: nginx` | manual | pass | — |
| Tenant isolation | `test_rls_isolation.py` (7), `test_tenant_resolution.py`, `test_origin_pinning.py` | integration | pass | Staging should re-run the gates (STEPS §4.1) |
| Role / endpoint authz | `test_role_coverage.py` (full map, IT-support forbidden list) | integration | pass | — |
| Order ownership / IDOR | `test_guest_checkout.py` (token scope, forged token, other guest), `test_order_flow.py` | integration, negative | pass | — |
| Session expiry and revocation | `test_session_revocation.py` | integration | pass | — |
| Webhook signature, replay, dedupe | `test_stripe_webhook.py`, `test_clerk_customers.py`, `test_payment_reconciliation.py` | integration | pass | Live delivery through the real edge: staging |
| Server-side pricing, tax | `test_order_flow.py`, `test_combo_pricing.py`, `test_stripe_tax.py`, `test_platform_fee.py`, `test_order_delivery_fee.py` | integration | pass | — |
| Refunds and state transitions | `test_order_refund.py`, `test_order_board_actions.py` | integration | pass; live cancel and refund of order #1047 | — |
| Pickup PIN brute force | `test_pickup_pin.py`; live: wrong PIN → "4 attempts left" | integration, live | pass | — |
| Uploads | `test_images.py` | integration | pass | Malware scanning N/A (images are re-encoded) |
| Error sanitisation | `test_error_envelope.py::test_production_says_nothing_about_the_fault`; production boot: `/docs` `/openapi.json` `/redoc` 404 | unit, live | pass | — |
| PII in logs and Sentry | `test_logsafe.py`, `test_observability.py` | unit | pass | — |
| Health and monitoring | `test_ops_health.py` (13) | integration | pass | Uptime monitors not yet created |
| Backups and restore | `scripts/backup.sh` + `scripts/restore_drill.sh` run end to end (tampered archive refused) | manual | pass (local) | Production drill pending |
| Secrets in the tree, history, bundle | Trivy; history pattern scan; bundle grep | scanner | 0 real | Continuous: new CI `secrets` job |

## Outstanding coverage gaps

1. No automated browser suite in CI for the sign-in pages (F-02 is unit-tested only).
2. Nothing verifies production-only configuration: TLS, Cloudflare, firewall, live keys.
3. No load or DoS testing. Container limits are set (F-12) and were proven on a production-shape rehearsal, not under load.
4. No human penetration test yet.
