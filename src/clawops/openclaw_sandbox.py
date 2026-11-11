"""Provision the Docker image OpenClaw's agent sandbox runs tools in.

StrongClaw configs run every agent with `sandbox.mode: "all"`. OpenClaw 2026.3 pulled
`debian:bookworm-slim` and retagged it as the default sandbox image on first use; 2026.5+ fails
the turn instead and expects the operator to build the image, which must carry `python3` for the
sandbox write/edit helpers. StrongClaw builds it from upstream's documented recipe on a
digest-pinned base, and labels it with the recipe digest so stale or foreign images (including
the old retagged base) are rebuilt.
"""

from __future__ import annotations

import hashlib
import pathlib
import tempfile
from typing import Final

from clawops.strongclaw_runtime import CommandError, run_command, run_command_inherited

SANDBOX_IMAGE: Final[str] = "openclaw-sandbox:bookworm-slim"
SANDBOX_IMAGE_LABEL: Final[str] = "ai.strongclaw.openclaw-sandbox.recipe"
# Upstream recipe: docs/gateway/sandboxing.md ("Build the default image", npm install path).
SANDBOX_DOCKERFILE: Final[str] = """\
FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \\
  bash ca-certificates curl git jq python3 ripgrep \\
  && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --shell /bin/bash sandbox
USER sandbox
WORKDIR /home/sandbox
CMD ["sleep", "infinity"]
"""
_BUILD_TIMEOUT_SECONDS: Final[int] = 1800
_INSPECT_TIMEOUT_SECONDS: Final[int] = 60


def sandbox_recipe_digest() -> str:
    """Return the digest of the sandbox recipe that labels a StrongClaw-built image."""
    return hashlib.sha256(SANDBOX_DOCKERFILE.encode("utf-8")).hexdigest()[:16]


def installed_sandbox_recipe() -> str | None:
    """Return the recipe label of the local sandbox image, or None when absent or unlabeled."""
    result = run_command(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            f'{{{{ index .Config.Labels "{SANDBOX_IMAGE_LABEL}" }}}}',
            SANDBOX_IMAGE,
        ],
        timeout_seconds=_INSPECT_TIMEOUT_SECONDS,
    )
    label = result.stdout.strip()
    if not result.ok or not label or label == "<no value>":
        return None
    return label


def ensure_openclaw_sandbox_image() -> bool:
    """Build the sandbox image unless the current recipe is already installed.

    Returns True when an image was built.
    """
    recipe = sandbox_recipe_digest()
    if installed_sandbox_recipe() == recipe:
        return False
    with tempfile.TemporaryDirectory(prefix="strongclaw-openclaw-sandbox.") as context_dir:
        dockerfile = pathlib.Path(context_dir) / "Dockerfile"
        dockerfile.write_text(SANDBOX_DOCKERFILE, encoding="utf-8")
        returncode = run_command_inherited(
            [
                "docker",
                "build",
                "--label",
                f"{SANDBOX_IMAGE_LABEL}={recipe}",
                "--tag",
                SANDBOX_IMAGE,
                "--file",
                str(dockerfile),
                context_dir,
            ],
            timeout_seconds=_BUILD_TIMEOUT_SECONDS,
        )
    if returncode != 0:
        raise CommandError(f"building the OpenClaw sandbox image {SANDBOX_IMAGE} failed")
    return True
