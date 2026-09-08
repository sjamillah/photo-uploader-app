#!/usr/bin/env bash
# Start the built image and wait for it to serve /health. Needs no AWS and no
# database, because /health is deliberately shallow.
#
#   scripts/smoke-test.sh ci-candidate
set -euo pipefail

IMAGE="${1:?usage: smoke-test.sh <image>}"
PORT="${PORT:-8080}"
CONTAINER=ci-smoke

cleanup() { docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run -d --name "$CONTAINER" -p "$PORT:8080" \
  -e S3_BUCKET=smoke -e CLOUDFRONT_DOMAIN=smoke.invalid \
  -e DB_HOST=localhost -e DB_NAME=x -e DB_USER=x -e DB_PASSWORD=x \
  "$IMAGE" >/dev/null

if ! timeout 30 sh -c "until curl -fsS http://localhost:$PORT/health >/dev/null 2>&1; do sleep 1; done"; then
  echo "container never became healthy" >&2
  docker logs "$CONTAINER" >&2
  exit 1
fi

uid="$(docker run --rm "$IMAGE" id -u)"
[ "$uid" != "0" ] || { echo "image runs as root" >&2; exit 1; }

echo "healthy, running as uid $uid"
