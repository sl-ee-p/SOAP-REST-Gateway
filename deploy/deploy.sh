#!/usr/bin/env bash
# Deploy one image tag with docker compose; roll back to the previous tag if it never gets healthy.
# Usage: IMAGE=ghcr.io/owner/repo:<sha> deploy/deploy.sh
# Runs on the self-hosted runner. The app dir holds the real .env and the deployed-tag record.
set -euo pipefail

: "${IMAGE:?IMAGE must be set, e.g. ghcr.io/owner/repo:<sha>}"
APP_DIR="${APP_DIR:-/srv/apps/soap-rest-gateway}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8083/health}"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

cd "$APP_DIR"
[ -f .env ] || { echo "Missing $APP_DIR/.env (create it from .env.example first)" >&2; exit 1; }
cp "$SRC_DIR/compose.prod.yml" compose.prod.yml

compose() { docker compose -f compose.prod.yml "$@"; }

healthy() {
  for _ in $(seq 1 30); do
    curl -fsS "$HEALTH_URL" >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}

PREVIOUS="$(cat .deployed-image 2>/dev/null || true)"

echo "Deploying $IMAGE (previous: ${PREVIOUS:-none})"
IMAGE="$IMAGE" compose pull
IMAGE="$IMAGE" compose up -d --remove-orphans

if healthy; then
  echo "$IMAGE" > .deployed-image
  docker image prune -f >/dev/null
  echo "Deployed $IMAGE"
  exit 0
fi

echo "Health check failed for $IMAGE" >&2
IMAGE="$IMAGE" compose logs --tail=50 >&2 || true
if [ -n "$PREVIOUS" ]; then
  echo "Rolling back to $PREVIOUS" >&2
  IMAGE="$PREVIOUS" compose up -d --remove-orphans
  healthy && echo "Rollback is healthy" >&2 || echo "Rollback is NOT healthy either" >&2
fi
exit 1
