# Contributing to Zenoeats

How to set up, make a change, and get it into `main`. The README explains
what the system is; this is how to work on it.

## Setting up

Follow the README's **Running it**: hosts entries, `.env` from `.env.example`,
then either everything in Docker (`make up-all`) or the infrastructure in
Docker and the app native (`make infra`, `make api`, `make web`). Use test
keys only; production accounts are in the README's **Production setup
guide**.

## Making a change

1. **Branch from `main`** with a short descriptive name: `cart-page`,
   `order-refunds`, `security-audit`. `main` is protected, so nothing can be
   pushed to it directly.
2. **Keep a pull request to one purpose.** A feature, a fix, or a document,
   not all three.
3. **Match the code around you.** Naming, structure, and how much and how
   the comments explain *why*. Python indents 4 spaces, the web code 2;
   `.editorconfig` sets the rest.
4. **Test what you change** (below), and add a test for any behaviour you
   add or fix. A bug fix comes with the test that would have caught it.
5. **Update the documentation in the same pull request**, when the change
   affects something written down:
   - `README.md`
   - `docs/operations/STEPS_BEFORE_PRODUCTION.md`
   - `CHANGELOG.md`, under *Unreleased*

### Commit messages

The first line says what the change does, in the imperative and in plain
words ("Let a customer close their own account"). The body says **why**:
the problem, what was decided and what was ruled out, and how it was
verified. `git log` is the project's memory; write for the person who reads
it in a year.

Changes written with an AI assistant carry a `Co-Authored-By:` trailer. The
human who opens the pull request is responsible for having read every line.

## Tests

| What | Command |
|---|---|
| Backend, everything | See README, **Tests** (runs in a container against the dev Postgres and Redis) |
| Tenant isolation gates | `make rls` |
| Web | `cd web && node --test tests/*.test.mjs` |
| Web static checks | `cd web && npm run lint && npx tsc --noEmit && npm run build` |

CI runs all of it on every pull request, plus a secret scan, the dependency
audits, a reversible-migration check and a bundle-size guard. The four
checks `secrets`, `backend`, `frontend` and `docker` must pass before a pull
request can merge.

## Rules that protect the system

These are enforced by tests or CI. Know them before changing the areas they
cover.

- **Tenant isolation.** Every table with a `restaurant_id` has row-level
  security enabled and forced; `tests/test_rls_isolation.py` fails
  otherwise. Resolve the restaurant from the `Host` header, never from
  anything the client sends.
- **Every endpoint declares who may call it.** Adding an endpoint, or
  widening who may call one, means editing the map in
  `tests/test_role_coverage.py` on purpose, and the role tables in the
  README.
- **Migrations are reversible and run by the migrate job.** Name them
  `NNNN_what_it_does.py` with `revision = "NNNN"`, continuing the sequence.
  Every upgrade needs a working downgrade; CI checks it on an empty
  database. The API never runs a migration itself.
- **Money** is integer minor units (cents), and prices are always
  recalculated on the server from the database.
- **Only Stripe says an order is paid**: a verified webhook, or the payment
  read back from Stripe. Never the browser.
- **No secrets in the repository.** `.env` is ignored; `.env.example` holds
  placeholders only, and CI's secret scan fails the build otherwise. A key
  that was ever pushed must be rotated at the provider; deleting it from the
  file is not enough.
- **Test keys never reach production.** The API refuses them unless
  `ALLOW_TEST_KEYS=true`, which is for staging only.

## Security

Report vulnerabilities privately, as described in `SECURITY.md`; never in a
public issue. Changes to authentication, sessions, tenancy, payments,
webhooks or configuration get a line-by-line review. `CODEOWNERS` lists
those paths.

## Releasing

From `main` with CI green: move *Unreleased* in `CHANGELOG.md` under the new
version, merge that, then tag it (`git tag v1.0.2 && git push origin
v1.0.2`). CI publishes the images for the tag, and production deploys that
tag only (`docs/operations/STEPS_BEFORE_PRODUCTION.md`, §11).
