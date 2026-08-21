#!/bin/sh
# Mint a self-signed certificate pair for the ingress (C4), OUTSIDE the repo
# tree — the same C13 rule as .env: nothing secret lives in a OneDrive-synced
# folder. A real certificate later replaces the same two files.
#
#   ./make-self-signed.sh [server-name] [out-dir]
#
# Defaults: localhost, $LOCALAPPDATA/recon-deploy/certs (falls back to
# ~/.recon-deploy/certs off Windows).
set -eu

NAME="${1:-localhost}"
if [ -n "${LOCALAPPDATA:-}" ]; then
    DEFAULT_OUT="$LOCALAPPDATA/recon-deploy/certs"
else
    DEFAULT_OUT="$HOME/.recon-deploy/certs"
fi
OUT="${2:-$DEFAULT_OUT}"
mkdir -p "$OUT"

# MSYS_NO_PATHCONV: Git Bash on Windows otherwise rewrites "/CN=..." into a
# filesystem path (measured: it became C:/.../Git/CN=localhost). Harmless
# elsewhere — an unknown environment variable is ignored.
MSYS_NO_PATHCONV=1 openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
    -subj "/CN=$NAME" \
    -addext "subjectAltName=DNS:$NAME" \
    -keyout "$OUT/recon.key" -out "$OUT/recon.crt"

echo "wrote $OUT/recon.crt and recon.key (CN=$NAME, 365 days, self-signed)"
echo "start the ingress with RECON_INGRESS_CERTS=$OUT"
