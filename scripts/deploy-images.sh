#!/usr/bin/env bash
# Deploy the already-published GHCR images without building on the host.
set -euo pipefail
cd "$(dirname "$0")/.."

case "${1:-external}" in
  external) compose_file=compose.external-db.images.yaml ;;
  internal) compose_file=compose.images.yaml ;;
  *) echo 'Usage: scripts/deploy-images.sh [external|internal] [pi]' >&2; exit 2 ;;
esac

args=(-f "$compose_file")
case "${2:-}" in
  pi) args+=(--profile pi) ;;
  '') ;;
  *) echo 'Second argument must be pi or omitted' >&2; exit 2 ;;
esac

docker compose "${args[@]}" pull
docker compose "${args[@]}" up -d --no-build
