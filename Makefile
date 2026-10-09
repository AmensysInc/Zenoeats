# The project virtualenv. Windows puts binaries in Scripts/, POSIX in bin/,
# so pick whichever is actually there rather than hard-coding one and failing
# on the other machine with "no such file or directory".
PY := $(firstword $(wildcard .venv/Scripts/python.exe .venv/bin/python) .venv/bin/python)

.PHONY: help setup certs infra api web worker up-all down logs migrate seed test rls fresh key

help:
	@echo "Development (app runs natively, infrastructure in Docker):"
	@echo "  make setup    copy .env.example to .env and generate an encryption key"
	@echo "  make certs    check the HTTPS certificate is there (bash scripts/local_https_cert.sh makes it)"
	@echo "  make infra    start postgres, redis x2 and nginx -- builds nothing"
	@echo "  make api      run the API natively (own terminal)"
	@echo "  make web      run the frontend natively (own terminal)"
	@echo "  make worker   run Celery natively (only needed for payments)"
	@echo "  make migrate  apply database migrations"
	@echo "  make seed     create the demo restaurant and menu"
	@echo "  make test     run the test suite"
	@echo "  make rls      run the tenant-isolation gates only"
	@echo "  make down     stop the containers"
	@echo ""
	@echo "Production rehearsal (builds images -- slow, not for daily work):"
	@echo "  make up-all   build and start every service in Docker"

key:
	@python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

setup:
	@test -f .env || cp .env.example .env
	@echo "Created .env. Fill in what it marks REQUIRED:"
	@echo "  FIELD_ENCRYPTION_KEY  from 'make key'"
	@echo "  SESSION_SECRET        from 'openssl rand -base64 32'"
	@echo "Then add your Clerk and Stripe test keys, and copy web/.env.example to web/.env."

# Infrastructure only. The app services sit behind the "app" profile, so this
# starts postgres, redis and nginx and builds nothing. Image builds are what
# repeatedly wedged the Docker engine; nothing here needs one to run the app.
infra: certs
	docker compose up -d
	@echo "postgres 127.0.0.1:5433  redis 6379/6380  nginx https :8443"
	@echo "Now run 'make api' and 'make web' in their own terminals."

# Each of these runs in the foreground in its own terminal.
# --timeout-keep-alive 30: uvicorn idles a kept-alive connection out after 5s
# by default, which is close enough to nginx's own idle timeout that the two
# race and a request lands in a connection the other side just closed. The
# edge is set to 3s, well under this.
#
# The API and worker are runtime processes and must not hold the schema
# owner's credentials. .env carries DATABASE_URL_MIGRATE for `make migrate`;
# exporting it empty here overrides the file, as docker-compose.yml does for
# the containers.
api worker: export DATABASE_URL_MIGRATE :=

api:
	$(PY) scripts/dev_preflight.py native
	cd backend && ../$(PY) -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --timeout-keep-alive 30

web:
	$(PY) scripts/dev_preflight.py native
	cd web && npm run dev

# Only needed to test payments: an order cannot leave PENDING_PAYMENT without
# a worker to process the Stripe webhook. --pool=solo because Celery's default
# prefork pool silently hangs on Windows.
worker:
	$(PY) scripts/dev_preflight.py native
	cd backend && ../$(PY) -m celery -A app.workers.celery_app.celery_app worker --loglevel=info --pool=solo

# Everything in Docker, images included. Slow. Use to rehearse production,
# not for day-to-day work.
up-all: certs
	$(PY) scripts/dev_preflight.py docker
	docker compose --profile app up -d --build
	@echo "Portal:  https://spicehouse.zenoeats.local:8443"

# nginx serves HTTPS only and will not start without its certificate.
certs:
	@test -f infra/certs-local/fullchain.pem || { \
		echo "No certificate in infra/certs-local/. Run once: bash scripts/local_https_cert.sh"; \
		exit 1; }

down:
	docker compose down

logs:
	docker compose logs -f

migrate:
	cd backend && ../$(PY) -m alembic upgrade head

seed:
	cd backend && ../$(PY) scripts/seed.py

.PHONY: import-menu
import-menu: ## Load a menu onto an existing restaurant:  make import-menu slug=jrs-corner file=menus/jrs-corner.json
	@test -n "$(slug)" || (echo 'Usage: make import-menu slug=<restaurant-slug> file=<menu.json>' && exit 1)
	@test -n "$(file)" || (echo 'Usage: make import-menu slug=<restaurant-slug> file=<menu.json>' && exit 1)
	cd $(BACKEND) && ../$(PY) scripts/import_menu.py $(slug) $(file)

.PHONY: export-menu
export-menu: ## Write a restaurant's menu to stdout as JSON:  make export-menu slug=jrs-corner
	@test -n "$(slug)" || (echo 'Usage: make export-menu slug=<restaurant-slug>' && exit 1)
	cd $(BACKEND) && ../$(PY) scripts/export_menu.py $(slug)

test:
	cd backend && ../$(PY) -m pytest tests/ -q

rls:
	cd backend && ../$(PY) -m pytest tests/test_rls_isolation.py -v

fresh:
	docker compose --profile app down -v
	$(MAKE) infra
	sleep 12
	$(MAKE) migrate
	$(MAKE) seed
