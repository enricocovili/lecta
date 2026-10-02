#!/bin/sh
set -eu
SECRET=/secrets/db-password
mkdir -p /secrets
if [ ! -s "$SECRET" ]; then
  umask 022
  head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$SECRET.tmp"
  mv "$SECRET.tmp" "$SECRET"
  echo "lecta: generated database password in $SECRET"
fi
export POSTGRES_PASSWORD_FILE="$SECRET"
exec docker-entrypoint.sh "$@"
