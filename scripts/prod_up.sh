#!/bin/bash
# Build and start the production stack. Run on the VPS, from anywhere.
#
# A release:
#   cd /opt/zenoeats && git pull && bash scripts/prod_up.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "No .env. From this directory:"
  echo "  python3 scripts/make_prod_env.py --domain zenoeats.com --release local"
  echo "Then fill the FILL IN values and run this again."
  exit 1
fi

python3 scripts/make_prod_env.py --check .env

export COMPOSE_PROJECT_NAME=zenoeats-prod
export COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml:docker-compose.vps.yml

docker compose --profile app up -d --build "$@"

# Uploaded images are a bind mount of ./images, which a checkout creates as
# the deploying user. The api runs as uid 10001 (backend/Dockerfile) and
# could not write it, so every photo upload failed. Root in a throwaway
# container is the only way to hand it over without sudo.
docker run --rm -u 0 -v "$PWD/images:/i" --entrypoint chown zenoeats-prod-api:local 10001:10001 /i

docker compose ps
