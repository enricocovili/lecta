#!/usr/bin/env bash
# Run the backend test-suite inside the app image, against the running stack's
# Postgres (throw-away database) and the real compile service.
#   scripts/test.sh               # all tests
#   scripts/test.sh -k boundary   # pytest args
#   DEV=1 scripts/test.sh         # mount ./backend instead of using the built image
set -euo pipefail
cd "$(dirname "$0")/.."
docker network inspect webnet >/dev/null 2>&1 || docker network create webnet >/dev/null
docker compose up -d db latex >/dev/null
extra=()
if [[ "${DEV:-}" == "1" ]]; then extra=(-v "$PWD/backend:/app"); fi
exec docker compose run --rm --no-deps "${extra[@]}" -e PYTHONDONTWRITEBYTECODE=1 backend \
  python -m pytest -p no:cacheprovider "$@"
