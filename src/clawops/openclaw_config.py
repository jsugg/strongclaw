"""Helpers for rendering OpenClaw config overlays with local paths."""

from __future__ import annotations

import argparse
import os
import pathlib
from collections.abc import Mapping
from typing import Any

from clawops.common import load_json, write_json

REPO_ROOT_PLACEHOLDER = "__REPO_ROOT__"
HOME_PLACEHOLDER = "__HOME__"
WORKSPACE_ROOT_PLACEHOLDER = "__WORKSPACE_ROOT__"
UPSTREAM_REPO_ROOT_PLACEHOLDER = "__UPSTREAM_REPO_ROOT__"
WORKTREES_ROOT_PLACEHOLDER = "__WORKTREES_ROOT__"
PLUGIN_ROOT_PLACEHOLDER = "__PLUGIN_ROOT__"
OPENCLAW_HOME_PLACEHOLDER = "__OPENCLAW_HOME__"
USER_TIMEZONE_PLACEHOLDER = "__USER_TIMEZONE__"
ADMIN_WORKSPACE_PLACEHOLDER = "__ADMIN_WORKSPACE__"
READER_WORKSPACE_PLACEHOLDER = "__READER_WORKSPACE__"
CODER_WORKSPACE_PLACEHOLDER = "__CODER_WORKSPACE__"
REVIEWER_WORKSPACE_PLACEHOLDER = "__REVIEWER_WORKSPACE__"
MESSAGING_WORKSPACE_PLACEHOLDER = "__MESSAGING_WORKSPACE__"


def detect_local_timezone() -> str:
    """Best-effort detection for the host IANA timezone."""
    configured = os.environ.get("OPENCLAW_USER_TIMEZONE") or os.environ.get("TZ")
    if configured:
        return configured
    localtime = pathlib.Path("/etc/localtime")
    try:
        resolved = localtime.resolve(strict=True)
    except OSError:
        return "UTC"
    parts = resolved.as_posix().split("/zoneinfo/", 1)
    if len(parts) != 2 or not parts[1]:
        return "UTC"
    return parts[1]


def build_placeholder_map(
    *, repo_root: pathlib.Path, home_dir: pathlib.Path, user_timezone: str
) -> dict[str, str]:
    """Build the placeholder replacement table for rendered overlays."""
    resolved_repo_root = repo_root.expanduser().resolve()
    resolved_home_dir = home_dir.expanduser().resolve()
    workspace_root = resolved_repo_root / "platform" / "workspace"
    upstream_repo_root = resolved_repo_root / "repo" / "upstream"
    worktrees_root = resolved_repo_root / "repo" / "worktrees"
    plugin_root = resolved_repo_root / "platform" / "plugins"
    openclaw_home = resolved_home_dir / ".openclaw"
    return {
        REPO_ROOT_PLACEHOLDER: resolved_repo_root.as_posix(),
        HOME_PLACEHOLDER: resolved_home_dir.as_posix(),
        WORKSPACE_ROOT_PLACEHOLDER: workspace_root.as_posix(),
        UPSTREAM_REPO_ROOT_PLACEHOLDER: upstream_repo_root.as_posix(),
        WORKTREES_ROOT_PLACEHOLDER: worktrees_root.as_posix(),
        PLUGIN_ROOT_PLACEHOLDER: plugin_root.as_posix(),
        OPENCLAW_HOME_PLACEHOLDER: openclaw_home.as_posix(),
        USER_TIMEZONE_PLACEHOLDER: user_timezone,
        ADMIN_WORKSPACE_PLACEHOLDER: (workspace_root / "admin").as_posix(),
        READER_WORKSPACE_PLACEHOLDER: (workspace_root / "reader").as_posix(),
        CODER_WORKSPACE_PLACEHOLDER: (workspace_root / "coder").as_posix(),
        REVIEWER_WORKSPACE_PLACEHOLDER: (workspace_root / "reviewer").as_posix(),
        MESSAGING_WORKSPACE_PLACEHOLDER: (workspace_root / "messaging").as_posix(),
    }


def _replace_placeholders(value: Any, *, replacements: Mapping[str, str]) -> Any:
    """Recursively replace path placeholders in a JSON-like value."""
    if isinstance(value, str):
        rendered = value
        for placeholder, replacement in replacements.items():
            rendered = rendered.replace(placeholder, replacement)
        return rendered
    if isinstance(value, list):
        return [_replace_placeholders(item, replacements=replacements) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _replace_placeholders(item, replacements=replacements)
            for key, item in value.items()
        }
    return value


def render_openclaw_overlay(
    *,
    template_path: pathlib.Path,
    repo_root: pathlib.Path,
    home_dir: pathlib.Path,
    user_timezone: str | None = None,
) -> dict[str, Any]:
    """Load an OpenClaw overlay template and replace local path placeholders."""
    template = load_json(template_path)
    rendered = _replace_placeholders(
        template,
        replacements=build_placeholder_map(
            repo_root=repo_root,
            home_dir=home_dir,
            user_timezone=detect_local_timezone() if user_timezone is None else user_timezone,
        ),
    )
    if not isinstance(rendered, dict):
        raise TypeError("rendered OpenClaw overlay must be a mapping")
    return rendered


def render_qmd_overlay(
    *,
    template_path: pathlib.Path,
    repo_root: pathlib.Path,
    home_dir: pathlib.Path,
    user_timezone: str | None = None,
) -> dict[str, Any]:
    """Backward-compatible alias for the historical QMD overlay renderer."""
    return render_openclaw_overlay(
        template_path=template_path,
        repo_root=repo_root,
        home_dir=home_dir,
        user_timezone=user_timezone,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", required=True, type=pathlib.Path)
    parser.add_argument("--repo-root", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--home-dir", type=pathlib.Path, default=pathlib.Path.home())
    parser.add_argument("--user-timezone")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Render a placeholder-backed OpenClaw overlay to JSON."""
    args = parse_args(argv)
    rendered = render_openclaw_overlay(
        template_path=args.template,
        repo_root=args.repo_root,
        home_dir=args.home_dir,
        user_timezone=args.user_timezone,
    )
    write_json(args.output, rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
