#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STATE_DIR="${STATE_DIR:-$HOME/.openclaw}"
LAUNCHD_DIR="${LAUNCHD_DIR:-$HOME/Library/LaunchAgents}"
SYSTEMD_DIR="${SYSTEMD_DIR:-$HOME/.config/systemd/user}"
ACTIVATE_SERVICES=0
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/docker_runtime.sh"

usage() {
  cat <<'EOF'
Usage: install_host_services.sh [--activate]

Render host service definitions for the current platform.

Options:
  --activate  Activate the rendered gateway and sidecar services immediately.
  -h, --help  Show this help text.
EOF
}

require_command() {
  local command_name="$1"
  local message="$2"
  if command -v "$command_name" >/dev/null 2>&1; then
    return 0
  fi
  echo "ERROR: $message" >&2
  exit 1
}

activate_launchd_service() {
  local label="$1"
  local plist_path="$2"
  local domain
  domain="gui/$(id -u)"

  if launchctl print "${domain}/${label}" >/dev/null 2>&1; then
    launchctl bootout "$domain" "$plist_path"
  fi
  launchctl bootstrap "$domain" "$plist_path"
}

activate_launchd_services() {
  require_docker_backend_ready
  require_command launchctl "launchctl is required to activate launchd services."
  activate_launchd_service "ai.openclaw.gateway" "$LAUNCHD_DIR/ai.openclaw.gateway.plist"
  activate_launchd_service "ai.openclaw.sidecars" "$LAUNCHD_DIR/ai.openclaw.sidecars.plist"
  echo "Activated launchd services for gui/$(id -u)"
}

activate_systemd_services() {
  require_docker_backend_ready
  require_command systemctl "systemctl is required to activate user-level systemd services."
  systemctl --user daemon-reload
  systemctl --user enable --now openclaw-sidecars.service
  systemctl --user enable --now openclaw-gateway.service
  echo "Activated user systemd services from $SYSTEMD_DIR"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --activate)
      ACTIVATE_SERVICES=1
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

case "$(uname -s)" in
  Darwin)
    mkdir -p "$LAUNCHD_DIR" "$STATE_DIR/logs"
    for template in "$ROOT"/platform/launchd/*.template; do
      name="$(basename "$template" .template)"
      out="$LAUNCHD_DIR/$name"
      sed \
        -e "s|__REPO_ROOT__|$ROOT|g" \
        -e "s|__STATE_DIR__|$STATE_DIR|g" \
        "$template" > "$out"
    done
    echo "Rendered launchd plists into $LAUNCHD_DIR"
    if [[ "$ACTIVATE_SERVICES" -eq 1 ]]; then
      activate_launchd_services
    else
      echo "Run: launchctl bootstrap gui/$(id -u) $LAUNCHD_DIR/ai.openclaw.gateway.plist"
      echo "Run: launchctl bootstrap gui/$(id -u) $LAUNCHD_DIR/ai.openclaw.sidecars.plist"
      echo "Run with --activate to bootstrap them automatically."
    fi
    ;;
  Linux)
    mkdir -p "$SYSTEMD_DIR" "$STATE_DIR/logs"
    for template in "$ROOT"/platform/systemd/*.service; do
      name="$(basename "$template")"
      out="$SYSTEMD_DIR/$name"
      sed \
        -e "s|__REPO_ROOT__|$ROOT|g" \
        -e "s|__STATE_DIR__|$STATE_DIR|g" \
        "$template" > "$out"
    done
    echo "Rendered systemd user units into $SYSTEMD_DIR"
    if [[ "$ACTIVATE_SERVICES" -eq 1 ]]; then
      activate_systemd_services
    else
      echo "Run: systemctl --user daemon-reload"
      echo "Run: systemctl --user enable --now openclaw-sidecars.service"
      echo "Run: systemctl --user enable --now openclaw-gateway.service"
      echo "Run with --activate to enable and start them automatically."
    fi
    ;;
  *)
    echo "unsupported host OS for service installation: $(uname -s)" >&2
    exit 1
    ;;
esac
