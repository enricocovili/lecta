#!/usr/bin/env bash
# Restore a backup made by scripts/backup.sh (replaces the current data!).
#   scripts/restore.sh backups/lecta-YYYYmmdd-HHMM.tar.gz
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT=${PROJECT:-lecta}
ARCHIVE=${1:?usage: scripts/restore.sh <archive.tar.gz>}
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
tar -C "$TMP" -xzf "$ARCHIVE"
echo "stopping the stack…"
docker compose -p "$PROJECT" stop lecta-frontend backend worker latex
for vol in app-data secrets; do
  echo "restoring volume $vol…"
  docker volume create "${PROJECT}_${vol}" >/dev/null
  docker run --rm -v "${PROJECT}_${vol}:/v" -v "$TMP:/in:ro" --network none alpine:3.22 \
    sh -c "find /v -mindepth 1 -delete && tar -C /v -xzf /in/${vol}.tar.gz"
done
echo "restoring the database…"
docker compose -p "$PROJECT" up -d db
docker compose -p "$PROJECT" exec -T db sh -c 'until pg_isready -U lecta -d lecta; do sleep 1; done'
# The restored secrets volume carries the matching DB password; reset the role to it.
PW=$(docker run --rm -v "${PROJECT}_secrets:/s:ro" alpine:3.22 cat /s/db-password)
docker compose -p "$PROJECT" exec -T db psql -U lecta -d postgres -c "ALTER USER lecta PASSWORD '$PW'" >/dev/null
docker compose -p "$PROJECT" exec -T db dropdb -U lecta --if-exists --force lecta
docker compose -p "$PROJECT" exec -T db createdb -U lecta lecta
docker compose -p "$PROJECT" exec -T db pg_restore -U lecta -d lecta --no-owner < "$TMP/db.dump"
docker compose -p "$PROJECT" up -d
echo "restore complete"
