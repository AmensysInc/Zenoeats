-- Three-role model. Rule 18 of the architecture baseline.
--
--   zenoeats_migrate  owns the schema. Alembic only. Never used at runtime.
--   zenoeats_app      request path + tenant-scoped workers. NOBYPASSRLS.
--   zenoeats_system   narrow cross-tenant discovery + platform inboxes.
--
-- None of these has BYPASSRLS. The CI privilege gate asserts that.
--
-- Passwords come from ZENOEATS_{MIGRATE,APP,SYSTEM}_PASSWORD in the
-- environment of whoever runs this file (the postgres container, on first
-- start of an empty volume). Unset, each falls back to its *_dev_pw, which is
-- what development and CI use. docker-compose.prod.yml refuses to start
-- without all three, so production never gets a dev password by omission.
-- This runs once per volume: changing a password later is an ALTER ROLE.

\getenv migrate_pw ZENOEATS_MIGRATE_PASSWORD
\getenv app_pw ZENOEATS_APP_PASSWORD
\getenv system_pw ZENOEATS_SYSTEM_PASSWORD
\if :{?migrate_pw} \else \set migrate_pw migrate_dev_pw \endif
\if :{?app_pw} \else \set app_pw app_dev_pw \endif
\if :{?system_pw} \else \set system_pw system_dev_pw \endif

CREATE ROLE zenoeats_migrate LOGIN PASSWORD :'migrate_pw' NOBYPASSRLS;
CREATE ROLE zenoeats_app     LOGIN PASSWORD :'app_pw'     NOBYPASSRLS;
CREATE ROLE zenoeats_system  LOGIN PASSWORD :'system_pw'  NOBYPASSRLS;

GRANT CONNECT ON DATABASE zenoeats TO zenoeats_migrate, zenoeats_app, zenoeats_system;

-- Migration 0001 creates the pgcrypto and citext extensions, and CREATE
-- EXTENSION is checked against the database rather than the schema -- so the
-- schema grant below is not enough on its own. Both are "trusted" extensions
-- in PostgreSQL 13 and later, which is what lets a non-superuser create them
-- at all; without this the first migration stops at
--
--   permission denied to create extension "pgcrypto"
--
-- On a managed database (RDS, Cloud SQL) this file does not run and the
-- provider may not allow the grant. Create the two extensions as the
-- superuser before the first migration instead; both are IF NOT EXISTS, so
-- the migration then passes over them. See docs/operations/STEPS_BEFORE_PRODUCTION.md.
GRANT CREATE ON DATABASE zenoeats TO zenoeats_migrate;

-- The migrate role owns the schema, so it needs CREATE.
GRANT CREATE, USAGE ON SCHEMA public TO zenoeats_migrate;
GRANT USAGE ON SCHEMA public TO zenoeats_app, zenoeats_system;

-- Nobody else creates objects in public.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
