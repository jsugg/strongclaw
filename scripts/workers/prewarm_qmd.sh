#!/usr/bin/env bash
set -euo pipefail

QMD_BIN="${QMD_BIN:-$HOME/.bun/bin/qmd}"
if [[ ! -x "$QMD_BIN" ]]; then
  QMD_BIN="$(command -v qmd)"
fi

"$QMD_BIN" --help >/dev/null
echo "QMD is installed and reachable at $QMD_BIN."
