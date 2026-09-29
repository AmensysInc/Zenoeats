# Steps before production

Everything that has to be done, decided or fixed before Zenoeats takes real
orders and real money. Work top to bottom; later sections assume earlier ones.

**Priority labels**

| Label | Meaning |
|---|---|
| **[BLOCKER]** | Do not launch without it. Security, money or data-loss risk. |
| **[LAUNCH]** | Needed for a credible launch; small risk if it slips a few days. |
| **[SOON]** | Fine to go live without; schedule it for the first weeks. |

Items marked *(code)* need a change in this repository. Everything else is
configuration, accounts or process on your side. Ticked items are done; each
says what changed and where, so it can be checked.

### Go-live: what is left (updated 29 September 2026)

**The code is done.** Release **`v1.2.0`** is the one to deploy: `v1.0.1`
(security-audited, all twelve findings fixed, see
`docs/security/SECURITY_AUDIT_REPORT.md`), the email work of `v1.1.0`, and
the restaurant phone number and legal fixes of `v1.2.0` (`CHANGELOG.md`).
On 29 September it passed an end-to-end check against the running stack: a
guest order paid by Stripe test card, the kitchen board, ready, cancel and
refund, and the emails for each. CI publishes it as
`ghcr.io/haswanth13901/zenoeats-mvp/{api,web}:v1.2.0`. `main` is protected:
every change needs a pull request and green CI.

**Nothing below needs code.** It is accounts, the domain, the server and the
lawyer. Work through it in this order: each step needs the ones before it.
Tick the boxes here as you go.

**Critical path:** Stripe verification (start today) → domain → server →
rehearsal → first real order. About a week, gated mostly by Stripe's review
and the lawyer, not by engineering.

#### Making it a website like any other

Today the app runs only on the development laptop, at `*.zenoeats.local`,
which exists nowhere else. The optional `https://…:8443` door (README §7) is
for looking at it with a padlock on that one machine: it needs a
certificate installed by hand, and no other device can reach it.

To be opened by anyone, on any device, with the normal padlock, it needs
three things. None of them is code, and the production setup already serves
HTTPS, redirects HTTP and sends HSTS:

1. **A real domain** (for example `zenoeats.com`) → Step 3, §4.0
2. **A certificate every browser already trusts**: Cloudflare provides it
   for free once the domain is on Cloudflare, so there is nothing to install
   anywhere → Step 3, §4.0, §4.1
3. **A computer on the internet that is always on**, answering on port 443
   (no `:8443`) → Step 4, §11

Two ways to get the third:

| | **A. A real server (recommended)** | **B. A tunnel from the laptop** |
|---|---|---|
| What | A small Linux VM runs the published `v1.2.0` images (§11) | Cloudflare Tunnel (`cloudflared`) publishes the app running on the laptop at the domain |
| Cost | Domain about $10/year, plus a 2 vCPU / 4 GB VM (about $8–24/month, depending on provider) | Domain about $10/year only |
| Always on | Yes | Only while the laptop is on and awake |
| Fit for real users | Yes, once this list is done | **No**: a demo only. The 8 GB laptop stalls under load, and it is not the deployment target |
| Setup | 2–3 hours | About 1 hour. The laptop's `.env` must name the real domain as `ROOT_DOMAIN`, with a wildcard route to the tunnel |

Either way, **start in test mode.** Generate the server's `.env` with
`make_prod_env.py --staging --domain <domain>`, which allows Stripe and
Clerk test keys and nothing else. The result is a real public HTTPS website
on your domain that any phone can open, taking only Stripe test cards: the
§10 rehearsal. Switch to live keys (`make_prod_env.py` without `--staging`,
or `ALLOW_TEST_KEYS=false` with the live keys) once Stripe has approved the
account and the rest of this list is ticked.

#### Step 1 — Start today (the longest waits)

- [ ] **Activate the Stripe live account**: business details, bank,
      identity, Connect platform profile. Stripe's review takes 1–3 days.
      → §3.4
- [ ] **Save the backup private key.** Copy
      `C:\Users\lenovo\zenoeats-secrets\backup-age-identity.txt` into a password
      manager and onto an offline copy (USB). It is the only copy; without it
      no backup can ever be read. 5 minutes. → §5
- [ ] **Legal details**: the legal entity name, trading address, a contact
      email somebody reads, the country (and US state), and the deletion
      response windows (suggested 7 / 30 days). Those fill the 5
      `PLACEHOLDER`s in `web/legal/*.html`; then **a lawyer reviews all 4
      pages** and the draft banners come off. Days, for the lawyer. → §9

#### Step 2 — Accounts that need no domain

- [ ] **Cloudflare R2** bucket `zenoeats-backups` (90-day lifecycle, 30-day
      bucket lock, bucket-scoped token) and a **healthchecks.io** check.
      Then test a real backup and restore. 30 minutes. → §5
- [ ] **Sentry** project; copy its DSN. 10 minutes. → §6
- [ ] **UptimeRobot** account (the monitors come after the domain).
      5 minutes. → §6

#### Step 3 — The domain

- [ ] **Buy the domain** and add it to Cloudflare (Free plan, nameservers at
      the registrar). 1 hour plus propagation. → §4.0
- [ ] **DNS records** `@`, `*` and `admin` → the VM's IP, proxied; SSL/TLS
      **Full (strict)**; a **Cloudflare Origin Certificate** for `<domain>`
      and `*.<domain>`. 30 minutes. → §4.0, §4.1
- [ ] **Clerk production instance**: primary domain, Clerk's DNS records (DNS
      only, grey cloud), live keys, webhook. 1 hour. → §3.1
- [ ] **Google, Apple and Facebook sign-in** credentials in Clerk. Apple needs
      the $99/year developer programme; all three need the legal pages live
      on the root domain. Hours to days. → §3.2
- [ ] **Stripe**: live keys, and the **Connect webhook** at
      `https://<domain>/api/v1/webhooks/stripe/connect`. 20 minutes. → §3.4
- [ ] **SendGrid**: authenticate the sending domain (its CNAME records),
      set the key and sender. 30 minutes. → §3.6

#### Step 4 — The server

- [ ] **Rent the VM**: Ubuntu 24.04, 2 vCPU / 4 GB. Provider firewall: 80/443
      from Cloudflare's ranges only, 22 from your IP only. 30 minutes.
      → §4.1, §11
- [x] **Tag a release** that includes the email work and the legal fixes.
      *Done:* `v1.2.0` (29 September); CI publishes its images.
- [ ] **Deploy `v1.2.0`**: Docker, clone and check out the tag,
      `make_prod_env.py`, fill in the keys, `--check` until clean, the
      certificate, `up -d`. 1–2 hours. → §11
- [ ] **Backups on**: the timer enabled, the first backup lands in R2, and a
      **restore drill** passes on a machine other than the server.
      30 minutes. → §5
- [ ] **Uptime monitors** on `https://<domain>/health/ready` and
      `/health/operations`. 10 minutes. → §6

#### Step 5 — Before announcing

- [ ] **First restaurant**: create it in the admin portal with its phone
      number for customers, create its owner, Stripe onboarding, activate
      (refused without the phone number), then "Refresh Stripe" should show
      "Apple Pay active · Google Pay active". → §3.4, §8
- [ ] **Rehearsal** in the production shape. → §10
  - sign-up and sign-in, by email and socially
  - a card payment with 3-D Secure
  - Apple Pay and Google Pay on real phones
  - order → kitchen → PIN, and a refund
  - the emails arrive: the confirmation, ready or on the way and delivered,
    a cancellation with its refund, a staff invitation and a password reset
  - no CSP errors in the console
- [ ] **Penetration test** by a second person or a professional. →
      `docs/security/SECURITY_DEPLOYMENT_CHECKLIST.md` §7
- [ ] **One real low-value order** end to end with a live card, then refund
      it. Then announce.
- [ ] **Sign-off** table in `docs/security/SECURITY_DEPLOYMENT_CHECKLIST.md` filled in.

#### Can wait until after launch

- The weekly Dependabot pull requests (17 opened on the first run).
  Routine; each runs CI.
- Two medium dependency alerts (`uuid`, `stream-json`). Build tools only,
  not code customers download; fixable with an npm `overrides` entry.
- Apple "Hide My Email" relay registration. → §3.3
- The restaurant agreement (fees, disputes, refunds), drafted with the
  lawyer. → §9

---

## 1. Repository and release hygiene

