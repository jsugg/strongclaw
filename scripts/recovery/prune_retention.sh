#!/usr/bin/env bash
set -euo pipefail

find "$HOME/.openclaw/backups" -type f -name '*.tar.gz' -mtime +14 -delete || true
find "$HOME/.openclaw/logs" -type f -mtime +14 -delete || true
find /tmp/openclaw -type f -mtime +7 -delete || true
echo "Retention pruning complete."
