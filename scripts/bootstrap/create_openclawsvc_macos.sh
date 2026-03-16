#!/usr/bin/env bash
set -euo pipefail

USERNAME="${1:-openclawsvc}"

if id "$USERNAME" >/dev/null 2>&1; then
  echo "user already exists: $USERNAME"
  exit 0
fi

echo "Creating standard macOS user: $USERNAME"
echo "macOS will prompt for an admin password and the new user's password."
sudo sysadminctl -addUser "$USERNAME" -admin NO
echo "Done. You can now SSH to localhost as $USERNAME if Remote Login is enabled."
