# Security deployment checklist — Zenoeats

Security-specific gates for going live. It complements
`docs/operations/STEPS_BEFORE_PRODUCTION.md`, which has the full step-by-step deploy (§11),
and doesn't repeat it. Tick every box, and get the sign-off at the end,
before real money.

## 1. Code and release gates

- [ ] `security-audit` branch reviewed line by line by a human and merged via PR
- [x] **Branch protection on `main`** (F-07), enabled 26 September:
  - require a PR
  - require the checks `secrets`, `backend`, `frontend` and `docker`
  - no force pushes, no deletion
- [ ] CI green on the exact commit being released, including the new `secrets` job
- [ ] Release tagged (`v1.0.1` or later, containing the audit fixes); deploy
      only images CI published for that tag
- [x] Dependabot alerts and security updates, secret scanning and push
      protection: enabled 26 September

## 2. Environment configuration

- [ ] `.env` generated on the server with `python3 scripts/make_prod_env.py
      --domain <domain> --release <tag>` (from `.env.production.example`),
      never copied from development
- [ ] `python3 scripts/make_prod_env.py --check .env` lists nothing under
      "Before this can run"
- [ ] `ENV=production`, `ALLOW_TEST_KEYS=false`, live `sk_live_`/`pk_live_`
      Stripe keys and a Clerk production instance. The API refuses test keys,
      published CI secrets and `*_dev_pw` passwords at startup, so a
      successful start is evidence in itself.
- [ ] `.env` is mode 600, owned by the deploy user, and never in an image or git
- [ ] Staging has its own `.env`, database, Redis, Clerk instance and Stripe
      test keys (`--staging`), sharing nothing with production
- [ ] `CSP_REPORT_ONLY=false` after the staging CSP check (STEPS §10)

## 3. Keys, secrets, rotation

| Secret | Where | Rotation / escrow |
|---|---|---|
| `SESSION_SECRET` | `.env` | Rotating signs every staff and admin out; do it on suspected leak |
| `FIELD_ENCRYPTION_KEY` | `.env` | **Never rotate casually**: stored PINs become unreadable. Escrow in a password manager |
| Postgres role passwords | `.env` | `ALTER ROLE` then update `.env`, restart |
| Redis passwords | `.env` | Change and restart both Redis and the app together |
| Stripe live secret, webhook secret | `.env` | Roll in the Stripe dashboard; update; restart |
| Clerk secret, webhook secret | `.env` | Roll in Clerk; update; restart |
| SendGrid key | `.env` | Roll in SendGrid (Settings › API Keys) |
| Backup age private key | Password manager + offline copy | Never on the server |
| Cloudflare origin certificate key | `infra/certs/privkey.pem`, mode 600 | 15-year validity; calendar reminder |

- [ ] All of the above escrowed in a password manager, separate from the backups
- [x] Stale local `backend/.env` (test-mode Stripe and Clerk secrets, never
      committed) deleted from the development laptop. Optionally roll those
      test keys too.
- [ ] On any suspected leak: rotate at the provider first, then update `.env`.
      Removing a secret from a file does not un-leak it.

## 4. Network and host (Linux VM)

- [ ] Provider firewall (not `ufw`: Docker bypasses it): allow 80/443 from
      Cloudflare's ranges only, and 22 from your IP only
- [ ] SSH: keys only, no password login, no root login
- [ ] Unattended security updates enabled (`unattended-upgrades`)
- [ ] Only nginx publishes ports (`docker ps` shows `0.0.0.0:80`/`443` and nothing else)
- [ ] Cloudflare: SSL **Full (strict)**, Always Use HTTPS, Min TLS 1.2
- [ ] Disk encryption at rest confirmed with the VM provider
- [x] Container resource limits set (F-12), sized for 2 vCPU / 4 GB. Raise them in `.env` for a bigger VM
- [ ] `docker compose ps`: every service healthy, `migrate` exited 0

## 5. Monitoring and incident signals

- [ ] Uptime monitors on `https://<domain>/health/ready` and `/health/operations`
- [ ] Sentry DSN set; a deliberate test error appears, with no PII in it
- [ ] Log shipping chosen, with retention. Confirm access logs show paths
      without query strings (F-01).
- [ ] Alert on repeated `sign-in throttled` warnings, a sign of credential stuffing (F-04)
- [ ] healthchecks.io ping from the nightly backup

## 6. Backups and restore validation

- [ ] R2 bucket with a 90-day lifecycle rule and a **30-day bucket lock**; token scoped to the bucket
- [ ] `backup.sh` timer enabled; first run lands in R2; healthchecks.io received the ping
- [ ] `restore_drill.sh` passes against a production backup, on a machine
      other than the server; restore time recorded
- [ ] Monthly drill scheduled

## 7. Pre-launch verification (staging, production shape)

- [ ] STEPS §10 rehearsal completed, including:
  - [ ] forged `X-Forwarded-For` through the real edge is still rate-limited
  - [ ] a cross-origin request with a guest cookie is refused (F-11)
  - [ ] an email link opens its order on a second device, and the token is
        absent from the edge log (F-01). The link in the email is the
        storefront's own, not a SendGrid tracking redirect (click tracking is
        off on every message)
  - [ ] `/docs` returns 404
  - [ ] no CSP violations in the console during sign-in, 3-D Secure and wallet payments
- [ ] A human penetration test of staging (or an agreed, scoped self-test by a second person)

## Sign-off

Each item below needs an explicit, named human decision. The AI assistant
that performed this audit did not and cannot perform them.

| Decision | By | Date |
|---|---|---|
| Security-audit changes reviewed and approved for merge | | |
| Branch protection enabled on `main` (F-07) | Owner (approved in session) | 2026-09-26 |
| F-04 threshold (50 / 15 min / account) accepted | | |
| Accepted risks re-confirmed: limiter fails open without Redis; temporary staff passwords emailed, at invitation and at a reset | | |
| Container limits chosen (F-12) | Defaults for 2 vCPU / 4 GB; confirm against the chosen VM | |
| Production configuration checked (§2–§4) | | |
| Restore drill passed on production backups (§6) | | |
| Staging rehearsal and penetration test passed (§7) | | |
| **Go-live approved** | | |
