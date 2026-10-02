#!/usr/bin/env bash
# Browser end-to-end tests (Playwright, headless Chromium in Docker) against an
# isolated copy of the stack (project lecta-e2e, own volumes and network).
set -euo pipefail
cd "$(dirname "$0")/.."
PW_IMAGE=mcr.microsoft.com/playwright:v1.63.0-noble
NET=lecta-smoke-web
docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null
S=(docker compose -p lecta-e2e -f docker-compose.yml -f scripts/smoke.override.yml)
cleanup() {
  if [[ "${KEEP:-}" != "1" ]]; then
    "${S[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT
"${S[@]}" up -d --wait --wait-timeout 300 >/dev/null 2>&1
code=$("${S[@]}" exec -T backend cat /data/secrets/setup-code)
mkdir -p e2e/.fixtures
docker run --rm -u "$(id -u):$(id -g)" -v "$PWD/e2e/.fixtures:/out" --network none --entrypoint python lecta-app:local -m tests.fixtures /out >/dev/null
docker run --rm --network "$NET" --ipc=host -u "$(id -u):$(id -g)" -e HOME=/tmp -e SETUP_CODE="$code" -e CI=1 \
  -v "$PWD/e2e:/e2e" -w /e2e -e npm_config_cache=/tmp/npm -e PW_OUTPUT=/e2e/.results "$PW_IMAGE" \
  sh -c "npm install --no-audit --no-fund --silent >/dev/null && npx playwright test $*"
