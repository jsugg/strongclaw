#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STATE_DIR="$HOME/.openclaw"

mkdir -p "$HOME/Library/LaunchAgents" "$STATE_DIR/logs"

for template in "$ROOT"/platform/launchd/*.template; do
  name="$(basename "$template" .template)"
  out="$HOME/Library/LaunchAgents/$name"
  sed \
    -e "s|__REPO_ROOT__|$ROOT|g" \
    -e "s|__STATE_DIR__|$STATE_DIR|g" \
    "$template" > "$out"
done

echo "Rendered launchd plists into $HOME/Library/LaunchAgents"
