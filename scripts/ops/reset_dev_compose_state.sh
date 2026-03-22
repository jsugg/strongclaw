#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/app_paths.sh"

usage() {
  cat <<'EOF'
Usage: reset_dev_compose_state.sh --component COMPONENT [--state-dir PATH] [--force-stop]

Reset one repo-local compose-state component without wiping the rest of the
development stack.

Components:
  postgres
  qdrant
  litellm
  otel
  browser-lab
EOF
}

COMPONENT=""
STATE_DIR=""
FORCE_STOP=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --component)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --component requires a value." >&2
        exit 1
      fi
      COMPONENT="$2"
      shift 2
      ;;
    --state-dir)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --state-dir requires a value." >&2
        exit 1
      fi
      STATE_DIR="$2"
      shift 2
      ;;
    --force-stop)
      FORCE_STOP=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$COMPONENT" ]]; then
  echo "ERROR: --component is required." >&2
  usage >&2
  exit 1
fi

case "$COMPONENT" in
  postgres)
    compose_file="docker-compose.aux-stack.yaml"
    service_name="postgres"
    component_dir="postgres"
    ;;
  qdrant)
    compose_file="docker-compose.aux-stack.yaml"
    service_name="qdrant"
    component_dir="qdrant"
    ;;
  litellm)
    compose_file="docker-compose.aux-stack.yaml"
    service_name="litellm"
    component_dir="litellm"
    ;;
  otel)
    compose_file="docker-compose.aux-stack.yaml"
    service_name="otel-collector"
    component_dir="otel"
    ;;
  browser-lab)
    compose_file="docker-compose.browser-lab.yaml"
    service_name="browserlab-playwright"
    component_dir="browser-lab"
    ;;
  *)
    echo "ERROR: unsupported component: $COMPONENT." >&2
    usage >&2
    exit 1
    ;;
esac

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is required to confirm the target component is stopped." >&2
  exit 1
fi

if [[ -z "$STATE_DIR" ]]; then
  STATE_DIR="$(strongclaw_repo_local_compose_state_dir "$ROOT")"
fi
STATE_DIR="$(_strongclaw_expand_user_path "$STATE_DIR")"
TARGET_DIR="$STATE_DIR/$component_dir"
mkdir -p "$TARGET_DIR"

cd "$ROOT/platform/compose"
container_id="$(docker compose -f "$compose_file" ps -q "$service_name" 2>/dev/null || true)"
if [[ -n "$container_id" ]]; then
  if [[ "$FORCE_STOP" -eq 0 ]]; then
    echo "ERROR: $COMPONENT is still running. Stop it first or rerun with --force-stop." >&2
    exit 1
  fi
  docker compose -f "$compose_file" stop "$service_name" >/dev/null
fi

find "$TARGET_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
printf 'Reset repo-local %s state at %s\n' "$COMPONENT" "$TARGET_DIR"
