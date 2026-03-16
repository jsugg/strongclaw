#!/usr/bin/env bash
set -euo pipefail

readonly DEFAULT_PORTS=(18789 5432 4000 4318 9464 3128 9222 3000)

is_loopback_listener() {
  case "$1" in
    127.0.0.1:*|localhost:*|\[::1\]:*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

collect_listeners_lsof() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN -F n 2>/dev/null | sed -n 's/^n//p'
}

collect_listeners_ss() {
  local port="$1"
  ss -H -ltn "( sport = :$port )" 2>/dev/null | awk '{print $4}'
}

collect_listeners_netstat() {
  local port="$1"
  netstat -an 2>/dev/null | awk -v port=".$port" '$0 ~ port && $0 ~ /LISTEN/ {print $4}'
}

collect_listeners() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    collect_listeners_lsof "$port"
    return 0
  fi
  if command -v ss >/dev/null 2>&1; then
    collect_listeners_ss "$port"
    return 0
  fi
  if command -v netstat >/dev/null 2>&1; then
    collect_listeners_netstat "$port"
    return 0
  fi
  echo "No listener inspection tool found; install one of: lsof, ss, netstat." >&2
  return 1
}

main() {
  local ports=("$@")
  local failed=0
  if [ "${#ports[@]}" -eq 0 ]; then
    ports=("${DEFAULT_PORTS[@]}")
  fi

  for port in "${ports[@]}"; do
    listeners=()
    while IFS= read -r listener; do
      if [ -n "$listener" ]; then
        listeners+=("$listener")
      fi
    done < <(collect_listeners "$port" | awk '!seen[$0]++')
    if [ "${#listeners[@]}" -eq 0 ]; then
      echo "port $port: not listening"
      continue
    fi

    local non_loopback=0
    for listener in "${listeners[@]}"; do
      if ! is_loopback_listener "$listener"; then
        non_loopback=1
      fi
    done

    if [ "$non_loopback" -eq 0 ]; then
      echo "port $port: loopback-only (${listeners[*]})"
      continue
    fi

    echo "port $port: NON-LOOPBACK (${listeners[*]})" >&2
    failed=1
  done

  return "$failed"
}

main "$@"
