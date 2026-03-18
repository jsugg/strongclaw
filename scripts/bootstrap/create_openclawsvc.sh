#!/usr/bin/env bash
set -euo pipefail

USERNAME="${1:-openclawsvc}"
HOST_OS="$(uname -s)"

if id "$USERNAME" >/dev/null 2>&1; then
  echo "user already exists: $USERNAME"
  exit 0
fi

case "$HOST_OS" in
  Darwin)
    echo "Creating macOS runtime user: $USERNAME"
    echo "macOS will prompt for an admin password and the new user's password."
    sudo sysadminctl -addUser "$USERNAME" -admin NO
    echo "Done. You can now SSH to localhost as $USERNAME if Remote Login is enabled."
    ;;
  Linux)
    if [[ "$(id -u)" -ne 0 ]]; then
      echo "Run with sudo to create the Linux runtime user: $USERNAME" >&2
      exit 1
    fi

    command -v useradd >/dev/null 2>&1 || {
      echo "useradd is required for Linux runtime-user provisioning." >&2
      exit 1
    }

    echo "Creating Linux runtime user: $USERNAME"
    useradd --create-home --shell /bin/bash "$USERNAME"

    if command -v getent >/dev/null 2>&1 && getent group docker >/dev/null 2>&1; then
      command -v usermod >/dev/null 2>&1 || {
        echo "usermod is required to add the runtime user to the docker group." >&2
        exit 1
      }
      usermod -aG docker "$USERNAME"
      echo "Added $USERNAME to the docker group."
    else
      echo "WARNING: docker group not found; configure Docker access for $USERNAME manually." >&2
    fi

    if command -v loginctl >/dev/null 2>&1; then
      loginctl enable-linger "$USERNAME"
      echo "Enabled user linger for $USERNAME."
    else
      echo "WARNING: loginctl not found; enable lingering manually if you rely on user-level systemd." >&2
    fi

    echo "Done. Switch into the runtime account with: sudo -iu $USERNAME"
    ;;
  *)
    echo "unsupported host OS for runtime-user provisioning: $HOST_OS" >&2
    exit 1
    ;;
esac
