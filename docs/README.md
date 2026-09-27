# Documentation

Everything written about Zenoeats beyond the code. Start with the
[project README](../README.md), which covers what the system is, how to run
it, and the production setup guide.

| Folder | What is in it | Start with |
|---|---|---|
| [`operations/`](operations/) | Getting to production and running it: the go-live list, every account and setting, the first deploy, backups, monitoring | [STEPS_BEFORE_PRODUCTION.md](operations/STEPS_BEFORE_PRODUCTION.md) |
| [`security/`](security/) | The security audit, which tests cover which risk, and the gates and sign-off before each deployment | [SECURITY_AUDIT_REPORT.md](security/SECURITY_AUDIT_REPORT.md) |
| [`development/`](development/) | Working on the app day to day: every local URL | [URLS.txt](development/URLS.txt) |
| [`design/`](design/) | The storefront redesign: the brief, the screen-to-code mapping, verification records | [ASTRA_REDESIGN_BRIEF.md](design/ASTRA_REDESIGN_BRIEF.md) |

Also:
- **How to contribute:** [`.github/CONTRIBUTING.md`](../.github/CONTRIBUTING.md)
- **Reporting a vulnerability:** [`.github/SECURITY.md`](../.github/SECURITY.md)
- **What changed in each release:** [`CHANGELOG.md`](../CHANGELOG.md)

Keep documents here current in the same pull request as the change they
describe. A stale document is worse than none, because it is believed.
