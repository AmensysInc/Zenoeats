# Changelog

Notable changes to Zenoeats, newest first. Each release is a git tag, and CI
publishes `ghcr.io/haswanth13901/zenoeats-mvp/{api,web}:<tag>` from it. The
format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
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
- Documentation moved under `docs/`: `operations/`, `security/`, `design/`,
  `development/`. There is an index at `docs/README.md`.

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

[Unreleased]: https://github.com/haswanth13901/zenoeats-mvp/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/haswanth13901/zenoeats-mvp/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/haswanth13901/zenoeats-mvp/releases/tag/v1.0.0
