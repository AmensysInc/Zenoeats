# Security policy

Zenoeats handles restaurants' menus and orders, customers' contact details,
and payments through Stripe. Reports of security problems are welcome and
taken seriously.

## Reporting a vulnerability

**Please do not open a public issue.** Report privately through GitHub:
this repository → **Security** tab → **Report a vulnerability**. Only the
maintainers can see the report.

Include what you found, where (URL, endpoint or file), how to reproduce it,
and what an attacker could do with it. We aim to acknowledge a report within
three business days, and to agree a timeline for a fix and any disclosure
with you.

## Scope and ground rules

In scope: this repository's code, configuration and deployment files, and
the running application.

Please test only against your own local copy (see the README). Do not:
- access, change or delete data that is not yours
- degrade the service for others
- use social engineering
- test against restaurants' or customers' accounts
- disclose an issue publicly before a fix is available

Payment card data is handled entirely by Stripe and never reaches these
servers. Report issues in Stripe, Clerk or other providers to those
providers.

## Supported versions

Security fixes are made on `main` and shipped in the next release. Only the
latest release is supported; see `CHANGELOG.md`.

## How the project handles security

The design, the most recent audit and the checks that run on every change
are documented in `docs/security/`:
- `SECURITY_AUDIT_REPORT.md`: findings, evidence and status
- `SECURITY_TEST_MATRIX.md`: which tests cover which risk
- `SECURITY_DEPLOYMENT_CHECKLIST.md`: the gates before and after each
  deployment
