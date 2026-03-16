#!/usr/bin/env bash
set -euo pipefail

echo "Rotate secrets in the source-of-truth secret store first."
echo "Then update .env files, run varlock load, and restart gateway + sidecars."
