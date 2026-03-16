"""Helpers for rendering OpenClaw config overlays with local paths."""

from __future__ import annotations

import argparse
import pathlib
from typing import Any

from clawops.common import load_json, write_json

REPO_ROOT_PLACEHOLDER = "__REPO_ROOT__"
HOME_PLACEHOLDER = "__HOME__"


def _replace_placeholders(value: Any, *, repo_root: pathlib.Path, home_dir: pathlib.Path) -> Any:
    """Recursively replace path placeholders in a JSON-like value."""
    if isinstance(value, str):
        return value.replace(REPO_ROOT_PLACEHOLDER, repo_root.as_posix()).replace(
            HOME_PLACEHOLDER, home_dir.as_posix()
        )
    if isinstance(value, list):
        return [
            _replace_placeholders(item, repo_root=repo_root, home_dir=home_dir) for item in value
        ]
    if isinstance(value, dict):
        return {
            str(key): _replace_placeholders(item, repo_root=repo_root, home_dir=home_dir)
            for key, item in value.items()
        }
    return value


def render_openclaw_overlay(
    *,
    template_path: pathlib.Path,
    repo_root: pathlib.Path,
    home_dir: pathlib.Path,
) -> dict[str, Any]:
    """Load an OpenClaw overlay template and replace local path placeholders."""
    template = load_json(template_path)
    rendered = _replace_placeholders(
        template,
        repo_root=repo_root.expanduser().resolve(),
        home_dir=home_dir.expanduser().resolve(),
    )
    if not isinstance(rendered, dict):
        raise TypeError("rendered OpenClaw overlay must be a mapping")
    return rendered


def render_qmd_overlay(
    *,
    template_path: pathlib.Path,
    repo_root: pathlib.Path,
    home_dir: pathlib.Path,
) -> dict[str, Any]:
    """Backward-compatible alias for the historical QMD overlay renderer."""
    return render_openclaw_overlay(
        template_path=template_path,
        repo_root=repo_root,
        home_dir=home_dir,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", required=True, type=pathlib.Path)
    parser.add_argument("--repo-root", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--home-dir", type=pathlib.Path, default=pathlib.Path.home())
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Render a placeholder-backed OpenClaw overlay to JSON."""
    args = parse_args(argv)
    rendered = render_openclaw_overlay(
        template_path=args.template,
        repo_root=args.repo_root,
        home_dir=args.home_dir,
    )
    write_json(args.output, rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
