#!/bin/sh
set -eu
cmd="${1:-backend}"
shift || true
case "$cmd" in
  backend)
    python -m app.migrate
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*' \
      --no-server-header --timeout-keep-alive 75 "$@"
    ;;
  worker)
    python -m app.migrate
    exec python -m app.worker.runner "$@"
    ;;
  *)
    exec "$cmd" "$@"
    ;;
esac
