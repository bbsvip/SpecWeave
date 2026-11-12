#!/bin/sh
# Consistent offline backup for BUNDLED PostgreSQL Compose (not external DB).
# Production deployments should also monitor, encrypt and test backups regularly.
set -eu
umask 077
DEST=${1:-./backups}
mkdir -p "$DEST"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DBFILE="$DEST/specweave-$STAMP.postgres.sql"
FILESFILE="$DEST/specweave-$STAMP.projects.tar.gz"
HASHFILE="$DEST/specweave-$STAMP.sha256"
command -v docker >/dev/null || { echo 'Docker Compose is required' >&2; exit 1; }
RUNNING=$(docker compose ps --services --status running)
STOPPED=0
resume() {
  if [ "$STOPPED" -eq 1 ]; then
    for service in web review-worker; do
      if printf '%s\n' "$RUNNING" | grep -qx "$service"; then
        docker compose up -d "$service" >/dev/null 2>&1 || echo "WARNING: Could not restart $service" >&2
      fi
    done
  fi
}
trap resume EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf 'Stopping web and review worker for a consistent snapshot...\n'
docker compose stop web review-worker
STOPPED=1
printf 'Dumping PostgreSQL...\n'
docker compose exec -T db pg_dump --no-owner --no-privileges -U specweave -d specweave > "$DBFILE"
printf 'Archiving project volume...\n'
docker compose run --rm --no-deps --entrypoint tar web -C /data -czf - projects > "$FILESFILE"
# Manifest has stable basenames so verification also works after moving the folder.
(cd "$DEST" && sha256sum "$(basename "$DBFILE")" "$(basename "$FILESFILE")" > "$(basename "$HASHFILE")")
printf 'Backup complete: %s\n' "$HASHFILE"