- [x] **[BLOCKER]** Commit the working tree. *Done:* everything is committed
      and reached `main` through pull requests (#8–#21). The tree is clean.
- [x] **[BLOCKER]** CI green on the commit you deploy: backend tests, the RLS
      isolation gates (`tests/test_rls_isolation.py`), the reversible-migration
      check, web lint/typecheck/build. *Green* on `main` (110e07e) and on the
      `v1.0.1` tag itself. Branch protection now requires it for every change.
- [x] **[LAUNCH]** Tag the release and deploy images built from that tag
      only. *Done:* `v1.0.0` (25 September), `v1.0.1` (26 September, with the
      security audit, PR #21), `v1.1.0` (28 September, the emails), then
      **`v1.2.0`** (29 September, the restaurant phone number and legal
      fixes). **Deploy `v1.2.0`.** CI publishes
      `ghcr.io/haswanth13901/zenoeats-mvp/api:v1.2.0` and `…/web:v1.2.0`,
      which are what `API_IMAGE` and `WEB_IMAGE` name (§11). The repository and
      both images are public, so the server pulls them without logging in.
      Neither image contains a secret: every key arrives at runtime from
      `.env`.

      A later release is cut the same way, from `main` once CI is green:

      ```powershell
      git checkout main; git pull
      git tag v1.0.2; git push origin v1.0.2
      ```
- [x] **[LAUNCH]** `main` is protected (26 September): a pull request and
      the four CI checks (`secrets`, `backend`, `frontend`, `docker`) are
      required, admins included, and force pushes and deletion are refused.
      Dependabot alerts and updates, secret scanning and push protection are
      on.
- [x] **[LAUNCH]** `README.md` → "Before real money" was stale (it said rate
      limiting was unused). *Fixed:* it now points here as the source of truth.

---

## 2. Code changes required before launch

These were found by reading the code. All of them are now fixed, including the
three that needed a business decision (Stripe Tax, email, refunds in the
Stripe Dashboard).

### 2.1 Security

- [x] **[BLOCKER] Rate limits could be bypassed by forging an IP header.** *(code)*
      The limiter trusted the left-most `X-Forwarded-For` entry, which the
      client writes. *Fixed:* `app/core/ratelimit.py::_client_ip` believes
      forwarding headers only from our own proxies (`TRUSTED_PROXY_CIDRS`),
      prefers `X-Real-IP`, and walks `X-Forwarded-For` from the right. Both
      nginx configs now overwrite `X-Forwarded-For` instead of appending, and
      the production edge takes the real visitor IP from Cloudflare only on
      connections from Cloudflare's ranges. Verified live: 12 sign-in attempts
      with forged IPs were refused at the limit. Tests: `tests/test_client_ip.py`.
- [x] **[BLOCKER] Refuse to start with unsafe configuration.** *(code)*
      *Fixed:* `app/core/startup_checks.py`. With `ENV=production` the API
      refuses to start, listing every problem at once, if `SESSION_SECRET` is
      under 32 characters, `FIELD_ENCRYPTION_KEY` is not a valid key, any of
      `CLERK_JWKS_URL` / `CLERK_ISSUER` / `CLERK_SECRET_KEY` or the Stripe
      secrets are missing, `ADMIN_USERS` is empty, `ROOT_DOMAIN` is `.local`,
      `AUTH_DEV_BYPASS` is on, or `DATABASE_URL_MIGRATE` is present.
      Development only logs. Tests: `tests/test_startup_checks.py`.
- [x] **[LAUNCH] Security headers.** *(code)* *Fixed:* HSTS (one year,
      subdomains) at the production edge. A Content-Security-Policy is written
      by the web container at start (`web/docker-entrypoint.d/40-zenoeats-config.sh`)
      from Clerk's and Stripe's published requirements, with this environment's
      Clerk host read from the publishable key. `CSP_EXTRA_IMG_SRC` adds image
      origins; `CSP_REPORT_ONLY=true` reports without blocking.
      **Still to do:** see §10 — run staging with `CSP_REPORT_ONLY=true` first
      and check the browser console before enforcing.
- [x] **[LAUNCH] Uvicorn proxy trust.** *(code)* *Fixed:* `FORWARDED_ALLOW_IPS`
      set in `backend/Dockerfile` and compose. Verified on Linux: behind a
      trusted proxy uvicorn now sees `https` and the real client address.
- [x] **[SOON] Dependency updates and audits.** *(code)* *Fixed:* pip-audit found
      87 advisories in cryptography, Pillow, PyJWT, python-multipart, Starlette
      and pytest. Upgraded (FastAPI 0.141.1 / Starlette 1.6.0, cryptography
      50.0.1, Pillow 12.3.0, PyJWT 2.14.0, python-multipart 0.0.31,
      SQLAlchemy 2.0.52, pytest 9.0.3); full suite and a live boot pass; audit
      now clean. CI runs `pip-audit` and `npm audit --omit=dev` on every build.
- [x] **[SOON] PII in logs.** *(code)* *Fixed:* failed staff and admin sign-ins
      log `s***@example.com#3f9c1a2b` (masked address + keyed fingerprint)
      instead of the address. `app/core/logsafe.py`, `tests/test_logsafe.py`.
      The `ADMIN_USERS` configuration errors still name the entry, on purpose.

### 2.2 Deployment shape

- [x] **[BLOCKER] The web image could not be promoted between environments.** *(code)*
      *Fixed:* the bundle carries no key. The web container writes `/config.js`
      at start from `CLERK_PUBLISHABLE_KEY` (validated, so nothing injected can
      become script), and the app reads it before `VITE_CLERK_PUBLISHABLE_KEY`,
      which remains the development fallback. CI builds without a key and
      checks `config.js` is in the bundle.
- [x] **[BLOCKER] Migrations ran on every API start with owner credentials.** *(code)*
      *Fixed:* a one-off `migrate` compose service runs `alembic upgrade head`
      and exits; `api` and `worker` wait for it. Runtime containers no longer
      receive `DATABASE_URL_MIGRATE`, `DOCKER_DATABASE_URL_MIGRATE` or
      `POSTGRES_PASSWORD`. Alembic refuses to run without the owner URL, and a
      production API refuses to start with it. **In production:** run the same
      image with `alembic upgrade head` as a release step before rolling out.
- [x] **[BLOCKER] nginx was hard-wired to `zenoeats.local`.** *(code)*
      *Fixed:* `infra/nginx/production/zenoeats.conf.template` (domain from
      `ROOT_DOMAIN`), used by `docker-compose.prod.yml`. HTTPS only, HTTP→HTTPS
      redirect, unknown hosts and unknown TLS names refused, forwarding headers
      overwritten, Cloudflare real-IP (`cloudflare-realip.conf`). It also
      routes `/images/` to the API — previously every menu photo would have
      broken in production. Tested in the nginx image with a throwaway
      certificate.
      Run: `docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile app up -d`
- [x] **[LAUNCH] More than one API process.** *(code)* *Fixed:* `WEB_CONCURRENCY`
      (`API_WORKERS` in compose, default 2). Verified two workers on Linux.
      Size workers × database pools under Postgres' `max_connections`.

### 2.3 Payments and money

- [x] **[LAUNCH] Receipt email must be real.** *(code)* *Fixed:* a
      `…@pending.local` placeholder is never sent to Stripe as `receipt_email`;
      payment still goes through. `clerk_customers.receipt_address`.
- [x] **[BLOCKER] Platform fee mechanism.** *(code)* *Fixed:* `PLATFORM_FEE_BPS`
      and `PLATFORM_FEE_FIXED_MINOR` become a Stripe `application_fee_amount`
      on each charge, capped at the order total. Default 0.
      **Still to decide:** the fee itself. Tests: `tests/test_platform_fee.py`.
- [x] **[BLOCKER] Tax.** *(code)* *Fixed — Stripe Tax:* each restaurant has a
      tax mode. `FLAT` keeps `tax_rate_bps`; `STRIPE_TAX` calculates every
      quote and order on the restaurant's own connected account, at its pickup
      address, with product tax code `txcd_40060003` (Food for Immediate
      Consumption). Discounts are spread across lines exactly; identical carts
      reuse a cached calculation (Stripe bills per calculation) and uncached
      calculations are capped per restaurant. The PaymentIntent carries the
      calculation id; the payment webhook records a tax transaction and the
      refund webhook records reversals, each exactly once. Switching a
      restaurant to Stripe Tax, and activating one, is refused until its
      address is complete and its Stripe tax settings are active.
      `app/services/stripe_tax.py`, `tests/test_stripe_tax.py`; a real
      calculation request was accepted by Stripe's test API.
      **On your side:** §3.5.
- [x] **[LAUNCH] Refunds and cancellations.** *(code)* Cancelling an order on
      the kitchen board offers **Cancel and refund**, a full refund to the
      card, Zenoeats' fee included (untick it for a no-show the restaurant is
      charging for). Cancelled orders not yet refunded wait under **Refunds to
      issue** on the board. Anything else, such as a partial refund or a refund
      after collection, is issued in the restaurant's own Stripe Dashboard,
      where disputes also live. Either way the `charge.refunded` webhook
      reconciles the payment and, for Stripe Tax restaurants, reverses the tax
      (PR #17). **On your side:** tell restaurants (§9).
- [x] **[SOON] Receipts from Zenoeats.** *(code)* *Fixed:* the worker
      emails an order confirmation once the payment webhook marks the order
      paid — exactly once per order, retried on rate limits and outages. It
      never contains the pickup PIN; it links to the order page where the
      signed-in customer sees it. Sent through SendGrid since PR #52.
      `app/services/notifications.py`, `tests/test_notifications.py`.
      **On your side:** §3.6.
- [x] **[SOON] Emails after the confirmation.** *(code)* *Fixed (PR #53):*
      ready to collect, on its way, delivered, cancelled (with the refund and
      its 10 to 14 business days when one was issued) and refund issued, for
      refunds made later from the board or Stripe's dashboard. Each is claimed
      in the `sent_emails` table before it goes, because SendGrid has no
      idempotency key, so none is sent twice and a board refund Stripe reports
      back is not announced again. `app/services/order_emails.py`,
      `tests/test_order_emails.py`.

### 2.4 Customer sign-in (Clerk)

- [x] **[LAUNCH] Apple and Facebook buttons.** *(code)* *Fixed:* the sign-in and
      sign-up pages render a button for each provider enabled in Clerk
      (Google, Apple, Facebook), in each brand's style. Enabling a provider in
      the Clerk dashboard is all it takes.
- [x] **[LAUNCH] "We still need your email" step.** *(code)* *Fixed:* after a
      social sign-up Clerk cannot finish, the sign-up page collects exactly
      what is missing (email, name, terms consent) and verifies an unverified
      email by code.
- [x] **[LAUNCH] Two-step verification.** *(code)* *Fixed:* sign-in handles
      authenticator-app, text, email and backup codes, with a switch between
      them, including after a social sign-in.
      **Still to do:** click through each on staging; these were typechecked
      and built, not browser-tested.
- [ ] **[SOON] Apple "Hide My Email".** Customers may sign in with
      `@privaterelay.appleid.com` addresses. Mail only reaches them if the
      sending domain is registered with Apple (see §3.3).

### 2.5 Operations gaps

- [x] **[LAUNCH] Error tracking.** *(code)* *Fixed:* Sentry for the API and
      Celery, off until `SENTRY_DSN` is set. Request bodies, local variables
      and PII are never sent; PINs, client secrets, emails and notes are
      scrubbed. Errors carry the same reference the customer is shown.
      `app/core/observability.py`, `tests/test_observability.py`.
- [x] **[SOON] Table growth.** *(code)* *Fixed:* an hourly `sweep_retention`
      task removes expired idempotency keys, and processed/ignored webhook
      deliveries older than `WEBHOOK_EVENT_RETENTION_DAYS` (365). Unprocessed
      and failed deliveries are never removed. `app/services/retention.py`.
- [x] **[SOON] Dead setting.** *(code)* *Fixed:* `CORS_ORIGINS` removed.
- [x] **[SOON] Staff onboarding emails.** *(code)* *Fixed:* inviting someone
      emails them the restaurant, their role, the sign-in link and — for a
      new login — the temporary password.
      **Decided (2026-09-21):** the password is emailed, reversing the earlier
      rule that the admin pass it on by hand. The cost is that whoever reads
      that email can sign in first, and a forwarded invitation now opens the
      account. What bounds it: the password must be replaced at first
      sign-in and stops working then, it is only included while that is still
      pending, and it is sealed with `FIELD_ENCRYPTION_KEY` while it sits in
      the Celery queue so the broker's on-disk log never holds it in plain
      text. Since PR #54 a password reset is emailed the same way, as are a
      welcome on accepting, "has joined" to the inviter, a role change, a
      removal, and "a refund didn't go through" to admins and managers
      (`app/services/staff_emails.py`). A new owner created by the super
      admin is still given the password on screen only.

### 2.6 Admin portal (found by an end-to-end pass over the live portal)

A full pass over the platform portal against a running stack: 61 checks
passed, and everything below was found and has since been fixed.

- [x] **[BLOCKER] Creating a restaurant answered 500.** *(code)* The starter
      item types were written with the system role, which is not granted the
      menu tables at all, so "Create as draft" failed after the restaurant
      row had already been written. *Fixed:* creation is one tenant
      transaction -- restaurant, order counter and item types together -- so
      it either all lands or none of it does, and a taken subdomain is
      answered as a conflict rather than a crash.
      `tests/test_admin_restaurants.py`.
- [x] **[BLOCKER] An owner could not be given a second restaurant.** *(code)*
      Issuing an owner reset that person's password, so an operator who
      already ran one restaurant had their working login replaced. *Fixed:*
      an account already in use is invited instead: the membership waits at
      INVITED, the password is untouched, and they accept from inside the
      portal (rule 27). The portal shows "Owner invited" with no password to
      pass on.
- [x] **[LAUNCH] Storefront links pointed at a dev address.** *(code)* The
      "Open storefront" link was built from a build-time domain and port
      3000, so in production every link was dead. *Fixed:* the address is
      derived from the page the portal is open on. `web/src/utils/storefront.ts`.
- [x] **[BLOCKER] Signing out did not end the session.** *(code)* Sign-out
      deleted the cookie; a token copied beforehand kept working for up to
      eight hours. *Fixed:* `users.sessions_valid_after` (migration 0015) is
      stamped on admin sign-out, on a staff password change and on a
      super-admin reset, and every request refuses a token issued before it.
      Staff sign-out stays per-device, so one person signing out does not
      sign out the tablet on the pass; "Sign out all devices" in the portal
      header ends every session the account holds, for a lost phone.
      `tests/test_session_revocation.py`.
- [x] **[BLOCKER] The platform API answered on every restaurant subdomain.**
      *(code)* Restaurants and the portal share one root domain, so browsers
      treat them as one site: a page on any storefront could call the
      super-admin API with a signed-in operator's cookie attached, and CORS
      allowed exactly that. *Fixed:* the platform API exists on
      `admin.<root>` and nowhere else; both operator APIs refuse a request
      whose Origin is another host; production allows no cross-origin API
      access at all (every page that calls the API is served from the same
      hostname); and nginx no longer serves the portal's pages off the admin
      hostname, so there is no super-admin sign-in form to phish with on a
      thousand addresses. `tests/test_origin_pinning.py`.
- [x] **[BLOCKER] Stripe onboarding was an open redirect.** *(code)* The
      return and refresh URLs arrived as query parameters, so the onboarding
      link could hand the operator to any address with Stripe's credibility
      behind it. *Fixed:* the server builds both from the portal's own
      hostname, https in production. `tests/test_stripe_onboarding.py`.
- [x] **[LAUNCH] Connected accounts were contactable at the platform.**
      *(code)* A new connected account used the platform admin's address, so
      Stripe's verification requests, failed payouts and dispute deadlines
      came to us instead of the restaurant. *Fixed:* the owner's address is
      used, and onboarding a restaurant with no owner yet is refused with a
      message saying to create the owner first.
- [x] **[LAUNCH] "Gross volume" added different currencies.** *(code)* The
      tile summed minor units across restaurants and printed dollars, so one
      restaurant pricing in another currency made the headline figure
      meaningless. *Fixed:* one figure per currency.

### 2.7 Found by the end-to-end pass (25 September 2026, PR #20)

The pass itself: a guest ordered in a real browser and paid with Stripe's
test card. The order reached the kitchen board, was marked ready, refused a
wrong PIN and completed with the right one. A second order was cancelled
with a real test-mode refund. The admin portal and the sign-in rate limit
(forged IPs included) held, and 859 backend and 26 web tests passed. It
found no bug in the ordering path. What it did find:

- [x] **[BLOCKER] Production would start on test keys.** *(code)* An API on
      `sk_test_` keys boots, takes orders and collects nothing. *Fixed:* with
      `ENV=production` the startup check refuses Stripe or Clerk test keys
      unless `ALLOW_TEST_KEYS=true` (staging only). A test publishable key
      beside a live secret key is refused everywhere.
      `tests/test_startup_checks.py`.
- [x] **[BLOCKER] Monitors could never see `/health/ready` fail.** *(code)* The
      production nginx routed only an exact `/health` to the api. Every other
      health path fell through to the web container, whose page fallback
      answers 200 for anything. *Fixed:* every `/health…` path goes to the api.
      Confirmed through the edge on a production-shaped stack.
- [x] **[BLOCKER] Production passwords were optional.** *(code)* The roles
      script put `*_dev_pw` on a fresh database, Redis had no password, and
      every service published a host port. *Fixed:* see §4 (PostgreSQL,
      Redis) and §11. `docker-compose.prod.yml` refuses to start without real
      passwords.
- [x] **[LAUNCH] Apple Pay and Google Pay were never offered.** *(code)* Wallet
      buttons appear only on a domain registered on the connected account
      running the charge, and nothing registered one. *Fixed:* activation and
      "Refresh Stripe" register `<slug>.<root domain>` on the restaurant's own
      account, idempotently and never blocking activation. The portal shows
      the result. Checked against Stripe's test API.
      `tests/test_wallet_domain.py`.
- [x] **[LAUNCH] Proxy trust narrowed.** *(code)* The api believed forwarding
      headers from every private range, so any container on the network could
      claim to be any client. *Fixed:* nginx has a fixed address
      (`EDGE_IP`, default 172.31.250.10), and the api trusts that alone.
      Proven live: forged `X-Real-IP` from another container is rate-limited
      as that container.
- [x] **[LAUNCH] Silent failures now have an endpoint.** *(code)*
      `/health/operations`: see §6.
- [x] **[SOON] Test debris.** *(code)* The Stripe webhook tests left every event
      at RECEIVED (150 in the development database), which read as a stuck
      worker. They clean up after themselves now.
- [x] **[LAUNCH] The data deletion page contradicted the product.** It said
      deletion was by email only, after PR #19 had added self-service
      closing. *Fixed:* it leads with Manage profile → Personal details →
      Close my account, and email is the fallback.

---

### 2.8 Security audit (25–26 September 2026, PR #21, release v1.0.1)

A full audit of the ten primary and thirteen supplementary categories found
twelve issues, none critical, and **all twelve are fixed**. They include:

- the guest order-view token was being written to access logs
- an open redirect on the sign-in pages
- sign-in limits were per IP only, not per account
- CSV formula injection in the admin export
- the web container ran as root
- no container limits
- CI supply-chain hygiene
- no branch protection

Every check, finding, test and remaining production-only step is in
`docs/security/SECURITY_AUDIT_REPORT.md`, `docs/security/SECURITY_TEST_MATRIX.md` and
`docs/security/SECURITY_DEPLOYMENT_CHECKLIST.md`. The last one holds the sign-off table
that must be filled in before go-live.

---

## 3. Third-party accounts (new production accounts)

### 3.1 Clerk (production instance)

- [ ] **[BLOCKER]** Create a **production** instance in a new Clerk account (or
      promote a new application to production).
- [ ] **[BLOCKER]** Set the **primary domain** to the parent domain
      (e.g. `zenoeats.com`), so one sign-in covers every restaurant subdomain.
- [ ] **[BLOCKER]** Add the **DNS records** Clerk lists (typically CNAMEs for
      `clerk.`, `accounts.`, `clkmail.` and the two DKIM records) and wait for
      verification. Clerk's emails and its Frontend API depend on them.
- [ ] **[BLOCKER]** Enable **Email address** + **Password**, with email
      verification by **code** (the pages expect 6-digit codes, not links).
- [ ] **[BLOCKER]** Copy keys into production config: `pk_live_…` →
      `CLERK_PUBLISHABLE_KEY` on the web container (`VITE_CLERK_PUBLISHABLE_KEY`
      in `.env` when using compose); `sk_live_…` → `CLERK_SECRET_KEY`; JWKS URL
      → `CLERK_JWKS_URL`; Frontend API URL → `CLERK_ISSUER`.
- [ ] **[LAUNCH]** **Webhook**: endpoint `https://<yourdomain>/api/v1/webhooks/clerk`,
      events `user.created`, `user.updated`, `user.deleted`; signing secret →
      `CLERK_WEBHOOK_SECRET`. Requires the Celery worker to be running.
- [ ] **[LAUNCH]** Keep **bot protection** on (the sign-up page includes the
      `clerk-captcha` element it needs).
- [ ] **[SOON]** *Optional:* send Clerk's customer emails through SendGrid
      (*code done*). Subscribe the webhook to `email.created`, then turn off
      **Delivered by Clerk** for the verification and reset codes, one at a
      time, testing each. Only once the webhook reaches production: after
      that, it is the only way those codes arrive. README, "Email".
- [ ] **[LAUNCH]** Brand Clerk's **email templates** (verification and reset
      codes) with the Zenoeats name and sender.
- [ ] **[LAUNCH]** Decide **session lifetime** and inactivity timeout in Clerk.

### 3.2 Google, Facebook and Apple sign-in (production credentials)

Production Clerk instances need your own credentials for each provider. Every
provider page in Clerk shows the **redirect URI** to paste into the provider.

- [ ] **[LAUNCH] Google**: Google Cloud console → OAuth consent screen
      (publish it, add privacy policy and terms URLs) → Credentials → OAuth
      client *Web application* → Clerk's redirect URI → client ID and secret into
      Clerk.
- [ ] **[LAUNCH] Facebook**: developers.facebook.com → create app (Facebook
      Login) → *Valid OAuth Redirect URIs* = Clerk's redirect URI → add
      **Privacy Policy URL** and **Data deletion instructions URL** → App ID and
      Secret into Clerk → switch the app to **Live**.
- [ ] **[LAUNCH] Apple** (Apple Developer Program, $99/year): App ID with
      *Sign in with Apple* → **Services ID** (web domain + return URL = Clerk's
      redirect URI) → **Key** with Sign in with Apple, download the `.p8` once →
      enter Services ID, Team ID, Key ID and the key contents in Clerk.

### 3.3 Apple private email relay

- [ ] **[SOON]** Apple Developer → *Sign in with Apple for Email Communication*
      → register the domains that send mail to customers, so messages to
      `privaterelay.appleid.com` addresses are delivered.

### 3.4 Stripe (live, new account)

- [ ] **[BLOCKER]** Activate the **platform** account (business details, bank,
      identity) and complete the **Connect platform profile**.
- [ ] **[BLOCKER]** Live keys → `STRIPE_SECRET_KEY` and `STRIPE_PUBLISHABLE_KEY`.
      Both must be live, or both test on staging: the API refuses a mix, and
      refuses test keys in production (§2.7).
- [ ] **[BLOCKER]** Create the webhook **on the Connect tab** (not the account
      tab): `https://<yourdomain>/api/v1/webhooks/stripe/connect`, events
      `payment_intent.succeeded`, `payment_intent.payment_failed`,
      `payment_intent.canceled`, `charge.refunded`, `account.updated`; its
      signing secret → `STRIPE_CONNECT_WEBHOOK_SECRET`.
- [ ] **[BLOCKER]** Onboard each restaurant through the admin portal's Stripe
      onboarding so it gets a **real** connected account with
      `charges_enabled`. Never carry over `acct_REPLACE_WITH_TEST_ACCOUNT`
      from the seed.
- [x] **[LAUNCH] Apple Pay / Google Pay.** *(code)* Checkout uses
      `automatic_payment_methods`. Wallet buttons only appear on domains
      registered as **payment method domains**. With direct charges that
      registration is per connected account, per restaurant subdomain.
      *Done automatically* on activation and on "Refresh Stripe" (§2.7).
      **On your side:** after go-live, press "Refresh Stripe" on each
      restaurant in the admin portal and check it shows "Apple Pay active ·
      Google Pay active". No domain association file needs hosting; Stripe
      does Apple's merchant validation.
- [ ] **[LAUNCH]** Decide **statement descriptors** (what shows on the
      customer's card statement). Stripe's own **email receipts** are not
      used (decided 2026-09-28): Zenoeats emails the confirmation and every
      refund, and no longer gives Stripe a `receipt_email`, which in live mode
      would make it send a second copy of each. Leave "email customers" off
      in the connected accounts' settings.
- [ ] **[LAUNCH]** Confirm restaurants understand they handle **disputes** and
      receive **payouts** directly (merchant of record).

### 3.5 Stripe Tax (for each restaurant using it)

- [ ] **[BLOCKER]** The restaurant, in its own Stripe Dashboard (Tax), sets its
      **head office address** and **preset tax code**, so its tax settings
      become `active`. Zenoeats refuses to switch it to Stripe Tax until then.
- [ ] **[BLOCKER]** The restaurant adds a **tax registration** for its state.
      Without one Stripe calculates **zero tax** (`not_collecting`) — it does
      not error — so this is the step most likely to be silently missed.
- [ ] **[BLOCKER]** In the super admin portal: fill in the restaurant's
      **pickup address**, set tax to **Stripe Tax**, save (the save checks
      Stripe), then activate.
- [ ] **[LAUNCH]** Confirm the tax code. `txcd_40060003` fits prepared food;
      a restaurant mostly selling packaged goods may need another.
- [ ] **[LAUNCH]** Understand the cost: Stripe Tax is billed per calculation
      and per transaction on the restaurant's account. Test mode allows 1,000
      calculations a day.

### 3.6 SendGrid (email)

- [ ] **[LAUNCH]** Create a SendGrid account, authenticate your sending domain
      under **Settings → Sender Authentication**, and add the CNAME records it
      lists until verified.
- [ ] **[LAUNCH]** Set `SENDGRID_API_KEY` (a key with *Mail Send* access only),
      `EMAIL_FROM` (on the authenticated domain, e.g.
      `Zenoeats <orders@zenoeats.com>`) and optionally `EMAIL_REPLY_TO`.
- [ ] **[LAUNCH]** Check SendGrid's current plan and daily sending limit
      against the orders you expect: every paid order sends at least one email.
- [ ] **[LAUNCH]** Set `STOREFRONT_URL_TEMPLATE` (`https://{slug}.{root_domain}`)
      so links in emails point at the right storefront.
- [ ] **[SOON]** Register the sending domain with Apple's private email relay
      (§3.3) so confirmations reach "Hide My Email" customers.

---

## 4. Infrastructure

### 4.0 The domain (left until last on purpose; everything in §3.1, §3.2, §3.6 and TLS needs it)

- [ ] **[BLOCKER] Buy the domain and put it on Cloudflare.** Any registrar
      (Cloudflare Registrar sells at cost). In Cloudflare, **Add a site**,
      choose the Free plan, and at the registrar replace the nameservers with
      the two Cloudflare gives you. Wait for "Active".
- [ ] **[BLOCKER] Records**, in Cloudflare → DNS, all pointing at the VM's
      public IPv4 (§11):

      | Type | Name | Content | Proxy |
      |---|---|---|---|
      | A | `@` (the root domain) | VM IP | Proxied (orange) |
      | A | `*` (every restaurant subdomain) | VM IP | Proxied |
      | A | `admin` | VM IP | Proxied |

      Clerk's records (§3.1) and Resend's (§3.6) are added later and must be
      **DNS only (grey)**, never proxied. The `admin` record is required: the
      platform portal and its API answer on `admin.<domain>` and nowhere else
      (see 2.6). A restaurant is only ever one level deep (`spicehouse.<domain>`),
      which is what Cloudflare's free certificate covers.
- [ ] **[BLOCKER] SSL/TLS settings**: mode **Full (strict)**; Edge
      Certificates → **Always Use HTTPS** on, **Minimum TLS 1.2**.
- [ ] **[BLOCKER] Tell the app.** `make_prod_env.py --domain <domain>` sets
      `ROOT_DOMAIN`, the email sender and the storefront links from it (§11).
      Add any other hostname you plan to use to `RESERVED_SLUGS` in
      `app/core/tenant.py` first, so no restaurant can take it.

### 4.1 Server and services

- [x] **[BLOCKER] Domain and DNS shape** is described in 4.0: the root domain,
      `admin.<domain>`, and a wildcard `*.<domain>`.
- [x] **[BLOCKER] Reserve Clerk's subdomains.** *(code)* *Fixed:* `clerk`,
      `accounts` and `clkmail` added to `RESERVED_SLUGS` in
      `app/core/tenant.py`, so no restaurant can take the DNS names Clerk
      production needs. Add any other hostname you plan to use there too.
- [ ] **[BLOCKER] TLS: a Cloudflare Origin Certificate.** Cloudflare →
      SSL/TLS → Origin Server → **Create Certificate**. Choose RSA, hostnames
      `<domain>` and `*.<domain>`, validity 15 years. Save the certificate as
      `infra/certs/fullchain.pem` and the private key as
      `infra/certs/privkey.pem` on the server (git-ignored; `chmod 600` the
      key). It is only trusted by Cloudflare, which is exactly the point:
      every visitor arrives through Cloudflare's own public certificate, and
      nothing ever needs renewing on the server. Add a calendar reminder at
      14 years. A lapsed certificate takes down every storefront at once.
      (Let's Encrypt with DNS-01 also works, if you would rather not depend on
      Cloudflare for it.)
- [x] **[LAUNCH] Narrow proxy trust.** *(code)* `docker-compose.prod.yml` gives
      nginx a fixed address and sets `FORWARDED_ALLOW_IPS` and
      `TRUSTED_PROXY_CIDRS` to it alone (§2.7). Change `EDGE_SUBNET` and
      `EDGE_IP` together only if 172.31.250.0/24 is taken on the server.
- [x] **[LAUNCH] Cloudflare ranges.** Re-checked 2026-09-25 against
      https://www.cloudflare.com/ips-v4 and ips-v6: identical, 22 ranges.
      Check again if launch is months away.
- [ ] **[BLOCKER] Origin firewall**: allow inbound 80/443 **only from
      Cloudflare's ranges**, and SSH only from your own IP. Do this in the
      cloud provider's firewall (security group), **not** with `ufw`: Docker
      writes its own iptables rules for published ports and bypasses `ufw`
      entirely. The rate limiter and the forwarded headers are only
      trustworthy if the origin cannot be reached directly.
- [ ] **[BLOCKER] PostgreSQL**:
  - [x] *(code)* `zenoeats_migrate`, `zenoeats_app` and `zenoeats_system` get
        their passwords from `ZENOEATS_*_PASSWORD`. `infra/postgres/01-roles.sql`
        reads them when the volume is first created, and the production
        compose file refuses to start without them. `make_prod_env.py`
        generates all three (§11).
  - [x] All three roles `NOBYPASSRLS`; `zenoeats_app` owns no tables. CI's
        privilege gates prove it on every build.
        **On your side:** also run `tests/test_rls_isolation.py` against
        staging.
  - [x] *(code)* Not reachable from outside: no host port is published in
        production. On another host, use TLS for the connection.
  - [ ] On a managed database (RDS, Cloud SQL…), the init script will not run;
        create the roles and grants by hand before the first migration.
- [x] **[BLOCKER] Redis ×2.** *(code)* `redis-broker` runs with AOF and
      `noeviction` (a lost task is a lost payment webhook), `redis-runtime`
      with eviction. Both have passwords (`REDIS_*_PASSWORD`) and no published
      port. The rate limiter **fails open** without `redis-runtime`, so
      `/health/operations` reports `redis_runtime` (§6).
- [x] **[BLOCKER] Celery.** *(code)* The compose file runs one `worker` and
      **exactly one** `beat`; never `--scale` beat. Beat and the worker
      together are watched through the heartbeat in `/health/operations`
      (§6).
- [ ] **[BLOCKER] Secrets**: `make_prod_env.py` writes `.env` readable by its
      owner only, outside images and outside git. **On your side:** keep a
      copy in a password manager (§5), and give nobody else a login on the
      server.
### Menu images

**Decided: launch on the API host's disk.** Not because object storage is
wrong, but because moving is cheap *later* and expensive to justify now.
Rows store keys and never URLs, `IMAGES_PUBLIC_BASE` already accepts an
absolute URL, and the whole of storage is one class with five methods — so
the move is "copy the directory into a bucket, change one setting", with no
database change and no cutover logic. Adding a provider, credentials and a
new failure mode before the first real customer buys nothing.

What that decision costs, and therefore what is not optional:

- [x] **[BLOCKER] `IMAGES_DIR` on persistent storage that is backed up.**
      Losing the disk loses every menu photograph irrecoverably, and the rows
      keep their keys — so every storefront renders broken images rather than
      degrading. A container filesystem is not persistent storage.
      *(code)* The api writes to `./images` in the repository on the server's
      disk, a bind mount, never the container's filesystem. `backup.sh` takes
      it with every database dump (§5). If the VM gives you a separate data
      disk, clone the repository onto that disk.
- [x] **[BLOCKER] The restore drill (§5) covers the images directory too,**
      restored to the same point in time as the database. *(code)*
      `backup.sh` copies the folder before and after the dump, so the archive
      holds every file the dump refers to. `restore_drill.sh` fails if any
      referenced image is missing.
- [ ] **[LAUNCH] Accept one API machine.** `API_WORKERS` still uses every
      core on that host, so this is a limit on machines, not on processes.
      Images written on one host are invisible to another.

**Move to object storage when either happens** — whichever comes first, and
the second is the easier one to ignore:

1. You need a second API host.
2. The backup and restore-consistency discipline above starts slipping.

Cloudflare R2 is worth preferring over S3 here: image serving is egress, and
R2 does not charge for it. Whatever the provider, disable bucket listing —
keys are unguessable, which is the whole of the protection — and check
`delete_restaurant` still purges properly, since it becomes a list-and-delete
rather than a directory removal.

- [x] **[LAUNCH] Images are cacheable.** *(code)* They are served with
      `Cache-Control: public, max-age=31536000, immutable`, which they had
      none of. An ETag alone is the weakest useful caching: the browser still
      asked about every photograph on every view and was told 304, so a menu
      with thirty pictures cost thirty round trips from a phone on restaurant
      wifi, and a CDN in front had no instruction to answer them instead. The
      files are immutable by construction — a random key per upload, written
      once, released only when no row refers to it. `app/main.py`,
      `tests/test_images.py`.

---

## 5. Backups and recovery

*(code, done)* `scripts/backup.sh` runs nightly from
`infra/systemd/zenoeats-backup.timer`. It takes a `pg_dump` and the images
folder together, encrypts both to an **age** public key, copies them off the
server with **rclone**, and pings **healthchecks.io** whether it succeeds or
fails. `scripts/restore_drill.sh` restores one into a scratch PostgreSQL and
fails unless the checksums, `pg_restore`, row-level security and every
referenced image all check out. Both were run end to end on 2026-09-25: a
25-second backup, a passing drill, and a corrupted archive caught on its
checksum.

The encryption key pair was generated on 2026-09-25. The **public** key,
which goes on the server, is
`age1fayakxatfwar93dxcrl5qw09xfje6hpete73lnmrqsa8q9ss03dsmfccq9`. The
**private** key is in `%USERPROFILE%\zenoeats-secrets\backup-age-identity.txt`
on the development laptop, and nowhere else yet.

- [ ] **[BLOCKER] Keep the private key safe.** Copy that file into a password
      manager and onto an offline copy (USB). It never goes on the server or
      into git. Without it no backup can be read, ever.
- [ ] **[BLOCKER] Off-site storage: Cloudflare R2** (free tier 10 GB; needs a card).
  1. Cloudflare → **R2 Object Storage** → enable → **Create bucket**
     `zenoeats-backups`.
  2. Bucket → Settings → **Object lifecycle rules**: delete objects after
     **90 days**.
  3. Bucket → Settings → **Bucket lock rules**: retain all objects **30 days**,
     so nobody, including someone holding the server's key, can delete recent
     backups.
  4. R2 → **Manage API tokens → Create API token**: *Object Read & Write*,
     scoped to `zenoeats-backups` only. Note the Access Key ID, the Secret
     Access Key and the endpoint `https://<account-id>.r2.cloudflarestorage.com`.
- [ ] **[BLOCKER] Missed-backup alert: healthchecks.io** (free). Add a check
      named `zenoeats-backup`, with period 1 day and grace 2 hours, and note
      its ping URL.
- [ ] **[BLOCKER] Install on the server** (after §11). Write
      `/etc/zenoeats/backup.env` (`sudo chmod 600`):

      ```
      BACKUP_AGE_RECIPIENT=age1fayakxatfwar93dxcrl5qw09xfje6hpete73lnmrqsa8q9ss03dsmfccq9
      BACKUP_RCLONE_REMOTE=r2:zenoeats-backups/nightly
      BACKUP_HEALTHCHECK_URL=https://hc-ping.com/<uuid>
      RCLONE_CONFIG_R2_TYPE=s3
      RCLONE_CONFIG_R2_PROVIDER=Cloudflare
      RCLONE_CONFIG_R2_ACCESS_KEY_ID=<access key id>
      RCLONE_CONFIG_R2_SECRET_ACCESS_KEY=<secret access key>
      RCLONE_CONFIG_R2_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
      RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true
      ```

      Then:

      ```bash
      sudo apt install -y age rclone
      sudo cp infra/systemd/zenoeats-backup.* /etc/systemd/system/
      sudo systemctl daemon-reload
      sudo systemctl enable --now zenoeats-backup.timer
      sudo systemctl start zenoeats-backup.service   # one run now
      journalctl -u zenoeats-backup.service          # it should end "done:"
      ```

      The unit files assume the repository is at `/opt/zenoeats`. Edit both
      paths if it is not.
- [ ] **[BLOCKER] Escrow `.env`**, which holds `FIELD_ENCRYPTION_KEY`, separately
      from the backups: a password manager entry. Pickup PINs are encrypted
      with it. Losing it makes them unreadable, and a backup stored together
      with the key protects nothing, which is why `backup.sh` leaves `.env`
      out.
- [ ] **[BLOCKER] One timed restore drill before launch**, then monthly, on
      the laptop (never the server, which has no private key). It needs
      Docker, bash, `age` and `rclone`. On Windows the simplest is WSL Ubuntu
      with `sudo apt install age rclone`. Export the same `RCLONE_CONFIG_R2_*`
      variables as the server; a second, *Object Read* only token is enough
      here.

      ```bash
      BACKUP_AGE_IDENTITY=~/zenoeats-secrets/backup-age-identity.txt \
      BACKUP_RCLONE_REMOTE=r2:zenoeats-backups/nightly \
        scripts/restore_drill.sh
      ```

      Write down the time it prints. That is how long a real restore takes.
- [x] **[LAUNCH]** Back up the menu images directory. *(code)* It's in every
      backup (above).
- [ ] **[LAUNCH]** Write a short rollback plan: previous image tags, and
      whether the latest migration's downgrade is safe to run.

---

## 6. Monitoring and alerting

*(code, done)* `/health/operations` answers 503, naming what is failing, when
any of these is wrong, and 200 `{"status":"ok"}` otherwise:

| Name | Means |
|---|---|
| `worker_heartbeat` | beat or the worker has stopped (no heartbeat task for 5 minutes) |
| `webhooks_stuck` | a Stripe or Clerk delivery unprocessed for 10 minutes |
| `webhooks_failed` | a delivery failed in the last 24 hours |
| `stale_checkouts` | unpaid orders 15 minutes past their expiry |
| `redis_runtime` / `redis_broker` | a Redis unreachable (the rate limiter fails open) |
| `database` | the system role cannot reach Postgres |
| `disk` | under a tenth, or under 2 GB, free where images and the database live |

It gives names only, never counts, because it is public. It answers on the
root domain and every subdomain.

- [ ] **[BLOCKER]** Uptime checks: with **UptimeRobot** (free, 5-minute
      interval) or Better Stack, add two HTTP monitors that alert on anything
      but 200:
      `https://<domain>/health/ready` (the database, as the app role) and
      `https://<domain>/health/operations`. Alert by email and phone push.
- [x] **[LAUNCH]** Alert on **stale `PENDING_PAYMENT` orders**. *(code)*
      `stale_checkouts`, above.
- [x] **[LAUNCH]** Alert on `stripe_events` and `clerk_events` rows with status
      `FAILED`. *(code)* `webhooks_failed`, and `webhooks_stuck` for ones never
      processed.
- [x] **[LAUNCH]** Alert on Redis being unreachable and on disk space. *(code)*
      `redis_*` and `disk`, above. Certificate expiry: the Cloudflare origin
      certificate lasts 15 years (§4); Cloudflare renews its public one
      itself.
- [ ] **[LAUNCH]** A missed nightly backup alerts through healthchecks.io (§5).
- [ ] **[LAUNCH]** Centralised logs with a retention period.
- [ ] **[LAUNCH]** Create a Sentry project (free Developer plan, platform
      *Python / FastAPI*) and set `SENTRY_DSN`. `make_prod_env.py` already
      sets `SENTRY_ENVIRONMENT` and `RELEASE`.

---

## 7. Production configuration

Every setting the backend reads, with what it must be in production.
**`python3 scripts/make_prod_env.py --domain <domain> --release v1.2.0`
writes the whole file** (§11) from
[`.env.production.example`](../../.env.production.example), which explains
each setting: it generates every secret, sets every value below that does not
come from an account, and leaves those that do empty. `--check .env` lists
what is still missing.

| Variable | Production value | Notes |
|---|---|---|
| `ENV` | `production` | **[BLOCKER]** Secure cookies, no `/docs`, no error details, and the startup safety checks (§2.1). |
| `ROOT_DOMAIN` | e.g. `zenoeats.com` | Tenant resolution, CORS, the Clerk token origin check, the nginx template. |
| `ALLOW_TEST_KEYS` | `false` | `true` on staging only. Production refuses test keys without it (§2.7). |
| `API_IMAGE`, `WEB_IMAGE` | `ghcr.io/haswanth13901/zenoeats-mvp/{api,web}:v1.2.0` | The release to run. Never built on the server. |
| `POSTGRES_PASSWORD` | generated | The Postgres superuser. |
| `ZENOEATS_MIGRATE_PASSWORD`, `_APP_`, `_SYSTEM_` | generated, hex | The three roles. Applied when the volume is first created; later changes are an `ALTER ROLE`. |
| `REDIS_BROKER_PASSWORD`, `REDIS_RUNTIME_PASSWORD` | generated, hex | |
| `DATABASE_URL_APP` / `_SYSTEM` / `_MIGRATE`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `REDIS_RUNTIME_URL` | *not set* | Built by `docker-compose.prod.yml` from the passwords above. Only the migrate job gets the owner's URL. A production API refuses to start with it. |
| `TRUSTED_PROXY_CIDRS`, `FORWARDED_ALLOW_IPS` | *not set* | Set by `docker-compose.prod.yml` to nginx's fixed address (`EDGE_IP`). |
| `EDGE_SUBNET`, `EDGE_IP` | defaults `172.31.250.0/24`, `172.31.250.10` | Change together, only on a clash. |
| `API_WORKERS` / `WEB_CONCURRENCY` | 2+ | Size with database pools. |
| `CLERK_JWKS_URL`, `CLERK_ISSUER`, `CLERK_SECRET_KEY`, `CLERK_WEBHOOK_SECRET` | production Clerk instance | The first three are required at startup. |
| `CLERK_PUBLISHABLE_KEY` (web container) | `pk_live_…` | Read at container start. Compose maps it from `VITE_CLERK_PUBLISHABLE_KEY`. |
| `CSP_EXTRA_IMG_SRC`, `CSP_REPORT_ONLY` | bucket origin; `false` | Web container. `true` only for a first staging run. |
| `AUTH_DEV_BYPASS` | `false` | Startup refuses `true`. |
| `ADMIN_USERS` | `docker run --rm -it <API_IMAGE> python scripts/hash_password.py you@example.com` | Required at startup. One named entry per operator, `;`-separated. |
| `SESSION_SECRET` | generated | Required at startup, 32+ characters. |
| `ADMIN_SESSION_TTL_MINUTES`, `STAFF_SESSION_TTL_MINUTES` | 480 / 720, or your policy | |
| `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_CONNECT_WEBHOOK_SECRET` | live values | Secret and webhook secret required at startup. |
| `PLATFORM_FEE_BPS`, `PLATFORM_FEE_FIXED_MINOR` | your fee | 0 = no fee. |
| `SENDGRID_API_KEY`, `EMAIL_FROM`, `EMAIL_REPLY_TO` | SendGrid key (Mail Send); sender on an authenticated domain | Empty key = no emails. |
| `STOREFRONT_URL_TEMPLATE` | `https://{slug}.{root_domain}` | Links in emails. |
| `GOOGLE_MAPS_API_KEY`, `GOOGLE_MAPS_BROWSER_KEY` | two separate keys | Server key restricted to the server's IP; browser key to `https://*.<domain>/*`. Empty = no delivery (server key) or no map and suggestions (browser key). |
| `GEOCODING_PROVIDER`, `GEOCODE_CACHE_TTL_SECONDS`, `GEOCODE_TIMEOUT_SECONDS` | `google`, 30 days, 4 | |
| `DRIVER_LOCATION_STALE_SECONDS`, `DELIVERY_ETA_REFRESH_SECONDS`, `ROUTES_TIMEOUT_SECONDS` | defaults | Live tracking. Each arrival-time refresh is billed. |
| `GUEST_SESSION_TTL_MINUTES`, `GUEST_RETENTION_DAYS` | 30 days, 45 | |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | 10, 10 | Per API process; size with `API_WORKERS` under `max_connections`. |
| `API_MEM_LIMIT`, `API_CPUS`, `WORKER_MEM_LIMIT`, `WORKER_CPUS`, `BEAT_MEM_LIMIT`, `WEB_MEM_LIMIT`, `NGINX_MEM_LIMIT`, `REDIS_RUNTIME_MEM_LIMIT`, `MIGRATE_MEM_LIMIT` | defaults for 2 vCPU / 4 GB | `docker-compose.prod.yml`. Raise on a bigger VM. |
| `LOG_LEVEL` | `INFO` | |
| `FIELD_ENCRYPTION_KEY` | generated | Required at startup. Never reuse the dev key, never regenerate a live one; escrow it (§5). |
| `IMAGES_DIR`, `IMAGES_PUBLIC_BASE` | persistent path or bucket URL | |
| `PENDING_PAYMENT_TTL_MINUTES`, `IDEMPOTENCY_TTL_HOURS`, `MAX_ITEMS_PER_ORDER` | defaults are reasonable | |
| `WEBHOOK_EVENT_RETENTION_DAYS` | 365, or your policy | 0 = keep forever. |
| `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `SENTRY_TRACES_SAMPLE_RATE`, `RELEASE` | your Sentry project | Empty DSN = off. |
| `TLS_CERT_DIR` | certificate directory | `docker-compose.prod.yml`. |

---

## 8. Data

- [ ] **[BLOCKER]** Start production from an **empty database** plus migrations.
      Never copy the development database: it holds test users, test
      restaurants and rows created by the test suite.
- [ ] **[BLOCKER]** Do **not** run `scripts/seed.py` in production. It prints a
      temporary owner password and creates a placeholder Stripe account.
- [ ] **[LAUNCH]** Create restaurants, owners and Stripe onboarding through
      the super admin portal.

---

## 9. Legal and business

The pages now exist and are wired up *(code)*: `/legal/privacy`,
`/legal/terms`, `/legal/refunds` and `/legal/data-deletion`, served as files
by nginx on every host including the root domain, with no JavaScript needed —
which is what an OAuth reviewer and a crawler both fetch. They are linked from
the sign-up consent checkbox, from above the checkout button, and from a
footer on every customer page. **Every one of them carries a visible “draft,
not reviewed” banner and a list of what a lawyer must confirm, and those stay
until the review below happens.**

- [ ] **[BLOCKER]** **Your details for the pages** (skipped on 2026-09-25,
      to be supplied): the legal entity name (company, or your own name as a
      sole proprietor); the trading or registered address; a contact email
      somebody reads, for privacy and deletion requests; where you operate
      (country, and state in the US); and the deletion response windows
      (suggested: confirm within **7 days**, complete within **30**). Every
      `PLACEHOLDER` in `web/legal/*.html` is one of these.
- [ ] **[BLOCKER]** **Legal review** of all four pages. **Send the pack in
      [`docs/legal/`](../legal/)**: the brief (how the service works, the data
      it holds, the gaps found and 21 questions) and the four pages as PDFs.
      Each page also ends with the
      specific questions for its own text; the common ones are the legal
      entity name, trading address and contact address (`PLACEHOLDER` in the
      files today), the governing law, and which privacy regime applies —
      no GDPR lawful-basis section and no CCPA notice are present.
      Then delete the banner and the “Before this page goes live” paragraph
      from each page.
- [ ] **[BLOCKER]** **Publish them on the root domain** and give Google,
      Facebook and Apple the `https://<root domain>/legal/...` URLs, not a
      restaurant subdomain. §3.2 needs these before any provider is approved.
- [ ] **[BLOCKER]** **A process behind data deletion.** Customers close their
      own account from Manage profile → Personal details, which removes the
      Clerk sign-in, their details and favourites and keeps paid orders (PR
      #19). The page describes that first (§2.7). The email route, for
      anyone who cannot sign in, is manual: it needs the monitored mailbox and
      the response windows the page commits to. Handle it by deleting the
      user in the Clerk dashboard once the request is confirmed; the
      `user.deleted` webhook runs the same removal.
- [ ] **[LAUNCH]** **Restaurant agreement**: fees, merchant-of-record duties,
      disputes, tax responsibility, data processing. It must agree with
      `/legal/refunds` about who bears a refund and a chargeback, and must
      tell restaurants how refunds work: a full refund with **Cancel and
      refund** on the kitchen board, anything else from their own Stripe
      Dashboard (§2.3).
- [ ] **[LAUNCH]** **Cookie notice** where required. The privacy policy lists
      the cookies and says all are necessary; whether a consent banner is
      needed where you operate is part of the review above.
- [x] **[LAUNCH]** **Allergen / food disclaimer.** *(code)* In the footer of
      every customer page, and in the terms: kitchens handle allergens,
      cross-contact cannot be ruled out, contact the restaurant.
- [x] **[SOON]** **Proof of agreement.** *(code)* The checkbox blocks sign-up,
      and migration 0033 records `terms_accepted_at` and `terms_version` on
      the customer when an order is placed — the moment the checkout page says
      continuing means agreeing. Guests included, since a guest never signs
      up. Bump `CURRENT_VERSION` in `app/services/terms.py` when the wording
      changes materially; everyone re-agrees on their next order.
      `tests/test_terms_acceptance.py`.
- [ ] **[LAUNCH]** **PCI**: Stripe Elements keeps card data off your servers
      (SAQ A), but the annual self-assessment still has to be completed in
      Stripe.

---

## 10. Go-live rehearsal on staging

Run the whole flow on a staging environment that uses the **production shape**
(same nginx, TLS, containers and migration process) with test-mode Stripe and
a separate Clerk instance. Set it up exactly as §11, but generate the file
with `make_prod_env.py --staging --domain <staging domain>`, which allows
test keys and nothing else.

Already proven on 2026-09-25, on a production-shaped stack on the laptop
(fresh volume, HTTPS edge, real passwords):
- migrations ran as the owner role, and a dev password was refused
- Redis refused connections without its password
- no internal port was exposed
- HTTP redirected to HTTPS, with HSTS
- both health endpoints returned the API's JSON through the edge
- forged client IPs were rate-limited
- a guest card payment went through to a completed PIN handover and a
  cancel-and-refund, against Stripe's test API
- backup and restore drill passed

Staging repeats all of this where the laptop cannot: the real domain,
Cloudflare, Clerk and webhooks from Stripe.

- [ ] Customer **sign-up** with email code, **sign-in**, **forgot password**.
- [ ] **Google**, **Apple** and **Facebook** sign-in, including a first-time
      account, and a Facebook account without an email (the "continue" step).
- [ ] Two-step verification: authenticator app, then a backup code.
- [ ] With `CSP_REPORT_ONLY=true`, repeat sign-in, a card payment with
      3-D Secure, and Apple/Google Pay, and confirm the browser console shows
      no CSP violations. Then set it back to `false`.
- [ ] Sign in on one restaurant subdomain, open another: still signed in.
- [ ] Order with a card → webhook → order appears on the **kitchen board** →
      ready → **PIN** completes it.
- [ ] **Apple Pay** on a real iPhone (Safari) and **Google Pay** on Android.
- [ ] Abandon a checkout → it **expires** after the TTL with nothing charged.
- [ ] **Cancel and refund** on the kitchen board, and a **partial refund** from
      the restaurant's Stripe Dashboard: payment status updates in the
      portal, and for a Stripe Tax restaurant a reversal appears under Tax →
      Transactions.
- [ ] The order becomes PAID **through the webhook**, within seconds. If it
      takes about 10 seconds, the fallback settled it, and the webhook
      endpoint or its secret is wrong.
- [ ] `https://<domain>/health/operations` answers 200. Stop the worker
      (`docker compose … stop worker`) and within 5 minutes it answers 503
      `worker_heartbeat`, and the uptime monitor alerts. Start it again.
- [ ] "Refresh Stripe" on the test restaurant shows Apple Pay and Google Pay
      active.
- [ ] A **Stripe Tax** restaurant: the quote shows local tax for the pickup
      address, the paid order appears under the restaurant's Tax →
      Transactions, and the amounts match.
- [ ] Order **confirmation email** arrives once (without the PIN), then
      "ready to collect" (pick-up) or "on its way" and "delivered" (delivery),
      and a cancellation email with the refund amount. A **staff invitation
      email** arrives with the sign-in link, and a password reset from the
      portal is emailed. Links in every email open the right storefront.
- [ ] **Staff invite** → temporary password → change password → accept →
      board access by role.
- [ ] **Super admin**: create restaurant, create owner, Stripe onboarding,
      activate, suspend (storefront disappears within a few seconds).
- [ ] Try a forged `X-Forwarded-For` against `/restaurant/login` through the
      real edge and confirm the rate limit still applies.
- [ ] Menu photos load on a storefront (served by the API through the edge).
- [ ] Last night's backup exists in R2, healthchecks.io received its ping,
      and `restore_drill.sh` passes on it (§5).
- [ ] Light load test on menu, quote and order creation.

When every **[BLOCKER]** and **[LAUNCH]** box is ticked, switch Stripe and
Clerk to live keys, deploy the tagged release, and place one real low-value
order end to end before announcing.

---

## 11. First deploy, step by step

The server: an Ubuntu 24.04 VM with at least **2 vCPU and 4 GB RAM**, a
public IPv4 address, and its disk backed up by the provider if it offers
that. Everything below runs on it, as a user with `sudo`.

1. **Firewall first** (§4.1): in the provider's firewall, allow 22 from your
   IP only, and 80/443 from Cloudflare's ranges only.
2. **Docker**, the native engine, not Docker Desktop:

   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker $USER   # then log out and back in
   ```

3. **The repository**, at the path the backup timer expects:

   ```bash
   sudo git clone https://github.com/haswanth13901/zenoeats-mvp.git /opt/zenoeats
   sudo chown -R $USER: /opt/zenoeats
   cd /opt/zenoeats && git checkout v1.2.0
   ```

4. **The production `.env`** (§7):

   ```bash
   python3 scripts/make_prod_env.py --domain <domain> --release v1.2.0
   ```

   Fill in each value marked `# FILL IN:` from §3 (Clerk, Stripe, SendGrid,
   Sentry). For `ADMIN_USERS`:

   ```bash
   docker run --rm -it ghcr.io/haswanth13901/zenoeats-mvp/api:v1.2.0 \
     python scripts/hash_password.py you@example.com
   ```

   Then `python3 scripts/make_prod_env.py --check .env` until it lists
   nothing under "Before this can run". **Put a copy of `.env` in your password
   manager now** (§5).
5. **The certificate** from §4.0 into `infra/certs/fullchain.pem` and
   `infra/certs/privkey.pem` (`chmod 600` the key).
6. **Start it.** Pull, migrate, then everything else:

   ```bash
   export COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml
   docker compose --profile app pull
   docker compose --profile app up -d
   docker compose ps          # all healthy; migrate "Exited (0)"
   ```

   Put the `export` line in `~/.bashrc` so every later `docker compose` on
   this server uses the production file.
7. **Check it**: `https://<domain>/health/ready` and `/health/operations`
   answer 200, the latter within a minute or two of starting.
   `https://admin.<domain>` shows the platform sign-in.
8. **Backups** (§5): write `/etc/zenoeats/backup.env`, install the timer, run
   it once and see it land in R2.
9. **Monitors** (§6): the two uptime checks.
10. **First restaurant** (§8): create it in the admin portal with its phone
    number for customers (activation refuses one without), create its owner,
    run Stripe onboarding, activate, and press "Refresh Stripe" to see the
    wallets active.

**A later release** is the same image swap: `git fetch --tags && git
checkout vX.Y.Z`, change `RELEASE`, `API_IMAGE` and `WEB_IMAGE` in `.env`,
then `docker compose --profile app pull && docker compose --profile app up -d`.
The `migrate` job runs first on every `up`, and the api waits for it.
