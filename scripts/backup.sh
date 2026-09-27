#!/usr/bin/env bash
# Nightly backup of a production Zenoeats host: the database and the menu
# images, taken together, encrypted, and copied off the machine.
#
#   scripts/backup.sh            run from anywhere; reads the settings below
#
# Run it from a systemd timer or cron on the VM (see docs/operations/STEPS_BEFORE_PRODUCTION.md,
# "Backups and recovery"). It exits non-zero on any failure, and reports both
# outcomes to BACKUP_HEALTHCHECK_URL, so a backup that silently stops running
# is noticed by the absence of a ping rather than by the day it is needed.
#
# Settings, from the environment or from BACKUP_ENV_FILE
# (default /etc/zenoeats/backup.env, readable by root only):
#
#   BACKUP_AGE_RECIPIENT     required. The age public key (age1...) backups
#                            are encrypted to. The matching private key does
#                            NOT live on this server: a stolen server then
#                            cannot read its own backups. Keep it with the
#                            FIELD_ENCRYPTION_KEY escrow, but not in the same
#                            place as the backups themselves.
#   BACKUP_RCLONE_REMOTE     required. Where copies go, as an rclone target,
#                            e.g. r2:zenoeats-backups/nightly. Configure the
#                            remote with `rclone config` first. Give this
#                            server's credentials write access only, and let
#                            the bucket's lifecycle rules expire old copies:
#                            this script never deletes anything remote, so a
#                            compromised server cannot erase the history.
#   BACKUP_HEALTHCHECK_URL   optional. Pinged on success; "<url>/fail" on
#                            failure (healthchecks.io convention).
#   BACKUP_DIR               local staging (default /var/backups/zenoeats).
#   BACKUP_KEEP_LOCAL_DAYS   local copies kept (default 3).
#   IMAGES_HOST_DIR          the images folder on the host (default
#                            <repo>/images, the api container's bind mount).
#   COMPOSE_FILE             default docker-compose.yml:docker-compose.prod.yml
#
# What is deliberately NOT in a backup: the .env file. It holds
# FIELD_ENCRYPTION_KEY, and a backup stored with the key that unlocks its
# pickup PINs protects nothing. Escrow .env separately.
#
# Needs: docker (with compose), age, rclone, GNU coreutils, tar, curl.

set -Eeuo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${BACKUP_ENV_FILE:-/etc/zenoeats/backup.env}"
if [[ -r "$ENV_FILE" ]]; then
  set -a; source "$ENV_FILE"; set +a
fi

: "${BACKUP_AGE_RECIPIENT:?set BACKUP_AGE_RECIPIENT to the age public key}"
: "${BACKUP_RCLONE_REMOTE:?set BACKUP_RCLONE_REMOTE, e.g. r2:zenoeats-backups/nightly}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/zenoeats}"
BACKUP_KEEP_LOCAL_DAYS="${BACKUP_KEEP_LOCAL_DAYS:-3}"
IMAGES_HOST_DIR="${IMAGES_HOST_DIR:-$REPO/images}"
export COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml:docker-compose.prod.yml}"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
work="$BACKUP_DIR/.work-$stamp"
out="$BACKUP_DIR/$stamp"

ping_health() {
  [[ -n "${BACKUP_HEALTHCHECK_URL:-}" ]] || return 0
  curl -fsS -m 10 --retry 3 -o /dev/null "$BACKUP_HEALTHCHECK_URL$1" || true
}

on_error() {
  echo "backup FAILED at line $1" >&2
  rm -rf "$work"
  ping_health /fail
}
trap 'on_error $LINENO' ERR

log() { echo "$(date -u +%H:%M:%S) $*"; }

mkdir -p "$BACKUP_DIR" "$work"
chmod 700 "$BACKUP_DIR"
cd "$REPO"

# Images before and after the dump. A photo is only deleted once no row
# refers to it, and keys are never reused, so the union of the folder as it
# stood just before the dump and just after it holds every file a row in the
# dump refers to. Hard links where the images and BACKUP_DIR share a disk (no
# second copy of the bytes, and instant); a plain copy where they do not.
log "images: first pass"
cp -al "$IMAGES_HOST_DIR" "$work/images" 2> /dev/null || {
  rm -rf "$work/images"
  cp -a "$IMAGES_HOST_DIR" "$work/images"
}

log "database: pg_dump"
# Custom format: compressed, and pg_restore can restore it selectively.
docker compose exec -T postgres \
  pg_dump -U postgres -d zenoeats --format=custom --compress=6 \
  > "$work/zenoeats.dump"
# An empty or truncated dump exits 0 surprisingly often through a pipe.
# pg_restore --list reads the whole table of contents or fails.
docker compose exec -T postgres pg_restore --list < "$work/zenoeats.dump" > /dev/null

log "images: second pass"
cp -aln "$IMAGES_HOST_DIR/." "$work/images/" 2> /dev/null \
  || cp -an "$IMAGES_HOST_DIR/." "$work/images/"

log "encrypting"
mkdir -p "$out"
age -r "$BACKUP_AGE_RECIPIENT" -o "$out/zenoeats-$stamp.dump.age" "$work/zenoeats.dump"
tar -C "$work" -cf - images | age -r "$BACKUP_AGE_RECIPIENT" -o "$out/images-$stamp.tar.age"
(cd "$out" && sha256sum ./*.age > "SHA256SUMS-$stamp")
rm -rf "$work"

log "copying off the server to $BACKUP_RCLONE_REMOTE/$stamp"
rclone copy "$out" "$BACKUP_RCLONE_REMOTE/$stamp" --immutable
# Read back what landed rather than trusting the upload's exit code.
rclone check "$out" "$BACKUP_RCLONE_REMOTE/$stamp" --one-way

log "pruning local copies older than $BACKUP_KEEP_LOCAL_DAYS days"
find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name '20*' \
  -mtime +"$BACKUP_KEEP_LOCAL_DAYS" -exec rm -rf {} +

size="$(du -sh "$out" | cut -f1)"
log "done: $stamp ($size)"
ping_health ""
