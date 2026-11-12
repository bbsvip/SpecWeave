#!/bin/sh
# Destructive restore for BUNDLED PostgreSQL Compose, from matching offline backup.
# Requires explicit acknowledgement to protect live instances.
set -eu
umask 077
if [ "${SPECWEAVE_RESTORE_CONFIRM:-}" != 'RESTORE_SPECWEAVE' ]; then
  echo 'ABORTED: set SPECWEAVE_RESTORE_CONFIRM=RESTORE_SPECWEAVE after verifying the backup.' >&2
  exit 2
fi
if [ "$#" -ne 1 ]; then
  echo 'Usage: SPECWEAVE_RESTORE_CONFIRM=RESTORE_SPECWEAVE sh scripts/restore-compose.sh /path/to/specweave-TIMESTAMP.sha256' >&2
  exit 2
fi
HASHFILE=$1
[ -f "$HASHFILE" ] || { echo 'Manifest not found' >&2; exit 1; }
DIR=$(cd "$(dirname "$HASHFILE")" && pwd)
MANIFEST=$(basename "$HASHFILE")
STEM=${MANIFEST%.sha256}
DBFILE="$DIR/$STEM.postgres.sql"
FILESFILE="$DIR/$STEM.projects.tar.gz"
[ -f "$DBFILE" ] && [ -f "$FILESFILE" ] || { echo 'Missing database or volume backup' >&2; exit 1; }
(cd "$DIR" && sha256sum -c "$MANIFEST")
# Reject archives with absolute or dot-dot paths, even if an operator substituted a file.
tar -tzf "$FILESFILE" | awk '
  /^\// {bad=1} /^\.\.\// {bad=1} /\/\.\.\// {bad=1}
  $0 !~ /^projects(\/|$)/ {bad=1}
  END { if (bad) exit 1 }
' || { echo 'Unsafe archive entries' >&2; exit 1; }
# Project volumes must contain normal files/directories only. Reject symlinks and hardlinks.
tar -tvzf "$FILESFILE" | awk 'substr($0,1,1)=="l" || substr($0,1,1)=="h" {bad=1} END {if (bad) exit 1}' || { echo 'Archive contains unexpected links' >&2; exit 1; }
command -v docker >/dev/null || { echo 'Docker required' >&2; exit 1; }
echo 'WARNING: replacing the entire project volume and specweave database.'
echo 'Stopping all SpecWeave writers...'
docker compose stop web review-worker
# DATABASE_RESET is intentionally a single explicit action; no new backup is made here.
echo 'Recreating PostgreSQL database...'
docker compose exec -T db sh -c 'dropdb --if-exists -U specweave specweave && createdb -U specweave specweave'
docker compose exec -T db psql -X -v ON_ERROR_STOP=1 -U specweave -d specweave < "$DBFILE"
echo 'Replacing project files...'
docker compose run --rm --no-deps --entrypoint sh web -c 'rm -rf /data/projects && mkdir -p /data/projects'
docker compose run --rm --no-deps --entrypoint tar web -C /data -xzf - < "$FILESFILE"
echo 'Starting web and review worker (Alembic upgrades any older snapshot)...'
docker compose up -d web review-worker
echo 'Restore complete. Verify health, login and export.'
