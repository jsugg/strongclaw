"""Helpers for rendering OpenClaw config overlays with local paths."""

from __future__ import annotations

import argparse
import dataclasses
import os
import pathlib
from collections.abc import Mapping, Sequence
from typing import Any

from clawops.app_paths import strongclaw_lossless_claw_dir
from clawops.common import load_overlay, write_json
from clawops.json_merge import merge_documents

REPO_ROOT_PLACEHOLDER = "__REPO_ROOT__"
HOME_PLACEHOLDER = "__HOME__"
WORKSPACE_ROOT_PLACEHOLDER = "__WORKSPACE_ROOT__"
UPSTREAM_REPO_ROOT_PLACEHOLDER = "__UPSTREAM_REPO_ROOT__"
WORKTREES_ROOT_PLACEHOLDER = "__WORKTREES_ROOT__"
PLUGIN_ROOT_PLACEHOLDER = "__PLUGIN_ROOT__"
LOSSLESS_CLAW_PLUGIN_PATH_PLACEHOLDER = "__LOSSLESS_CLAW_PLUGIN_PATH__"
OPENCLAW_HOME_PLACEHOLDER = "__OPENCLAW_HOME__"
USER_TIMEZONE_PLACEHOLDER = "__USER_TIMEZONE__"
ADMIN_WORKSPACE_PLACEHOLDER = "__ADMIN_WORKSPACE__"
READER_WORKSPACE_PLACEHOLDER = "__READER_WORKSPACE__"
CODER_WORKSPACE_PLACEHOLDER = "__CODER_WORKSPACE__"
REVIEWER_WORKSPACE_PLACEHOLDER = "__REVIEWER_WORKSPACE__"
MESSAGING_WORKSPACE_PLACEHOLDER = "__MESSAGING_WORKSPACE__"
OPENCLAW_CONFIG_DIR = pathlib.Path("platform/configs/openclaw")
DEFAULT_PROFILE_NAME = "hypermemory"
DEFAULT_OPENCLAW_CONFIG_OUTPUT = pathlib.Path.home() / ".openclaw" / "openclaw.json"
DEFAULT_EXEC_APPROVALS_TEMPLATE = OPENCLAW_CONFIG_DIR / "exec-approvals.json"


@dataclasses.dataclass(frozen=True, slots=True)
class RenderProfile:
    """Named OpenClaw config render profile."""

    name: str
    overlays: tuple[pathlib.Path, ...]
    description: str


