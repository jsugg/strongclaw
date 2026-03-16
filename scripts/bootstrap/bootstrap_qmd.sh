#!/usr/bin/env bash
set -euo pipefail

EXPECTED_QMD_BIN="$HOME/.bun/bin/qmd"

if [[ -x "$EXPECTED_QMD_BIN" ]]; then
  echo "QMD detected at: $EXPECTED_QMD_BIN"
  exit 0
fi

command -v bun >/dev/null 2>&1 || {
  echo "bun not found. Install bun first, then rerun this script."
  exit 1
}

bun install -g https://github.com/tobi/qmd

if [[ ! -x "$EXPECTED_QMD_BIN" ]]; then
  echo "qmd install finished but $EXPECTED_QMD_BIN is not executable."
  exit 1
fi

echo "QMD installed at: $EXPECTED_QMD_BIN"
