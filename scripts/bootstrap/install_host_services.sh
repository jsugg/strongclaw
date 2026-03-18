#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STATE_DIR="${STATE_DIR:-$HOME/.openclaw}"
LAUNCHD_DIR="${LAUNCHD_DIR:-$HOME/Library/LaunchAgents}"
SYSTEMD_DIR="${SYSTEMD_DIR:-$HOME/.config/systemd/user}"

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
    echo "Run: launchctl bootstrap gui/$(id -u) $LAUNCHD_DIR/ai.openclaw.gateway.plist"
    echo "Run: launchctl bootstrap gui/$(id -u) $LAUNCHD_DIR/ai.openclaw.sidecars.plist"
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
    echo "Run: systemctl --user daemon-reload"
    echo "Run: systemctl --user enable --now openclaw-sidecars.service"
    echo "Run: systemctl --user enable --now openclaw-gateway.service"
    ;;
  *)
    echo "unsupported host OS for service installation: $(uname -s)" >&2
    exit 1
    ;;
esac
