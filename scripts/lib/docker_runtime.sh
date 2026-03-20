#!/usr/bin/env bash

set -euo pipefail

# shellcheck disable=SC2034
DOCKER_RUNTIME_INSTALLED_BY_BOOTSTRAP=0
DOCKER_RUNTIME_LIB_DIR="$(cd "${BASH_SOURCE[0]%/*}" && pwd)"
# shellcheck disable=SC1091
source "$DOCKER_RUNTIME_LIB_DIR/setup_state.sh"

detect_docker_runtime_provider() {
  local host_os="$1"

  if docker_cli_installed; then
    printf 'docker'
    return 0
  fi

  if command -v docker-compose >/dev/null 2>&1; then
    printf 'docker-compose'
    return 0
  fi

  case "$host_os" in
    darwin)
      if command -v orb >/dev/null 2>&1 || [[ -d /Applications/OrbStack.app ]]; then
        printf 'OrbStack'
        return 0
      fi
      if command -v rdctl >/dev/null 2>&1 || [[ -d "/Applications/Rancher Desktop.app" ]]; then
        printf 'Rancher Desktop'
        return 0
      fi
      if command -v colima >/dev/null 2>&1; then
        printf 'Colima'
        return 0
      fi
      if [[ -d /Applications/Docker.app ]]; then
        printf 'Docker Desktop'
        return 0
      fi
      ;;
    linux)
      if command -v rdctl >/dev/null 2>&1; then
        printf 'Rancher Desktop'
        return 0
      fi
      if command -v podman >/dev/null 2>&1; then
        printf 'Podman'
        return 0
      fi
      if command -v colima >/dev/null 2>&1; then
        printf 'Colima'
        return 0
      fi
      if command -v nerdctl >/dev/null 2>&1; then
        printf 'containerd/nerdctl'
        return 0
      fi
      ;;
  esac

  return 1
}

docker_cli_installed() {
  command -v docker >/dev/null 2>&1
}

docker_compose_available() {
  docker_cli_installed || return 1
  docker compose version >/dev/null 2>&1
}

docker_backend_ready() {
  docker_compose_available || return 1
  docker info >/dev/null 2>&1
}

print_docker_runtime_enable_guidance() {
  local provider="$1"

  case "$provider" in
    "Docker Desktop")
      echo "Launch Docker Desktop once so it can provision \`docker\` and \`docker compose\`, then rerun bootstrap." >&2
      ;;
    OrbStack)
      echo "Open OrbStack and enable its Docker CLI integration, then rerun bootstrap." >&2
      ;;
    "Rancher Desktop")
      echo "Enable the Docker/Moby socket plus CLI integration in Rancher Desktop, then rerun bootstrap." >&2
      ;;
    Colima)
      echo "Start Colima with Docker socket support and ensure \`docker\` plus \`docker compose\` are on PATH, then rerun bootstrap." >&2
      ;;
    Podman)
      echo "Expose Podman through a Docker-compatible \`docker\` CLI with compose support, then rerun bootstrap." >&2
      ;;
    "containerd/nerdctl")
      echo "Expose a Docker-compatible \`docker\` CLI with compose support for nerdctl/containerd, then rerun bootstrap." >&2
      ;;
    docker-compose)
      echo "Install a Docker-compatible \`docker\` CLI that provides \`docker compose\`, then rerun bootstrap." >&2
      ;;
    *)
      echo "Enable the runtime's Docker-compatible CLI integration (\`docker\` plus \`docker compose\`), then rerun bootstrap." >&2
      ;;
  esac
}

ensure_docker_compatible_runtime() {
  local host_os="$1"
  local runtime_provider

  if docker_compose_available; then
    return 0
  fi

  runtime_provider="$(detect_docker_runtime_provider "$host_os" || true)"
  if docker_cli_installed; then
    echo "ERROR: docker is installed but \`docker compose\` is unavailable." >&2
    print_docker_runtime_enable_guidance "${runtime_provider:-docker}"
    exit 1
  fi

  if [[ -n "$runtime_provider" ]]; then
    echo "ERROR: detected $runtime_provider, but Strongclaw requires \`docker\` plus \`docker compose\` on PATH." >&2
    echo "Strongclaw will not install Docker over an existing alternative runtime." >&2
    print_docker_runtime_enable_guidance "$runtime_provider"
    exit 1
  fi

  case "$host_os" in
    darwin)
      command -v brew >/dev/null 2>&1 || {
        echo "ERROR: Homebrew is required to install Docker Desktop on macOS." >&2
        exit 1
      }
      brew install --cask docker
      ;;
    linux)
      command -v sudo >/dev/null 2>&1 || {
        echo "ERROR: sudo is required to install Docker on Linux." >&2
        exit 1
      }
      command -v apt-get >/dev/null 2>&1 || {
        echo "ERROR: apt-get is required to install Docker on Linux." >&2
        exit 1
      }
      sudo apt-get install -y docker.io docker-compose-plugin
      ;;
    *)
      echo "ERROR: unsupported host OS for Docker installation: $host_os" >&2
      exit 1
      ;;
  esac

  # shellcheck disable=SC2034
  DOCKER_RUNTIME_INSTALLED_BY_BOOTSTRAP=1
  if docker_compose_available; then
    return 0
  fi

  case "$host_os" in
    darwin)
      echo "ERROR: Docker Desktop was installed but the docker CLI is not ready yet." >&2
      echo "Launch Docker.app once, then rerun bootstrap." >&2
      ;;
    linux)
      echo "ERROR: Docker was installed but \`docker compose\` is still unavailable." >&2
      ;;
  esac
  exit 1
}

repair_linux_runtime_user_docker_access() {
  local runtime_user="${1:-$(id -un)}"
  local membership_added=0

  if ! getent group docker >/dev/null 2>&1; then
    echo "ERROR: docker group is missing after Docker install." >&2
    exit 1
  fi

  if ! id -nG "$runtime_user" | tr ' ' '\n' | grep -qx docker; then
    sudo usermod -aG docker "$runtime_user"
    mark_docker_shell_refresh_required "$runtime_user" "docker-group-membership-updated"
    membership_added=1
    echo "Added $runtime_user to the docker group."
    echo "A fresh login shell is required before this user session can activate Docker-backed services." >&2
  fi

  if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files docker.service >/dev/null 2>&1; then
    sudo systemctl enable --now docker.service
  else
    echo "WARNING: docker.service was not found under systemd; ensure the Docker daemon is running before activating sidecars." >&2
  fi

  if docker_backend_ready; then
    clear_docker_shell_refresh_required
    return 0
  fi

  if [[ "$membership_added" -eq 1 ]]; then
    echo "Setup will resume automatically after you open a fresh login shell and rerun \`clawops setup\`." >&2
    return 0
  fi

  if ! docker_backend_ready; then
    echo "WARNING: Docker is installed but not reachable from this shell yet." >&2
    if docker_shell_refresh_required; then
      echo "Start a fresh login shell as $runtime_user, then rerun \`clawops setup\`." >&2
    else
      echo "Ensure the Docker daemon is running before activating sidecar services." >&2
    fi
  fi
}

require_docker_backend_ready() {
  if ! docker_cli_installed; then
    echo "ERROR: A Docker-compatible runtime is required to activate the sidecar services." >&2
    exit 1
  fi

  if ! docker_compose_available; then
    echo "ERROR: docker is installed but \`docker compose\` is unavailable." >&2
    echo "Install or enable a Docker-compatible runtime that provides the compose plugin, then rerun with --activate." >&2
    exit 1
  fi

  if docker_backend_ready; then
    clear_docker_shell_refresh_required
    return 0
  fi

  echo "ERROR: Docker is not ready for the current user." >&2
  if docker_shell_refresh_required; then
    local runtime_user
    runtime_user="$(setup_state_value "$OPENCLAW_DOCKER_REFRESH_STATE_FILE" RUNTIME_USER)"
    if [[ -z "$runtime_user" ]]; then
      runtime_user="$(id -un)"
    fi
    echo "Docker access was granted during bootstrap, but this shell has not picked up the new group membership yet." >&2
    echo "Start a fresh login shell as $runtime_user, then rerun \`clawops setup\`." >&2
    exit 1
  fi
  echo "Ensure the Docker backend is running, then rerun the activation step." >&2
  exit 1
}
