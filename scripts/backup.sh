#!/usr/bin/env bash
# Full backup of a Lecta installation: Postgres dump + app-data + secrets volumes.
#   scripts/backup.sh [output-dir]      (default: ./backups)
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT=${PROJECT:-lecta}
OUT=${1:-backups}
STAMP=$(date +%Y%m%d-%H%M)
mkdir -p "$OUT"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
echo "dumping database…"
docker compose -p "$PROJECT" exec -T db pg_dump -U lecta -d lecta -Fc > "$TMP/db.dump"
for vol in app-data secrets; do
  echo "archiving volume $vol…"
  docker run --rm -v "${PROJECT}_${vol}:/v:ro" -v "$TMP:/out" --network none alpine:3.22 \
    tar -C /v -czf "/out/${vol}.tar.gz" .
done
tar -C "$TMP" -czf "$OUT/lecta-$STAMP.tar.gz" db.dump app-data.tar.gz secrets.tar.gz
echo "backup written to $OUT/lecta-$STAMP.tar.gz"
