#!/usr/bin/env bash
# Serve CDP on 9222 for OpenClaw's `browserlab` profile from the Playwright image's Chromium.
# The container is the isolation boundary: its port is published on host loopback only, and
# all browser egress goes through the allowlisting browserlab-proxy.
set -euo pipefail
shopt -s nullglob

candidates=(
  /ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell
  /ms-playwright/chromium-*/chrome-linux*/chrome
)
if ((${#candidates[@]} == 0)); then
  echo "browser-lab: no Chromium build found under /ms-playwright" >&2
  exit 1
fi

profile_dir="/workspace/state/chromium-profile"
mkdir -p "${profile_dir}"

exec "${candidates[0]}" \
  --headless \
  --no-sandbox \
  --disable-gpu \
  --disable-dev-shm-usage \
  --no-first-run \
  --remote-debugging-address=0.0.0.0 \
  --remote-debugging-port=9222 \
  --proxy-server="${HTTP_PROXY:-http://browserlab-proxy:3128}" \
  --user-data-dir="${profile_dir}" \
  about:blank
