#!/usr/bin/env bash
set -euo pipefail

QDRANT_URL="${HYPERMEMORY_QDRANT_URL:-http://127.0.0.1:6333}"
DRY_RUN=0
declare -a PREFIXES=("memory-v2-int-")

usage() {
  cat <<'EOF'
Usage: prune_qdrant_test_collections.sh [--qdrant-url URL] [--prefix PREFIX] [--dry-run]

Delete stale test collections from Qdrant without touching the active
hypermemory collection. Defaults to pruning the legacy memory-v2 integration
test prefix only.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --qdrant-url)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --qdrant-url requires a value." >&2
        exit 1
      fi
      QDRANT_URL="$2"
      shift 2
      ;;
    --prefix)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --prefix requires a value." >&2
        exit 1
      fi
      PREFIXES+=("$2")
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

payload="$(curl -fsS "$QDRANT_URL/collections")"
collections=()
while IFS= read -r collection; do
  if [[ -n "$collection" ]]; then
    collections+=("$collection")
  fi
done < <(
  python3 -c '
import json
import sys

prefixes = tuple(sys.argv[1:])
payload = json.load(sys.stdin)
for entry in payload.get("result", {}).get("collections", []):
    name = entry.get("name")
    if isinstance(name, str) and name.startswith(prefixes):
        print(name)
' "${PREFIXES[@]}" <<<"$payload"
)

if [[ "${#collections[@]}" -eq 0 ]]; then
  echo "No matching Qdrant collections found."
  exit 0
fi

for collection in "${collections[@]}"; do
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf 'Would prune %s\n' "$collection"
    continue
  fi
  curl -fsS -X DELETE "$QDRANT_URL/collections/$collection" >/dev/null
  printf 'Pruned %s\n' "$collection"
done
