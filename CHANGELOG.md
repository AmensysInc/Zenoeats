# Changelog

Notable changes to Zenoeats, newest first. Each release is a git tag, and CI
publishes `ghcr.io/haswanth13901/zenoeats-mvp/{api,web}:<tag>` from it. The
format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- A legal review pack in `docs/legal/`: a brief for the lawyer describing how
  the service works, the personal information it holds and who receives it,
  retention as it actually is, every email, the gaps found and the questions
  to answer, with the four policy pages as PDFs (#60).

### Fixed
- The policies say what the software does: Stripe gets the customer's email
  for fraud screening and sends no receipt; SendGrid sends every kind of
  email; Google receives the visitor's IP address and typed address for the
  suggestions and map; driver positions are discarded within 10 minutes. The
  refunds page no longer promises a restaurant phone number the software
  does not hold (#60).

## [1.1.0] - 2026-09-28

Email. Everything Zenoeats sends now goes through SendGrid from templates in
the repository: the order confirmation and every step after it, the staff
and account emails, and optionally Clerk's own sign-in codes.

**Upgrading from 1.0.1.** In the server's `.env`, replace `RESEND_API_KEY`
with `SENDGRID_API_KEY` and set `EMAIL_FROM` to a sender SendGrid has
verified (see `.env.production.example`). Migration 0042 (`sent_emails`)
runs as usual before the api starts. Deploy the api and worker images
together: the new email tasks live in the worker. In each restaurant's
Stripe settings, leave "email customers" off.

### Added
- Clerk's customer emails can go through SendGrid: with "Delivered by Clerk"
  off for an email, the `email.created` webhook sends it, the verification
  and reset codes in Zenoeats' own template. The code is never stored or
  logged, and a queue that is down asks Clerk to deliver it again (#58).
- Customer account emails: a welcome from the restaurant whose storefront a
  brand-new account first opens, and a confirmation when an account is
  closed, whether from a storefront or in Clerk (#55).
- Team emails: a welcome to someone who accepts an invitation and word to
  whoever invited them, a notice when a role changes or someone is removed,
  a reset password emailed to its owner (from the portal or the super admin),
  and a "refund didn't go through" email to every admin and manager when a
  cancellation's refund is refused (#54).
- Order emails after the confirmation: ready to collect, on its way,
  delivered, cancelled (with the refund and its 10 to 14 business days when
  one was issued) and refund issued, for refunds made later from the board or
  Stripe's dashboard. Each is sent once, recorded in the new `sent_emails`
  table (migration 0042) (#53).
- Email templates: the words and layout of every email are files under
  `backend/app/templates/email/`, one folder per email, with
  `scripts/preview_emails.py` to see them without sending (#50).
- A customer whose order the restaurant cancelled and refunded is told so on
  the order page, with the amount and when to expect it (#48).
- The production setup guide in the README: Stripe live, Clerk production,
  Google Maps, Resend, Sentry, backups, monitoring and platform admins, step
  by step (#42).
- Optional HTTPS on the development laptop at
  `https://<slug>.zenoeats.local:8443`, with a name-constrained local
  certificate authority (#40).
- The go-live list and "making it a website like any other" in the steps
  before production (#39, #41).
- Repository standards: `CHANGELOG.md`, `.editorconfig`, and in `.github/`
  `CONTRIBUTING.md`, `SECURITY.md`, `CODEOWNERS` and a pull request template.

### Changed
- Stripe no longer emails its own receipts. The PaymentIntent carries no
  `receipt_email`, which in live mode sends a receipt and one per refund
  whatever the account's settings; Zenoeats' own emails cover both. The
  customer's address goes to Stripe as the payment's billing email instead,
  for fraud screening (#57).
- `.env.example` is the development template only, and now correct for a
  native run (127.0.0.1 ports) with every setting the code reads. Production
  has its own template, `.env.production.example`, which
  `scripts/make_prod_env.py` fills. The README, the production steps and the
  security checklist describe the emails and the two templates (#56).
- Email is sent through SendGrid instead of Resend. `RESEND_API_KEY` is
  replaced by `SENDGRID_API_KEY`, and `EMAIL_FROM` must be a sender SendGrid
  has verified (#52).
- The refund policy gives the same 10 to 14 business days as the order page
  (#49).
- Documentation moved under `docs/`: `operations/`, `security/`, `design/`,
  `development/`. There is an index at `docs/README.md`.

### Fixed
- The delivery tracking map no longer reloads on every five-second poll for
  restaurants with themed pins or a palette map; only the driver moves (#47).

## [1.0.1] - 2026-09-26

The security audit (#21). All twelve findings are remediated; the record is
in `docs/security/`.

### Security
- A guest's order-view token is kept out of every access log: it travels in
  the email link's fragment and an `X-Order-Token` header, and `?t=` is no
  longer accepted.
- Closed an open redirect on the customer, staff and admin sign-in pages.
- Wrong passwords are counted per account as well as per address. Password
  changes, email changes, invitations and resets are rate limited.
- Restaurant names are exported to CSV as text, never as formulas.
- The web container runs as `nginx`, not root, and CI fails if either image
  runs as root.
- Production refuses the secrets this repository publishes for CI, and
  development database passwords.
- The orders API is pinned to its own origin.
- Overlong admin input is refused with a 422 instead of a 500.
- The nginx version is no longer disclosed.

### Changed
- Memory and CPU limits for the stateless services in production.
- Every base and service image is pinned by digest.
- CI: a read-only token by default, actions pinned by commit, a secret scan,
  and Dependabot.
- `main` is protected: a pull request and green CI are required, admins
  included.

## [1.0.0] - 2026-09-25

The first release: a multi-restaurant ordering platform with three portals
(storefront, restaurant, platform admin), Stripe Connect payments, and
pickup and delivery.

### Added
- Production readiness (#20):
  - test keys refused in production
  - production passwords required
  - nightly encrypted off-site backups with a restore drill
  - `/health/operations`
  - Apple Pay and Google Pay domains registered per restaurant
  - `scripts/make_prod_env.py`
- A cart page before checkout (#18).
- Customers can close their own account (#19).
- Cancel and refund from the kitchen board (#17).
- Calories on items and sizes, summed for combos (#15).
- Delivery maps in the restaurant's own colours (#16).
- Uniform menu card sizes (#14); combo photos (#13); storefront shortcuts
  (#12); restaurant logo and name lettering (#11).
- Staff invitations:
  - the temporary password is emailed (#7)
  - "resend invite" (#9)
  - the sign-in link is on the member's row (#10)
  - the portal no longer says an invitation was emailed when it was not (#5)
- The restaurant's name on its sign-in pages (#8); photo size guidance (#4).
- The working MVP: tenant isolation by row-level security, the menu model,
  checkout, webhook-confirmed payments, the kitchen board with pickup PINs,
  staff roles, reports and the super admin portal (#1).
- MIT licence (#3).

[Unreleased]: https://github.com/haswanth13901/zenoeats-mvp/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/haswanth13901/zenoeats-mvp/compare/v1.0.1...v1.1.0
[1.0.1]: https://github.com/haswanth13901/zenoeats-mvp/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/haswanth13901/zenoeats-mvp/releases/tag/v1.0.0
