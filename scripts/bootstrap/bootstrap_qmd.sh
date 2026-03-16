#!/usr/bin/env bash
set -euo pipefail

command -v qmd >/dev/null 2>&1 || {
  echo "qmd not found. Install with: brew install bun sqlite && bun install -g https://github.com/tobi/qmd"
  exit 1
}

echo "QMD detected at: $(command -v qmd)"