PROFILES: dict[str, RenderProfile] = {
    "openclaw-default": RenderProfile(
        name="openclaw-default",
        overlays=(
            OPENCLAW_CONFIG_DIR / "00-baseline.json5",
            OPENCLAW_CONFIG_DIR / "10-trust-zones.json5",
        ),
        description="OpenClaw built-ins only: memory-core and the legacy context engine.",
    ),
    "openclaw-qmd": RenderProfile(
        name="openclaw-qmd",
        overlays=(
            OPENCLAW_CONFIG_DIR / "00-baseline.json5",
            OPENCLAW_CONFIG_DIR / "10-trust-zones.json5",
            OPENCLAW_CONFIG_DIR / "40-qmd-context.json5",
        ),
        description="OpenClaw built-ins plus the experimental QMD memory backend.",
    ),
    "memory-lancedb-pro": RenderProfile(
        name="memory-lancedb-pro",
        overlays=(
            OPENCLAW_CONFIG_DIR / "00-baseline.json5",
            OPENCLAW_CONFIG_DIR / "10-trust-zones.json5",
            OPENCLAW_CONFIG_DIR / "40-qmd-context.json5",
            OPENCLAW_CONFIG_DIR / "75-memory-lancedb-pro.local.json5",
        ),
        description="Vendored memory-lancedb-pro with Ollama-backed smart extraction.",
    ),
    "hypermemory": RenderProfile(
        name="hypermemory",
        overlays=(
            OPENCLAW_CONFIG_DIR / "00-baseline.json5",
            OPENCLAW_CONFIG_DIR / "10-trust-zones.json5",
            OPENCLAW_CONFIG_DIR / "77-hypermemory.example.json5",
        ),
        description="Lossless context compaction plus strongclaw-hypermemory sparse+dense recall.",
    ),
    "acp": RenderProfile(
        name="acp",
        overlays=(
            OPENCLAW_CONFIG_DIR / "00-baseline.json5",
            OPENCLAW_CONFIG_DIR / "10-trust-zones.json5",
            OPENCLAW_CONFIG_DIR / "40-qmd-context.json5",
            OPENCLAW_CONFIG_DIR / "20-acp-workers.json5",
        ),
        description="Legacy OpenClaw built-ins plus ACP worker agents.",
    ),
    "browser-lab": RenderProfile(
        name="browser-lab",
        overlays=(
            OPENCLAW_CONFIG_DIR / "00-baseline.json5",
            OPENCLAW_CONFIG_DIR / "10-trust-zones.json5",
            OPENCLAW_CONFIG_DIR / "40-qmd-context.json5",
            OPENCLAW_CONFIG_DIR / "60-browser-lab.json5",
        ),
        description="Legacy OpenClaw built-ins plus browser-lab integration.",
    ),
}


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
    *,
    repo_root: pathlib.Path,
    home_dir: pathlib.Path,
    user_timezone: str,
    lossless_claw_plugin_path: pathlib.Path | None = None,
) -> dict[str, str]:
    """Build the placeholder replacement table for rendered overlays."""
    resolved_repo_root = repo_root.expanduser().resolve()
    resolved_home_dir = home_dir.expanduser().resolve()
    workspace_root = resolved_repo_root / "platform" / "workspace"
    upstream_repo_root = resolved_repo_root / "repo" / "upstream"
    worktrees_root = resolved_repo_root / "repo" / "worktrees"
    plugin_root = resolved_repo_root / "platform" / "plugins"
    openclaw_home = resolved_home_dir / ".openclaw"
    replacements = {
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
    if lossless_claw_plugin_path is not None:
        replacements[LOSSLESS_CLAW_PLUGIN_PATH_PLACEHOLDER] = (
            lossless_claw_plugin_path.expanduser().resolve().as_posix()
        )
    return replacements


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


def _contains_placeholder(value: Any, placeholder: str) -> bool:
    """Return whether a loaded overlay still references a specific placeholder."""
    if isinstance(value, str):
        return placeholder in value
    if isinstance(value, list):
        return any(_contains_placeholder(item, placeholder) for item in value)
    if isinstance(value, dict):
        return any(
            _contains_placeholder(key, placeholder) or _contains_placeholder(item, placeholder)
            for key, item in value.items()
        )
    return False


def _resolve_lossless_claw_plugin_path(
    repo_root: pathlib.Path, *, home_dir: pathlib.Path | None = None
) -> pathlib.Path:
    """Return the configured or default lossless-claw plugin path."""
    configured = os.environ.get("OPENCLAW_LOSSLESS_CLAW_PLUGIN_PATH")
    if configured:
        configured_path = pathlib.Path(configured)
        candidate = (
            configured_path if configured_path.is_absolute() else repo_root / configured_path
        )
        return candidate.expanduser().resolve()

    app_data_path = strongclaw_lossless_claw_dir(home_dir=home_dir)
    vendored_path = (repo_root / "vendor" / "lossless-claw").expanduser().resolve()
    plugin_path = (repo_root / "platform" / "plugins" / "lossless-claw").expanduser().resolve()
    if app_data_path.is_dir():
        return app_data_path
    if vendored_path.is_dir():
        return vendored_path
    if plugin_path.is_dir():
        return plugin_path
    return app_data_path


def render_openclaw_overlay(
    *,
    template_path: pathlib.Path,
    repo_root: pathlib.Path,
    home_dir: pathlib.Path,
    user_timezone: str | None = None,
) -> dict[str, Any]:
    """Load an OpenClaw overlay template and replace local path placeholders."""
    template = load_overlay(template_path)
    lossless_claw_plugin_path = None
    if _contains_placeholder(template, LOSSLESS_CLAW_PLUGIN_PATH_PLACEHOLDER):
        lossless_claw_plugin_path = _resolve_lossless_claw_plugin_path(
            repo_root.expanduser().resolve(), home_dir=home_dir
        )
    rendered = _replace_placeholders(
        template,
        replacements=build_placeholder_map(
            repo_root=repo_root,
            home_dir=home_dir,
            user_timezone=detect_local_timezone() if user_timezone is None else user_timezone,
            lossless_claw_plugin_path=lossless_claw_plugin_path,
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


def _resolve_repo_relative_path(*, repo_root: pathlib.Path, path: pathlib.Path) -> pathlib.Path:
    """Resolve a possibly repo-relative path against *repo_root*."""
    if path.is_absolute():
        return path.expanduser().resolve()
    return (repo_root.expanduser().resolve() / path).resolve()


def _resolve_profile(profile_name: str) -> RenderProfile:
    """Look up a named render profile."""
    try:
        return PROFILES[profile_name]
    except KeyError as exc:
        available = ", ".join(sorted(PROFILES))
        raise ValueError(
            f"unknown OpenClaw render profile: {profile_name} (choose from {available})"
        ) from exc


def render_openclaw_profile(
    *,
    profile_name: str,
    repo_root: pathlib.Path,
    home_dir: pathlib.Path,
    user_timezone: str | None = None,
    extra_overlays: Sequence[pathlib.Path] = (),
) -> dict[str, Any]:
    """Render and merge a named OpenClaw config profile."""
    profile = _resolve_profile(profile_name)
    template_paths = [*profile.overlays, *extra_overlays]
    rendered_documents = [
        render_openclaw_overlay(
            template_path=_resolve_repo_relative_path(repo_root=repo_root, path=template_path),
            repo_root=repo_root,
            home_dir=home_dir,
            user_timezone=user_timezone,
        )
        for template_path in template_paths
    ]
    if not rendered_documents:
        raise ValueError(f"OpenClaw render profile {profile_name} did not resolve any overlays")
    base, *overlays = rendered_documents
    merged = merge_documents(base, overlays)
    if not isinstance(merged, dict):
        raise TypeError("rendered OpenClaw profile must merge to a mapping")
    return merged


def build_profile_help() -> str:
    """Return a compact help block for named profiles."""
    lines = ["available profiles:"]
    for profile_name in sorted(PROFILES):
        profile = PROFILES[profile_name]
        lines.append(f"  {profile.name:<23} {profile.description}")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=build_profile_help(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--template", type=pathlib.Path)
    parser.add_argument("--profile", choices=sorted(PROFILES))
    parser.add_argument(
        "--overlay",
        action="append",
        default=[],
        type=pathlib.Path,
        help="Additional overlay template to render and merge on top of the selected profile.",
    )
    parser.add_argument("--repo-root", required=True, type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path, default=DEFAULT_OPENCLAW_CONFIG_OUTPUT)
    parser.add_argument(
        "--exec-approvals-output",
        type=pathlib.Path,
        help="Optional path for a rendered exec-approvals policy file.",
    )
    parser.add_argument(
        "--exec-approvals-template",
        type=pathlib.Path,
        default=DEFAULT_EXEC_APPROVALS_TEMPLATE,
    )
    parser.add_argument("--home-dir", type=pathlib.Path, default=pathlib.Path.home())
    parser.add_argument("--user-timezone")
    args = parser.parse_args(argv)
    if args.template is not None and args.profile is not None:
        parser.error("--template and --profile are mutually exclusive")
    if args.template is not None and args.overlay:
        parser.error("--overlay can only be used with --profile")
    if args.template is None and args.profile is None:
        args.profile = DEFAULT_PROFILE_NAME
    return args


def main(argv: list[str] | None = None) -> int:
    """Render a placeholder-backed OpenClaw overlay to JSON."""
    args = parse_args(argv)
    if args.template is not None:
        rendered = render_openclaw_overlay(
            template_path=_resolve_repo_relative_path(repo_root=args.repo_root, path=args.template),
            repo_root=args.repo_root,
            home_dir=args.home_dir,
            user_timezone=args.user_timezone,
        )
    else:
        rendered = render_openclaw_profile(
            profile_name=args.profile,
            repo_root=args.repo_root,
            home_dir=args.home_dir,
            user_timezone=args.user_timezone,
            extra_overlays=tuple(args.overlay),
        )
    write_json(args.output, rendered)
    print(f"Rendered {args.output.expanduser().resolve()}")
    if args.exec_approvals_output is not None:
        approvals = render_openclaw_overlay(
            template_path=_resolve_repo_relative_path(
                repo_root=args.repo_root, path=args.exec_approvals_template
            ),
            repo_root=args.repo_root,
            home_dir=args.home_dir,
            user_timezone=args.user_timezone,
        )
        write_json(args.exec_approvals_output, approvals)
        print(f"Rendered {args.exec_approvals_output.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
