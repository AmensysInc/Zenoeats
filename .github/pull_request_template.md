## What and why

<!-- What this changes, and the problem it solves. One purpose per pull request. -->

## How it was tested

<!-- Commands run and what they showed; what was checked by hand, and where. -->

## Checklist

- [ ] Tests added or updated for the behaviour changed, and passing locally
- [ ] A new endpoint, or a change to who may call one, is reflected in `tests/test_role_coverage.py` and the README's role tables
- [ ] Migrations, if any, have a working downgrade
- [ ] No secrets, keys or real customer data in the diff
- [ ] Documentation updated where affected (`README.md`, `docs/`, `CHANGELOG.md` under *Unreleased*)

## Needs particular care

<!-- Tick any that apply; these get a line-by-line review. -->

- [ ] Authentication, sessions or tenancy
- [ ] Payments, refunds or webhooks
- [ ] Configuration, deployment, CI or infrastructure
- [ ] Written with an AI assistant, and every line has been read by the author
