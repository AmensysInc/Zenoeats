# Security audit report — Zenoeats

| | |
|---|---|
| Date | 25 September 2026 |
| Scope | Repository at `main` 2328d15 (release `v1.0.0`), running development stack, production compose shape |
| Branch with fixes | `security-audit` (10 commits, not yet pushed) |
| Release gate status | **CONDITIONAL / PENDING VERIFICATION** — see [Release status](#release-status) |

This is an engineering assessment, not a guarantee of security. It was
performed by an AI assistant (Claude) on a local development machine. Every
code change it made needs human review before merge (see
[Human review required](#human-review-required)).

---

## Executive summary

The application's core security design is strong and was confirmed by
evidence rather than by reading alone:

- **Tenancy.** Tenant isolation is enforced in PostgreSQL row-level security,
  and seven CI gates prove it.
- **Identities.** Four identities are kept apart by a signed token-type claim,
  and every token's signing algorithm is pinned.
- **Payments.** Stripe charges are confirmed only by signed webhooks or by
  reading Stripe back, and are deduplicated at the database level.
- **Uploads.** Uploaded images are size-capped, pixel-capped and re-encoded.
- **Secrets.** No secret has ever been committed to this public repository.

**Twelve findings were raised: no Critical, four Medium, eight Low (two of
them Low–Medium). All twelve are now remediated** — ten in code with 39
new regression tests, F-12 (container limits) on 26 September, and F-07
(branch protection) as a repository setting the owner approved on 26
September.

The most consequential issues fixed:

- **F-01 (Medium):** a guest's 7-day order-view token (name, phone, address,
  pickup PIN) was written to API and nginx access logs every few seconds.
- **F-02 (Medium):** an open redirect on all three sign-in pages via
  `?next=/%09/evil.com`, proven in Chromium.
- **F-04 (Medium):** staff and admin sign-in were limited per IP only, so one
  account could be guessed at from many addresses without limit.

What is **not** verified is everything that exists only in production:
- the real domain, TLS and Cloudflare
- the VM's firewall and disk encryption
- the live Clerk and Stripe configuration
- Apple Pay
- a manual penetration test

---

## Scope, exclusions, limitations

**Inspected.** All 113 API endpoints, enumerated from the running app with
their auth dependencies. Also:
- the FastAPI backend, Celery tasks and both webhook handlers
- the React SPA and the `web/login` sign-in pages
- all three nginx configurations, both Dockerfiles, and both compose files
- the CI workflow and dependency manifests
- the full git history (read locally, never reproduced)
- the built browser bundle
- the running containers

**Tools.**
- Trivy 0.74.0: secrets, vulnerable dependencies and misconfiguration.
- Bandit 1.8.6, run in a throwaway container.
- `git log -p` pattern scan of all history.
- Playwright and Chromium, against the live stack.
- The project's own pytest and node test suites.

**Not done, and why.**

| Exclusion | Reason |
|---|---|
| Production, staging, the real domain | They do not exist yet |
| Live Clerk and Stripe accounts | Only test mode exists; nothing live was touched |
| Active exploitation of external systems | Out of bounds by the brief |
| Load and DoS testing | The laptop is memory-constrained (7.8 GB); it is not the deployment target |
| Manual penetration test by a human | Recommended before launch (see follow-ups) |
| GitHub repository settings | Observed; changing them needs owner approval |
| Clerk-hosted customer sign-in flows | No Clerk test credentials were available; they were reviewed as code only |

---

## System threat model

```
Internet ─▶ Cloudflare ─▶ nginx edge (TLS, HSTS, CF real-IP, fixed IP)
   ├─ storefront / portals (static SPA, CSP)       web container (nginx, now non-root)
   ├─ /api, /images, /health                       api (FastAPI, UID 10001)
   └─ admin.<domain> only                          platform API (host-pinned)
api ─▶ Postgres (owner / app NOBYPASSRLS + RLS / system narrow) ─▶ backups (age → R2)
    ─▶ redis-runtime (rate limits; fails open)  redis-broker ◀─ worker/beat (JSON only)
    ─▶ Stripe · Clerk · Google · Resend (fixed hosts; no user-controlled URLs)
```

| Asset | Threat | Primary control |
|---|---|---|
| Another restaurant's data | Cross-tenant read/write | Host → tenant; RLS; 7 gates |
| Customer PII and pickup PIN | Account takeover, IDOR, leakage | Ownership check; PIN encrypted; token scoped per order; F-01 |
| Money | Forged "paid", double charge, price tampering | Signed webhooks; Stripe read-back; server repricing; unique index |
| Staff and admin accounts | Brute force, CSRF, session theft | argon2; per-IP and (now) per-account limits; HttpOnly/Lax/Secure; origin pinning; revocation |
| Platform admin | Phishing, cross-subdomain abuse | `admin.` host only; F-02; audit log |
| Secrets | Leak via public repo or bundle | None committed; CI scan (F-06); startup refusals (F-10) |
| Supply chain | Malicious dependency or action | pip-audit / npm audit / Trivy; SHA-pinned actions (F-06) |

Trust boundaries:
- **Browser → edge:** TLS.
- **Edge → api:** forwarded headers are trusted only from nginx's address (PR #20).
- **api → Postgres:** role-separated.
- **Stripe and Clerk → webhooks:** signature verified over the raw body.
- **Operator → server:** SSH; out of scope.

No AI or LLM component exists in the application (no model SDK, no
prompts, no tools). The only AI involvement is in *development* (this
audit), which is why S12 matters.

---

## Findings

Severity is a triage priority, not proof of exploitability.

| ID | Sev | Category | Status | Title |
|---|---|---|---|---|
| F-01 | Medium | V10/V01 data exposure | **REMEDIATED** | Guest order-view token logged by API and nginx |
| F-02 | Medium | Open redirect | **REMEDIATED** | `?next=` bypass on customer, staff and admin sign-in |
| F-04 | Medium | V06 | **REMEDIATED** | Sign-in throttled per IP only; oracles unthrottled |
| F-07 | Medium | CI/CD, S12 | **REMEDIATED** | `main` unprotected: no required checks or review |
| F-08 | Low–Med | V02 injection | **REMEDIATED** | CSV formula injection in admin export |
| F-05 | Low–Med | Container | **REMEDIATED** | Web container ran as root |
| F-03 | Low | V02/V08 | **REMEDIATED** | Overlong admin input → 500 instead of 422 |
| F-06 | Low | V07 CI | **REMEDIATED** | CI token permissions implicit; actions tag-pinned; no secret scan |
| F-09 | Low | V04 | **REMEDIATED** | nginx version disclosed |
| F-10 | Low | V01/S13 | **REMEDIATED** | Published CI secrets would be accepted in production |
| F-11 | Low | V03 CSRF | **REMEDIATED** | Orders API not origin-pinned though guests use a cookie |
| F-12 | Low | Resource exhaustion | **REMEDIATED** | No container memory/CPU limits |

### F-01 — Guest order-view token written to access logs (Medium, REMEDIATED)

- **Where.**
  - `backend/app/services/notifications.py:120` (the email link)
  - `web/src/features/storefront/storefrontApi.ts:110` (the page sent `?t=` on every poll)
  - uvicorn's access log
  - `infra/nginx/production/zenoeats.conf.template` (default `combined` format logs `$request`)
- **Impact.** The token opens one guest order (name, phone, address, pickup
  PIN) for 7 days. Anyone who can read logs or log aggregation, or who is
  handed a support bundle, can collect food or read PII.
- **Preconditions.** Read access to logs, which are routinely shipped and
  kept.
- **Evidence.** A fake token sent as `?t=FAKE-TOKEN-PROBE-123` appeared in
  the API log and the nginx log.
- **Remediation** (commit ec5a548):
  - The email link carries the token in `#t=`, which is never sent to a server.
  - The page sends it in an `X-Order-Token` header.
  - `?t=` is no longer accepted at all (e1862c9): no client can put the
    token back in a URL. The page still reads `?t=` from an old link in the
    browser and sends it in the header.
  - uvicorn's access log drops query strings (`logsafe.DropQueryStrings`).
  - The production nginx logs `$uri` through its own format.
- **Tests.**
  - `test_notifications.py::test_a_guest_link_keeps_its_token_out_of_every_server_log`
  - `test_payment_reconciliation.py::test_an_order_token_works_from_a_header_and_opens_only_its_own_order`
  - `test_logsafe.py` (3 new)
  - `web/tests/ordertoken.test.mjs` (6)
- **Verified live.** The same probe after the fix: the token appears in
  neither log, and the path is still logged. In Chromium, opening
  `/orders/<id>#t=<token>` on a cookieless device shows the order, clears the
  token from the address bar, and sends every API call with the header and
  no `?t=`.

### F-02 — Open redirect on all three sign-in pages (Medium, REMEDIATED)

- **Where.**
  - `web/login/customer-shared.ts:36`
  - `web/login/admin-login.ts:21`
  - `web/login/staff-login.ts:24`
- **Impact.** A link to our own sign-in page ends on an attacker's page after
  a successful sign-in: the classic "session expired, re-enter your password"
  phish.
- **Evidence.** Chromium resolved `?next=/%09/example.com`, `/%0A/…` and
  `/%0D/…` to `http://example.com/` while the old check passed them.
- **Remediation** (91bc9ca). A shared `web/login/safe-next.ts` rejects
  control characters and backslashes, resolves the value with the browser's
  URL parser, and requires the same origin with a path that doesn't start
  with `//`. That last rule also catches `/.//evil`.
- **Tests.** `web/tests/safenext.test.mjs` covers 11 bypass spellings plus
  legitimate paths. It was re-run in Chromium against the live site.

### F-04 — Sign-in limited per address only (Medium, REMEDIATED)

- **Where.**
  - `backend/app/api/v1/restaurant.py` (`/restaurant/login`, change-password, change-email, invite, reset-password)
  - `backend/app/api/v1/admin.py` (`/admin/login`)
- **Impact.** Credential stuffing spread over many IPs (cheap behind
  Cloudflare) against one owner or admin account was unlimited. A stolen
  session could brute-force the current password through change-password.
  Failed sign-ins for unknown emails were not logged.
- **Remediation** (019e2b0):
  - A per-account failure budget of 50 per 15 minutes on staff and admin
    sign-in. It is HMAC-keyed, so Redis holds no email address.
  - Unknown emails are counted and answered identically, so the budget
    reveals nothing about which accounts exist.
  - Per-user limits on change-password and change-email (10 per 15 minutes),
    staff invitations (30 an hour) and resets (20 an hour).
  - Failed sign-ins for unknown emails are now logged, masked.
- **Tests.** `test_sign_in_throttling.py` (6):
  - the right password is refused once the budget is spent
  - successes are never counted
  - one account under attack leaves another alone
  - real and invented addresses are indistinguishable at every step
  - the key holds no address
  - admin sign-in has the same budget
- **Needs human review:** the threshold trade-off (see below).

### F-07 — `main` has no branch protection (Medium, REMEDIATED)

- **Evidence.** `GET repos/haswanth13901/zenoeats-mvp/branches/main/protection`
  returns 404 "Branch not protected", and there are no rulesets.
- **Impact.** Code can reach `main`, and therefore the published images,
  without CI passing and without review. This includes AI-generated changes,
  so the human review gate of S12 doesn't exist in practice.
- **Recommended.** Protect `main`:
  - require a pull request
  - require the status checks `secrets`, `backend`, `frontend` and `docker`
  - disallow force pushes and deletion

  With a single maintainer, "required approvals" would block self-merge;
  requiring PRs plus checks, with the owner as the reviewer of record, is the
  practical gate.
- **Remediated** 26 September, with the owner's approval. Read back from
  the API:
  - a pull request is required, with zero approvals (so the owner can merge
    their own, as the reviewer of record)
  - the checks `secrets`, `backend`, `frontend` and `docker` must pass on an
    up-to-date branch
  - **enforced for admins too.** The AI works through the owner's token, so
    without this it could still push straight to `main`
  - force pushes and deletion are refused

  Also switched on: Dependabot alerts and security updates, secret scanning,
  and push protection.

### F-08 — CSV formula injection in the platform export (Low–Medium, REMEDIATED)

- **Where.** `backend/app/api/v1/admin.py` `platform_reports_csv`.
- **Impact.** A restaurant owner sets their own trading name. A name
  beginning with `=`, `+`, `-`, `@`, tab or carriage return executes as a
  formula (HYPERLINK, DDE) when a platform admin opens the export.
- **Remediation** (a439c72). Such text cells are prefixed with `'`, OWASP's
  recommended neutraliser.
- **Tests.** `test_reports_csv.py` (7). The 6 payload cases **failed before
  the fix** and pass after.

### F-05 — Web container ran as root (Low–Medium, REMEDIATED)

- **Where.** `web/Dockerfile` (Trivy DS-0002 HIGH).
- **Remediation** (f1e440e). `USER nginx`, owning only `config.js`,
  `/etc/nginx/zenoeats` and the cache, with the pid file in `/tmp`.
- **Verified.** Built and run: master and workers are UID 101, `config.js`
  and the CSP are generated at start, pages are served, and Trivy no longer
  reports DS-0002. The live stack runs it.

### F-03 — Overlong admin input caused a 500 (Low, REMEDIATED)

- **Where.** `backend/app/schemas/api.py` `CreateRestaurantIn`.
- **Evidence.** `currency: "USDOLLARS"` → 500 `StringDataRightTruncation`.
  Development showed the SQL; production hides it.
- **Remediation** (fbb81ab, f5d05b3). `currency` must match `^[A-Z]{3}$`;
  `tagline` and `admin_email` are bounded to their columns.
- **Tests.** 4 parametrised cases; all 4 failed before the fix.
- **Note.** The negative run against the unfixed code created two test
  restaurants in the dev database. They were removed by hand, and the test
  now cleans up after itself even against unfixed code.

### F-06 — CI supply-chain hygiene (Low, REMEDIATED)

- **Remediation** (c15e6ad):
  - top-level `permissions: contents: read`
  - `checkout`, `setup-python` and `setup-node` pinned to full SHAs, each
    checked to equal its release tag
  - a `secrets` job running Trivy 0.74.0, pinned, that fails on high or
    critical findings
  - `.github/dependabot.yml` for actions, pip and npm
- **Verified.** `actionlint` passes, and a dry run on the tracked files is
  clean.
- **Also done** (220a777, 42205b2):
  - every base and service image is pinned by digest (Dockerfiles, compose,
    the CI service images, Trivy, the restore drill), with Dependabot watching
    `docker` and `docker-compose`
  - CI asks each built image who it runs as, and fails before publishing if
    either is root (a stock root image was checked to fail)

### F-09 — nginx version disclosure (Low, REMEDIATED)

`server_tokens off` in all three nginx configurations (ec5a548). Verified
live: `Server: nginx`.

### F-10 — Published CI secrets accepted in production (Low, REMEDIATED)

The CI Fernet key and two test session secrets are public by design, as are
the `*_dev_pw` database passwords. With `ENV=production` the API now refuses
them (9387b78). A drift test proves the list matches the files that publish
them.

### F-11 — Orders API not origin-pinned (Low, REMEDIATED)

- **Evidence.** A guest cookie sent with another storefront's `Origin` got 200.
- **Why no order could be forged before:**
  - the cookie is SameSite=Lax
  - CORS is off in production
  - FastAPI refuses a JSON body that isn't declared `application/json`, so a
    form can't send one (now tested)
- **Remediation** (c2c597d). The orders router is pinned with the same
  dependency the staff, platform and profile APIs use. A cross-origin guest
  request now gets 403.
- **Verified live.** A full guest checkout and payment, and a cancel and
  refund through the kitchen board, work after the change.

### F-12 — No container resource limits (Low, REMEDIATED)

- **Where.** `docker-compose.prod.yml` set no `mem_limit` or `cpus`, so a
  runaway process (for example a pathological image decode) could starve
  Postgres on a shared VM.
- **Remediation** (fc16228). Caps sized for the recommended 2 vCPU / 4 GB VM,
  each overridable in `.env`:

  | Service | Memory | CPUs |
  |---|---|---|
  | api | 1 GB | 1.5 |
  | worker | 768 MB | 1.0 |
  | beat | 256 MB | — |
  | redis-runtime | 320 MB | — |
  | web | 128 MB | — |
  | nginx | 128 MB | — |
  | migrate | 512 MB | — |

  Postgres and the Redis broker are deliberately uncapped: they hold data,
  and an out-of-memory kill there costs more than it saves.
- **Verified** on a production-shape rehearsal running the caps and the
  pinned images. Every service was healthy inside its limit (api 246 MB of
  1 GB with two processes, worker 162 of 768, beat 106 of 256), and both
  health endpoints were OK through the HTTPS edge.

---|---|
| api | 1 GB |
| worker | 512 MB |
| beat | 256 MB |
| redis-runtime | 320 MB |
| web | 128 MB |
| nginx | 128 MB |

Leave Postgres unlimited.

---

## Check-by-check status

Legend:
- **PASS:** verified by evidence.
- **FIXED:** a finding was remediated.
- **NV:** needs verification in production.
- **N/A:** not applicable.
- **BLOCKED:** needs owner approval.

### Primary categories

| # | Check | Status | Evidence |
|---|---|---|---|
| V01 | Hardcoded secrets | PASS + FIXED (F-06, F-10) | Trivy tree scan; history scan of 145k lines for 14 key patterns (0 real); bundle scan (no server keys, no source maps); `.env` files untracked and never committed |
| V02 | Server-side input validation | PASS + FIXED (F-03, F-08) | 41 request models enumerated; unbounded fields checked individually (pattern validators); quantities 1–50; uploads re-encoded; JSON-only bodies |
| V03 | AuthN / AuthZ / ownership | PASS + FIXED (F-11) | 113 routes with dependencies; `test_role_coverage.py` map; order ownership read in code and tested; RLS gates; host-pinned admin |
| V04 | Debug, errors, CORS | PASS + FIXED (F-09) | Prod error bodies generic (`test_error_envelope.py`); `/docs` `/openapi.json` `/redoc` 404 with `ENV=production` (verified by booting the image); no CORS in production |
| V05 | SQL/NoSQL injection | PASS | Only f-string SQL uses fixed internal table lists (Bandit B608 false positives, verified); everything else parameterised or ORM |
| V06 | Rate limiting | PASS + FIXED (F-04) | Every public and credential endpoint limited; PIN per-order lockout plus per-staff budget; forged-IP bypass tested live earlier (429 after 10) |
| V07 | Dependencies / supply chain | PASS + FIXED (F-06) | Trivy: 0 vulnerabilities in `requirements.txt` and `package-lock.json`; pip-audit and npm audit in CI; actions SHA-pinned |
| V08 | Fail-closed errors | PASS | Paid only from a signed webhook or Stripe read-back; unique index against double success; PIN counter committed before refusal; wallet registration never blocks activation; limiter fails open (accepted, monitored) |
| V09 | Independent read-through | DONE, human review pending | Every changed line re-read; see "Human review required" |
| V10 | Security logging | PASS + FIXED (F-01, F-04) | Failed sign-ins masked and fingerprinted; unknown emails now logged; webhook signature failures logged; platform audit log; Sentry scrubbing (`test_observability.py`); tokens out of access logs |

### Supplementary checks

| # | Check | Status | Evidence |
|---|---|---|---|
| S01 | HTTPS, TLS, secure cookies, mixed content | PASS (code, rehearsal) / NV (prod) | TLS 1.2/1.3, HSTS, HTTP→HTTPS proven on the production-shape rehearsal; cookies Secure in prod; CSP has no `http:` sources |
| S02 | Admin and internal routes independently gated | PASS | `admin.` host only (`test_origin_pinning.py`); separate `ADMIN_USERS` identity; health endpoints disclose names only |
| S03 | Swagger / diagnostics restricted | PASS | Verified 404 in production mode; `/health/operations` returns names, no counts |
| S04 | Paid features server-side | PASS | Storefront customisation switch enforced in `services/storefront.py`, tested; no subscription tiers exist |
| S05 | File uploads | PASS | 8 MB (nginx and app), 40 MP cap, decompression-bomb guard, re-encode to WebP, random keys, traversal guard, staff-only, rate limited |
| S06 | Sessions, logout, CSRF, replay | PASS | JWT exp and type claims; `sessions_valid_after` revocation (`test_session_revocation.py`); HttpOnly/Lax/Secure; origin pinning (F-11); Clerk handles customer token refresh |
| S07 | Demo and test credentials | PASS / process | `seed.py` never in production (STEPS §8); dev passwords now refused in production (F-10) |
| S08 | Server-side totals | PASS | Cart repriced from the database; minor-unit integers; discount capped; tax server-side (`test_order_flow.py`, `test_combo_pricing.py`, `test_stripe_tax.py`) |
| S09 | Signed webhooks, replay, idempotency | PASS | Stripe `construct_event` and svix over the raw body, 5-minute tolerance, unique event ids, `IntegrityError` → 200 duplicate |
| S10 | Environment separation | PASS (code) / NV | `ALLOW_TEST_KEYS`, key-mode checks, per-environment `.env` from `make_prod_env.py`; separate staging Clerk instance is a process step |
| S11 | AI agent input handling | N/A | No AI or LLM component in the application |
| S12 | Human review gate for AI changes | PASS (F-07 fixed) | PR plus four required checks, enforced for admins; this audit's own changes await the owner's review in PR #21 |
| S13 | Startup config validation | PASS + FIXED (F-10) | `startup_checks.py` refuses unsafe production configuration, test keys, published secrets and dev DB passwords |

### Additional production checks

| Check | Status | Evidence / gap |
|---|---|---|
| Multi-tenant isolation | PASS | Host-only tenant; RLS forced on all 27 tenant tables; 7 gates; drill checks RLS after restore |
| AI/RAG security | N/A | No AI component |
| Security headers, CSP, HSTS, frames | PASS | CSP enforced (it blocked an injected inline script during this audit); X-Frame-Options DENY; nosniff; Referrer-Policy; HSTS at the edge |
| SSRF | PASS | All outbound calls go to fixed hosts; no user-supplied URLs fetched |
| Open redirects | FIXED (F-02) | Stripe return URLs built server-side (#20 era) |
| Path traversal / file access | PASS | Image keys regex-validated and resolved under the root |
| Deserialization / command injection | PASS | Celery JSON only; no `pickle`, `subprocess`, `os.system` or `eval` in `app/` |
| DB least privilege, backups, restore | PASS (local) / NV (prod) | Three-role model; `backup.sh` plus `restore_drill.sh` proven locally; production drill pending |
| Container / VM | PASS + FIXED (F-05, F-12) / NV | Both images non-root (asserted in CI); no internal ports in prod; limits set; host firewall, SSH and disk encryption unverified |
| CI/CD | FIXED (F-06, F-07) | Tag-based release; manual deploy; required checks; digest-pinned images |
| Concurrency | PASS | Webhook dedupe; `FOR UPDATE` on PIN; idempotency keys; unique successful payment per order |
| Data protection | PASS / NV | PIN Fernet-encrypted; PII masked in logs; Sentry scrubbed; backups age-encrypted; TLS; disk encryption at rest depends on the VM provider |
| Resource exhaustion | PASS + FIXED (F-12) | Body limits; list endpoints capped (50–200); report range ≤ 366 days; per-restaurant Stripe Tax cap |

---

## Modified files

| Area | Files |
|---|---|
| Backend code | `backend/app/api/v1/{orders,restaurant,admin,router}.py`, `backend/app/core/{logsafe,ratelimit,startup_checks}.py`, `backend/app/main.py`, `backend/app/schemas/api.py`, `backend/app/services/notifications.py` |
| Backend tests | `test_sign_in_throttling.py` (new), `test_reports_csv.py` (new), `test_admin_restaurants.py`, `test_origin_pinning.py`, `test_startup_checks.py`, `test_logsafe.py`, `test_notifications.py`, `test_payment_reconciliation.py` |
| Web | `web/login/safe-next.ts` (new), `web/login/{customer-shared,admin-login,staff-login}.ts`, `web/src/features/storefront/{orderToken,storefrontApi}.ts`, `web/src/services/{api,apiClient}.ts`, `web/src/components/layout/Guards.tsx`, `web/Dockerfile`, `web/nginx.conf` |
| Web tests | `web/tests/ordertoken.test.mjs` (new), `web/tests/safenext.test.mjs` (new) |
| Infra / CI | `infra/nginx/nginx.conf`, `infra/nginx/production/zenoeats.conf.template`, `.github/workflows/ci.yml`, `.github/dependabot.yml` (new) |

35 files, +818/−42. No new runtime dependencies.

## Verification results

| Command / check | Result |
|---|---|
| Backend suite (containerised, full) | **890 passed, 1 skipped** (the drift test; it passes with the repo mounted and runs in CI) |
| Web `node --test tests/*.test.mjs` | **35/35** |
| `tsc --noEmit`, `npm run lint`, `vite build` | Pass |
| nginx `-t` (prod template, web, dev edge) | Pass |
| `actionlint` | Pass |
| Trivy secrets on tracked files, HIGH/CRITICAL | 0 |
| Trivy dependency vulnerabilities | 0 |
| Trivy Dockerfile HIGH/CRITICAL | 0 (after F-05) |
| Bandit medium/high | 0 real (2 verified false positives) |
| Live stack after rebuild | Token absent from logs; email-link flow works; guest order paid; cancel and refund works; web runs as nginx |

---

## Human review required

1. **F-04 thresholds.** 50 failures per 15 minutes per account is a
   deliberate trade-off between brute-force resistance and an attacker's
   ability to lock an owner out. Confirm it fits your support model; staff
   who are already signed in are unaffected.
2. **F-01.** `?t=` was removed from the API outright (no production emails
   exist yet). Any link in an old test email that still carries `?t=` works
   only through the page, which moves the token into the header.
3. **F-11.** Any future cross-origin browser client of `/orders` would now be
   refused. Native apps and servers send no `Origin` and are unaffected.
4. **F-10.** `PUBLISHED_SECRETS` is a denylist and must track CI's values;
   the drift test enforces it.
5. **Accepted designs, re-confirm consciously:**
   - The rate limiter fails **open** when `redis-runtime` is down (alerted by `/health/operations`).
   - Staff temporary passwords are emailed (decided 2026-09-21).
   - Customer Clerk tokens without `azp` are accepted (only a holder of the Clerk secret can mint those).
6. **Every commit on `security-audit` was written by an AI.** Review the diff
   line by line before merging.

## Remaining risks and prioritised follow-ups

| # | Action | Owner | Priority |
|---|---|---|---|
| 1 | Review PR #21 line by line and merge it; tag `v1.0.1` | You | Before launch |
| 2 | Provider firewall: 80/443 from Cloudflare only, SSH from your IP (STEPS §4.1) | You | Before launch |
| 3 | Staging rehearsal incl. CSP report-only, webhooks, Apple Pay (STEPS §10) | You | Before launch |
| 4 | Manual penetration test of the staging environment by a human tester | External | Before or soon after launch |
| 5 | Re-check the container limits once the VM is chosen (only if it is not 2 vCPU / 4 GB) | You | At deploy |

Done since the first version of this report (26 September): branch
protection and GitHub security features (F-07), container limits (F-12),
digest pinning, the non-root CI check, removing `?t=`, and deleting the stale
`backend/.env`.

## Release status

**CONDITIONAL / PENDING VERIFICATION.**

Every finding is remediated, and the code is **ready for human release
review** in PR #21. The release is still not cleared, because:

- **PR #21 needs your review and merge.** That is the gate F-07 now enforces.
- **Production-only evidence is missing:**
  - the real domain, TLS and Cloudflare
  - the VM firewall and disk encryption
  - the live Clerk and Stripe configuration
  - webhook delivery through the real edge
  - a restore drill of production backups
  - a staging rehearsal
  - a human penetration test

The steps that need your authorization are listed in
`SECURITY_DEPLOYMENT_CHECKLIST.md` under "Sign-off".
