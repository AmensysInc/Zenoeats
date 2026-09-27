#!/usr/bin/env bash
# The certificate for HTTPS on this laptop: https://<slug>.zenoeats.local:8443
#
#   bash scripts/local_https_cert.sh
#
# Makes, once, a local certificate authority whose key stays outside the
# repository (~/zenoeats-secrets/local-ca/), and signs with it a certificate
# for zenoeats.local and *.zenoeats.local into infra/certs-local/ (git-
# ignored), where the `https` compose service reads it. Run it again to renew
# the certificate (397 days); the authority is kept, so nothing needs
# re-trusting.
#
# The authority is name-constrained: it can sign zenoeats.local names and
# nothing else, so even a leaked key could not vouch for any other site.
# Trusting it is still a decision for the person at the keyboard -- see
# README, "HTTPS on this laptop".
#
# openssl runs in a container, so nothing needs installing, and the machine's
# own openssl configuration (broken on some Windows setups) is never read.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CA_DIR="${ZENOEATS_LOCAL_CA_DIR:-$HOME/zenoeats-secrets/local-ca}"
OUT_DIR="$REPO/infra/certs-local"
IMAGE="nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10"

mkdir -p "$CA_DIR" "$OUT_DIR"
host_path() { if command -v cygpath > /dev/null; then cygpath -w "$1"; else echo "$1"; fi; }

MSYS_NO_PATHCONV=1 docker run --rm \
  -v "$(host_path "$CA_DIR"):/ca" -v "$(host_path "$OUT_DIR"):/out" "$IMAGE" sh -c '
set -e
apk add --no-cache -q openssl > /dev/null
cd /ca
if [ ! -f ca.key ]; then
  openssl req -x509 -newkey rsa:3072 -nodes -days 3650 -sha256 \
    -keyout ca.key -out ca.crt -subj "/CN=Zenoeats local development CA" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "nameConstraints=critical,permitted;DNS:zenoeats.local,permitted;DNS:.zenoeats.local" 2> /dev/null
  echo "new local authority: $(openssl x509 -in ca.crt -noout -fingerprint -sha256)"
fi
printf "%s\n" "basicConstraints=critical,CA:FALSE" \
  "keyUsage=critical,digitalSignature,keyEncipherment" \
  "extendedKeyUsage=serverAuth" \
  "subjectAltName=DNS:zenoeats.local,DNS:*.zenoeats.local" > /tmp/leaf.ext
openssl req -newkey rsa:2048 -nodes -keyout /out/privkey.pem -out /tmp/leaf.csr \
  -subj "/CN=*.zenoeats.local" 2> /dev/null
openssl x509 -req -in /tmp/leaf.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 397 \
  -sha256 -extfile /tmp/leaf.ext -out /tmp/leaf.crt 2> /dev/null
cat /tmp/leaf.crt ca.crt > /out/fullchain.pem
chmod 644 /out/fullchain.pem /out/privkey.pem
openssl verify -CAfile ca.crt /tmp/leaf.crt
echo "certificate valid until $(openssl x509 -in /tmp/leaf.crt -noout -enddate | cut -d= -f2)"
'
echo "Written to infra/certs-local/. Restart the https service to use it:"
echo "  docker compose --profile https up -d --force-recreate https"
