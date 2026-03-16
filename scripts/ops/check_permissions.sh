#!/usr/bin/env bash
set -euo pipefail

chmod 700 "$HOME/.openclaw"
find "$HOME/.openclaw" -type d -exec chmod 700 {} +
find "$HOME/.openclaw" -type f -name '*.json' -exec chmod 600 {} +
echo "Permissions normalized under $HOME/.openclaw"
