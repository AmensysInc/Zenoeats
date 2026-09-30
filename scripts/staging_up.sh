#!/bin/bash
# Build and start the staging stack. Run on the VPS, from anywhere.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "No .env. From this directory:"
  echo "  python3 scripts/make_prod_env.py --staging --domain stg9.zenoeats.com --release local"
  echo "Then fill the FILL IN values and run this again."
  exit 1
fi

export COMPOSE_PROJECT_NAME=zenoeats-stg9
export COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml:docker-compose.staging.yml

docker compose --profile app up -d --build "$@"
docker compose ps
