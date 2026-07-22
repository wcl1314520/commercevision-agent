#!/usr/bin/env sh
set -eu

ACTION="${1:-up}"
COMPOSE_FILE="$(dirname "$0")/../infra/compose/docker-compose.yml"
VERIFY_PHASE0="$(dirname "$0")/verify_phase0.py"
VERIFY_PHASE1="$(dirname "$0")/verify_phase1.py"

case "$ACTION" in
  up)
    docker compose -f "$COMPOSE_FILE" up -d --build --wait
    uv run python "$VERIFY_PHASE0"
    uv run python "$VERIFY_PHASE1"
    ;;
  down)
    docker compose -f "$COMPOSE_FILE" down
    ;;
  status)
    docker compose -f "$COMPOSE_FILE" ps
    ;;
  logs)
    docker compose -f "$COMPOSE_FILE" logs --follow --tail 200
    ;;
  *)
    echo "Usage: $0 [up|down|status|logs]" >&2
    exit 2
    ;;
esac
