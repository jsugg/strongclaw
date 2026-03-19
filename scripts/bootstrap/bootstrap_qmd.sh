#!/usr/bin/env bash
set -euo pipefail

EXPECTED_QMD_BIN="$HOME/.bun/bin/qmd"
QMD_PACKAGE="${QMD_PACKAGE:-@tobilu/qmd}"
QMD_INSTALL_PREFIX="${QMD_INSTALL_PREFIX:-$HOME/.openclaw/vendor/qmd}"
QMD_DIST_ENTRY="$QMD_INSTALL_PREFIX/node_modules/@tobilu/qmd/dist/cli/qmd.js"

write_qmd_wrapper() {
  mkdir -p "$(dirname "$EXPECTED_QMD_BIN")"
  cat > "$EXPECTED_QMD_BIN" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec node "$QMD_DIST_ENTRY" "\$@"
EOF
  chmod 755 "$EXPECTED_QMD_BIN"
}

qmd_is_healthy() {
  [[ -x "$EXPECTED_QMD_BIN" ]] || return 1
  "$EXPECTED_QMD_BIN" status >/dev/null 2>&1
}

if qmd_is_healthy; then
  echo "QMD detected at: $EXPECTED_QMD_BIN"
  exit 0
fi

if [[ -x "$EXPECTED_QMD_BIN" ]]; then
  echo "QMD detected at $EXPECTED_QMD_BIN but the launcher is unhealthy; reinstalling."
  rm -f "$EXPECTED_QMD_BIN"
  rm -rf "$QMD_INSTALL_PREFIX"
fi

command -v npm >/dev/null 2>&1 || {
  echo "npm not found. Install npm first, then rerun this script."
  exit 1
}
command -v node >/dev/null 2>&1 || {
  echo "node not found. Install node first, then rerun this script."
  exit 1
}

npm install --prefix "$QMD_INSTALL_PREFIX" "$QMD_PACKAGE"

if [[ ! -f "$QMD_DIST_ENTRY" ]]; then
  echo "qmd install finished but $QMD_DIST_ENTRY is missing."
  exit 1
fi

rm -f "$EXPECTED_QMD_BIN"
write_qmd_wrapper

if ! qmd_is_healthy; then
  echo "qmd install finished but $EXPECTED_QMD_BIN did not pass the health check."
  exit 1
fi

echo "QMD installed at: $EXPECTED_QMD_BIN"
