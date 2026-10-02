#!/usr/bin/env bash
# Smoke test for the Lecta deployment.
#
#   scripts/smoke.sh          checks the running `lecta` stack:
#                             - every service healthy
#                             - no service publishes a port
#                             - only lecta-frontend is attached to webnet
#                             - the compile container has no network
#                             - anonymous HTTP through lecta-frontend:4321 on webnet
#   scripts/smoke.sh --full   additionally starts an isolated copy of the stack
#                             (project lecta-smoke, own volumes, own test network),
#                             runs the setup wizard + login and checks the /api proxy
#                             end to end: SSE, WebSocket, a 512 MB streaming upload,
#                             admin pages; then tears it down.
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT=${PROJECT:-lecta}
CURL_IMAGE=curlimages/curl:8.11.1
fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "ok   $*"; }

docker network inspect webnet >/dev/null 2>&1 || { docker network create webnet >/dev/null; echo "created network webnet"; }

echo "== deployment invariants ($PROJECT)"
ids=$(docker compose -p "$PROJECT" ps -q)
[[ -n "$ids" ]] || fail "stack $PROJECT is not running (docker compose up -d)"
names=()
frontend_name=
for id in $ids; do
  name=$(docker inspect -f '{{.Name}}' "$id" | sed 's#^/##')
  service=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$id")
  health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$id")
  [[ "$health" == "healthy" ]] || fail "$service is $health"
  bindings=$(docker inspect -f '{{json .HostConfig.PortBindings}}' "$id")
  published=$(docker inspect -f '{{range $p, $conf := .NetworkSettings.Ports}}{{if $conf}}{{$p}} {{end}}{{end}}' "$id")
  [[ "$bindings" == "{}" || "$bindings" == "null" ]] || fail "$service has port bindings: $bindings"
  [[ -z "$published" ]] || fail "$service publishes ports: $published"
  nets=$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$id")
  mode=$(docker inspect -f '{{.HostConfig.NetworkMode}}' "$id")
  names+=("$name")
  if [[ "$service" == "lecta-frontend" ]]; then
    frontend_name=$name
    [[ " $nets " == *" webnet "* ]] || fail "lecta-frontend is not on webnet ($nets)"
  else
    [[ " $nets " != *" webnet "* ]] || fail "$service must not be on webnet ($nets)"
  fi
  if [[ "$service" == "latex" ]]; then
    [[ "$mode" == "none" ]] || fail "latex must have network_mode none (got $mode)"
  fi
  ok "$service ($name): healthy, no published ports, networks: ${nets:-$mode}"
done
# Nothing else from this project on webnet.
on_webnet=$(docker network inspect webnet -f '{{range .Containers}}{{.Name}} {{end}}')
for c in $on_webnet; do
  if [[ "$c" != "$frontend_name" && " ${names[*]} " == *" $c "* ]]; then fail "$c is on webnet"; fi
done
ok "only lecta-frontend of $PROJECT is on webnet"

echo "== anonymous HTTP via webnet (lecta-frontend:4321)"
c() { docker run --rm --network webnet "$CURL_IMAGE" -s -o /dev/null -w '%{http_code}' "$@"; }
[[ $(c http://lecta-frontend:4321/healthz) == 200 ]] || fail "/healthz"
[[ $(c http://lecta-frontend:4321/api/health) == 200 ]] || fail "/api/health via proxy"
[[ $(c http://lecta-frontend:4321/api/public/courses) == 200 ]] || fail "/api/public/courses"
code=$(c http://lecta-frontend:4321/)
[[ "$code" == 200 || "$code" == 302 ]] || fail "/ -> $code"
for p in /api/courses /api/jobs /api/settings /api/dashboard /admin /admin/courses /api/diag/sse; do
  [[ $(c "http://lecta-frontend:4321$p") == 404 ]] || fail "anonymous $p is not 404"
done
[[ $(c -X POST -H 'content-type: application/json' -d '{}' http://lecta-frontend:4321/api/courses) == 404 ]] || fail "anonymous POST"
ok "public pages up, private routes 404 for anonymous visitors"

if [[ "${1:-}" != "--full" ]]; then
  echo "smoke OK"
  exit 0
fi

echo "== isolated full proxy test (project lecta-smoke)"
docker network inspect lecta-smoke-web >/dev/null 2>&1 || docker network create lecta-smoke-web >/dev/null
S=(docker compose -p lecta-smoke -f docker-compose.yml -f scripts/smoke.override.yml)
cleanup() { "${S[@]}" down -v --remove-orphans >/dev/null 2>&1 || true; docker network rm lecta-smoke-web >/dev/null 2>&1 || true; }
trap cleanup EXIT
"${S[@]}" up -d --wait --wait-timeout 300 >/dev/null 2>&1
code=$("${S[@]}" exec -T backend cat /data/secrets/setup-code)
docker run --rm --network lecta-smoke-web -e SETUP_CODE="$code" -e BASE=http://lecta-frontend:4321 \
  -v "$PWD/scripts/proxy_check.py:/check.py:ro" --entrypoint python lecta-app:local /check.py
mem=$(docker stats --no-stream --format '{{.MemUsage}}' "$("${S[@]}" ps -q lecta-frontend)")
ok "frontend memory after upload: $mem"
echo "smoke --full OK"
