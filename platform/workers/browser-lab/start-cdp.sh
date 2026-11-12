#!/usr/bin/env bash
# Serve CDP on 9222 for OpenClaw's `browserlab` profile from the Playwright image's Chromium.
# The container is the isolation boundary: its port is published on host loopback only, and
# all browser egress goes through the allowlisting browserlab-proxy.
set -euo pipefail
shopt -s nullglob

readonly cdp_port=9222
# Chromium only reliably serves DevTools on its own loopback, so a TCP relay exposes it on the
# container interface that Docker publishes.
readonly chromium_port=9223

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

"${candidates[0]}" \
  --headless \
  --no-sandbox \
  --disable-gpu \
  --disable-dev-shm-usage \
  --no-first-run \
  --remote-debugging-port="${chromium_port}" \
  --proxy-server="${HTTP_PROXY:-http://browserlab-proxy:3128}" \
  --user-data-dir="${profile_dir}" \
  about:blank &
chromium_pid=$!

node -e '
const net = require("net");
const [listenPort, targetPort] = process.argv.slice(1).map(Number);
net.createServer((client) => {
  const upstream = net.connect(targetPort, "127.0.0.1");
  client.pipe(upstream).pipe(client);
  const close = () => { client.destroy(); upstream.destroy(); };
  client.on("error", close);
  upstream.on("error", close);
}).listen(listenPort, "0.0.0.0");
' "${cdp_port}" "${chromium_port}" &
relay_pid=$!
trap 'kill "${chromium_pid}" "${relay_pid}" 2>/dev/null || true' EXIT TERM INT

# Exit when either process dies so the container restart policy brings both back.
wait -n "${chromium_pid}" "${relay_pid}"
