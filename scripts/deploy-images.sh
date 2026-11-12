#!/usr/bin/env bash
# Deploy the already-published GHCR images without building on the host.
set -euo pipefail
cd "$(dirname "$0")/.."

case "${1:-external}" in
  external) compose_file=compose.external-db.images.yaml ;;
  internal) compose_file=compose.images.yaml ;;
  *) echo 'Usage: scripts/deploy-images.sh [external|internal]' >&2; exit 2 ;;
esac

args=(-f "$compose_file")
docker compose "${args[@]}" pull
docker compose "${args[@]}" up -d --no-build --remove-orphans
