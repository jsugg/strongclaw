"""Inventory and refresh pinned supply-chain inputs."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import pathlib
import re
import time
from collections.abc import Mapping, Sequence
from urllib.parse import quote

import requests

from clawops.cli_roots import add_repo_root_argument
from clawops.common import ResultSummary
from clawops.process_runner import CommandResult, run_command
from clawops.root_detection import resolve_strongclaw_repo_root
from clawops.typed_values import ObjectMapping, as_mapping_list

ACTION_USE_RE = re.compile(
    r"^(?P<prefix>\s*-\s+uses:\s+)(?P<action>[^@\s]+)@(?P<ref>[^\s#]+)(?P<suffix>\s*(?:#.*)?)$"
)
TAG_COMMENT_RE = re.compile(r"#\s*(?P<tag>[^\s]+)")
COMPOSE_IMAGE_RE = re.compile(
    r"^(?P<prefix>\s*image:\s+)(?P<image>[^@\s]+)@(?P<digest>sha256:[0-9a-f]+)(?P<suffix>\s*)$"
)
DIGEST_LINE_RE = re.compile(r"^\s*Digest:\s*(?P<digest>sha256:[0-9a-f]+)\s*$")
QUALITY_GATE_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("uv", "run", "pre-commit", "run", "actionlint", "--all-files"),
    ("uv", "run", "pre-commit", "run", "shellcheck", "--all-files"),
    ("uv", "run", "isort", "--check-only", "src", "tests"),
    ("uv", "run", "ruff", "check", "src", "tests"),
    ("uv", "run", "black", "--check", "src", "tests"),
    ("uv", "run", "pyright"),
    ("uv", "run", "mypy"),
    (
        "bash",
        "-lc",
        "ulimit -n 4096 && uv run pytest -q --junitxml=pytest.xml "
        "--cov=src/clawops --cov-report=xml --cov-report=term-missing",
    ),
    (
        "python3",
        "./tests/scripts/security_workflow.py",
        "enforce-coverage-thresholds",
        "--coverage-file",
        "coverage.xml",
    ),
    ("uv", "run", "python", "-m", "compileall", "-q", "src", "tests"),
)


@dataclasses.dataclass(frozen=True, slots=True)
class WorkflowActionPin:
    """Pinned GitHub Action reference discovered in a workflow."""

    relative_path: str
    line_number: int
    prefix: str
    action: str
    ref: str
    suffix: str
    tag: str | None

    def to_dict(self) -> dict[str, str | int | None]:
        """Return a serializable view."""
        return {
            "path": self.relative_path,
            "line": self.line_number,
            "action": self.action,
            "ref": self.ref,
            "tag": self.tag,
        }

    def render(self, ref: str) -> str:
        """Render the updated workflow line."""
        return f"{self.prefix}{self.action}@{ref}{self.suffix}"


@dataclasses.dataclass(frozen=True, slots=True)
class ComposeImagePin:
    """Pinned compose image reference with a digest."""

    relative_path: str
    line_number: int
    prefix: str
    image: str
    digest: str
    suffix: str

    def to_dict(self) -> dict[str, str | int]:
        """Return a serializable view."""
        return {
            "path": self.relative_path,
            "line": self.line_number,
            "image": self.image,
            "digest": self.digest,
        }

    def render(self, digest: str) -> str:
        """Render the updated compose line."""
        return f"{self.prefix}{self.image}@{digest}{self.suffix}"


def _resolve_repo_root(repo_root: pathlib.Path | None) -> pathlib.Path:
    """Resolve the repository root."""
    return resolve_strongclaw_repo_root(repo_root)


def _workflow_files(repo_root: pathlib.Path) -> list[pathlib.Path]:
    """Return tracked workflow files."""
    workflow_dir = repo_root / ".github" / "workflows"
    return sorted(path for path in workflow_dir.glob("*.yml") if path.is_file())


def _compose_files(repo_root: pathlib.Path) -> list[pathlib.Path]:
    """Return compose files that pin container images."""
    compose_dir = repo_root / "platform" / "compose"
    return sorted(path for path in compose_dir.glob("*.y*ml") if path.is_file())


def list_workflow_action_pins(repo_root: pathlib.Path) -> list[WorkflowActionPin]:
    """Discover pinned GitHub Actions in workflow files."""
    resolved_root = _resolve_repo_root(repo_root)
    pins: list[WorkflowActionPin] = []
    for path in _workflow_files(resolved_root):
        for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            match = ACTION_USE_RE.match(raw_line)
            if match is None:
                continue
            suffix = match.group("suffix")
            tag_match = TAG_COMMENT_RE.search(suffix)
            pins.append(
                WorkflowActionPin(
                    relative_path=path.relative_to(resolved_root).as_posix(),
                    line_number=line_number,
                    prefix=match.group("prefix"),
                    action=match.group("action"),
                    ref=match.group("ref"),
                    suffix=suffix,
                    tag=None if tag_match is None else tag_match.group("tag"),
                )
            )
    return pins


def list_compose_image_pins(repo_root: pathlib.Path) -> list[ComposeImagePin]:
    """Discover digest-pinned compose images."""
    resolved_root = _resolve_repo_root(repo_root)
    pins: list[ComposeImagePin] = []
    for path in _compose_files(resolved_root):
        for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            match = COMPOSE_IMAGE_RE.match(raw_line)
            if match is None:
                continue
            pins.append(
                ComposeImagePin(
                    relative_path=path.relative_to(resolved_root).as_posix(),
                    line_number=line_number,
                    prefix=match.group("prefix"),
                    image=match.group("image"),
                    digest=match.group("digest"),
                    suffix=match.group("suffix"),
                )
            )
    return pins


def inventory_pins(repo_root: pathlib.Path) -> dict[str, object]:
    """Return the current supply-chain pin inventory."""
    resolved_root = _resolve_repo_root(repo_root)
    action_pins = list_workflow_action_pins(resolved_root)
    compose_pins = list_compose_image_pins(resolved_root)
    return {
        "ok": True,
        "repoRoot": resolved_root.as_posix(),
        "workflowActions": [pin.to_dict() for pin in action_pins],
        "composeImages": [pin.to_dict() for pin in compose_pins],
    }


def _github_api_headers() -> dict[str, str]:
    """Build headers for the public GitHub API."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def resolve_workflow_action_sha(
    action: str, tag: str, *, github_api_base: str = "https://api.github.com"
) -> str:
    """Resolve the commit SHA for an action tag using the GitHub commits API."""
    parts = action.split("/")
    if len(parts) < 2:
        raise ValueError(f"invalid GitHub action reference: {action}")
    repo_slug = "/".join(parts[:2])
    ref = quote(tag, safe="")
    response = requests.get(
        f"{github_api_base.rstrip('/')}/repos/{repo_slug}/commits/{ref}",
        headers=_github_api_headers(),
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    sha = payload.get("sha")
    if not isinstance(sha, str) or len(sha) != 40:
        raise RuntimeError(f"unable to resolve a full commit SHA for {action}@{tag}")
    return sha


def _run_docker_inspect(image: str) -> CommandResult:
    """Inspect the remote image metadata via docker buildx."""
    return run_command(
        ["docker", "buildx", "imagetools", "inspect", image],
        timeout_seconds=120,
    )


def resolve_compose_image_digest(image: str) -> str:
    """Resolve the current digest for a compose image reference."""
    result = _run_docker_inspect(image)
    if not result.ok:
        raise RuntimeError(
            result.stderr.strip() or result.stdout.strip() or f"unable to inspect image {image}"
        )
    for line in result.stdout.splitlines():
        match = DIGEST_LINE_RE.match(line)
        if match is not None:
            return match.group("digest")
    raise RuntimeError(f"unable to parse a digest for image {image}")


def _apply_line_updates(
    repo_root: pathlib.Path, relative_path: str, replacements: Mapping[int, str]
) -> None:
    """Rewrite specific lines in a text file."""
    path = repo_root / relative_path
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for line_number, replacement in replacements.items():
        original_line = lines[line_number - 1]
        terminator = "\n" if original_line.endswith("\n") else ""
        lines[line_number - 1] = replacement + terminator
    path.write_text("".join(lines), encoding="utf-8")


def _updated_entries(payload: dict[str, object], *, path: str) -> list[ObjectMapping]:
    """Return the typed update entries from a refresh payload."""
    return list(as_mapping_list(payload.get("updated"), path=f"{path}.updated"))


def refresh_workflow_action_pins(
    repo_root: pathlib.Path,
    *,
    apply: bool,
    github_api_base: str = "https://api.github.com",
) -> dict[str, object]:
    """Refresh workflow action SHAs based on the version tags in comments."""
    resolved_root = _resolve_repo_root(repo_root)
    updates: dict[str, dict[int, str]] = {}
    refreshed: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    for pin in list_workflow_action_pins(resolved_root):
        if pin.tag is None:
            skipped.append(
                {"path": pin.relative_path, "line": pin.line_number, "reason": "missing-tag"}
            )
            continue
        resolved_sha = resolve_workflow_action_sha(
            pin.action, pin.tag, github_api_base=github_api_base
        )
        if resolved_sha == pin.ref:
            continue
        updates.setdefault(pin.relative_path, {})[pin.line_number] = pin.render(resolved_sha)
        refreshed.append(
            {
                "path": pin.relative_path,
                "line": pin.line_number,
                "action": pin.action,
                "from": pin.ref,
                "to": resolved_sha,
                "tag": pin.tag,
            }
        )
    if apply:
        for relative_path, replacements in updates.items():
            _apply_line_updates(resolved_root, relative_path, replacements)
    return {
        "ok": True,
        "apply": apply,
        "updated": refreshed,
        "skipped": skipped,
    }


def refresh_compose_image_digests(repo_root: pathlib.Path, *, apply: bool) -> dict[str, object]:
    """Refresh compose image digests using docker buildx imagetools inspect."""
    resolved_root = _resolve_repo_root(repo_root)
    updates: dict[str, dict[int, str]] = {}
    refreshed: list[dict[str, object]] = []
    for pin in list_compose_image_pins(resolved_root):
        resolved_digest = resolve_compose_image_digest(pin.image)
        if resolved_digest == pin.digest:
            continue
        updates.setdefault(pin.relative_path, {})[pin.line_number] = pin.render(resolved_digest)
        refreshed.append(
            {
                "path": pin.relative_path,
                "line": pin.line_number,
                "image": pin.image,
                "from": pin.digest,
                "to": resolved_digest,
            }
        )
    if apply:
        for relative_path, replacements in updates.items():
            _apply_line_updates(resolved_root, relative_path, replacements)
    return {"ok": True, "apply": apply, "updated": refreshed}


def _git(repo_root: pathlib.Path, *arguments: str) -> CommandResult:
    """Run a git command in the repository."""
    return run_command(["git", "-C", str(repo_root), *arguments], timeout_seconds=120)


def _ensure_git_clean(repo_root: pathlib.Path) -> None:
    """Require a clean git worktree before mutating refreshes."""
    status = _git(repo_root, "status", "--short")
    if not status.ok:
        raise RuntimeError(status.stderr.strip() or status.stdout.strip() or "git status failed")
    if status.stdout.strip():
        raise RuntimeError("refusing to propose a supply-chain refresh from a dirty worktree")


def _switch_branch(repo_root: pathlib.Path, branch: str) -> None:
    """Create or reuse the working branch for a refresh proposal."""
    branch_exists = _git(repo_root, "rev-parse", "--verify", f"refs/heads/{branch}")
    if branch_exists.ok:
        result = _git(repo_root, "switch", branch)
    else:
        result = _git(repo_root, "switch", "-c", branch)
    if not result.ok:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git switch failed")


def quality_gate(repo_root: pathlib.Path) -> dict[str, object]:
    """Run the repository quality gate and return the executed command surface."""
    resolved_root = _resolve_repo_root(repo_root)
    env = dict(os.environ)
    env["CLAWOPS_HTTP_RETRY_MODE"] = env.get("CLAWOPS_HTTP_RETRY_MODE", "safe")
    env["PYTHONPATH"] = "src"
    for command in QUALITY_GATE_COMMANDS:
        result = run_command(list(command), cwd=resolved_root, env=env, timeout_seconds=1800)
        if not result.ok:
            raise RuntimeError(
                result.stderr.strip() or result.stdout.strip() or "quality gate failed"
            )
    return {
        "ok": True,
        "repoRoot": resolved_root.as_posix(),
        "commands": [list(command) for command in QUALITY_GATE_COMMANDS],
    }


def _run_sbom_generation(repo_root: pathlib.Path) -> str:
    """Generate an SBOM artifact in a temporary ignored location."""
    output_path = repo_root / ".tmp" / "supply-chain-refresh" / "sbom.spdx.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = run_command(
        ["syft", "dir:.", "-o", f"spdx-json={output_path}"], cwd=repo_root, timeout_seconds=600
    )
    if not result.ok:
        raise RuntimeError(
            result.stderr.strip() or result.stdout.strip() or "SBOM generation failed"
        )
    return output_path.as_posix()


def _run_refresh_command(repo_root: pathlib.Path, command: str) -> None:
    """Run an operator-supplied refresh command."""
    result = run_command(["bash", "-lc", command], cwd=repo_root, timeout_seconds=1800)
    if not result.ok:
        raise RuntimeError(
            result.stderr.strip() or result.stdout.strip() or f"refresh command failed: {command}"
        )


def _changed_files(repo_root: pathlib.Path) -> list[str]:
    """Return changed tracked files after staged refreshes."""
    result = _git(repo_root, "status", "--short")
    if not result.ok:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git status failed")
    files: list[str] = []
    for raw_line in result.stdout.splitlines():
        if not raw_line.strip():
            continue
        parts = raw_line.strip().split(maxsplit=1)
        if len(parts) == 2:
            files.append(parts[1].strip())
    return files


def propose_refresh(
    repo_root: pathlib.Path,
    *,
    branch: str | None,
    base_branch: str,
    create_pr: bool,
    refresh_actions: bool,
    refresh_compose_digests_enabled: bool,
    refresh_commands: Sequence[str],
    commit_message: str,
    title: str,
    body: str,
    dry_run: bool,
) -> dict[str, object]:
    """Refresh pinned inputs, run quality gates, and optionally open a PR."""
    resolved_root = _resolve_repo_root(repo_root)
    empty_refresh_payload: dict[str, object] = {"updated": []}
    if dry_run:
        action_payload = (
            refresh_workflow_action_pins(resolved_root, apply=False)
            if refresh_actions
            else empty_refresh_payload
        )
        compose_payload = (
            refresh_compose_image_digests(resolved_root, apply=False)
            if refresh_compose_digests_enabled
            else empty_refresh_payload
        )
        action_updates = _updated_entries(action_payload, path="workflow_action_refresh")
        compose_updates = _updated_entries(compose_payload, path="compose_image_refresh")
        return {
            "ok": True,
            "dryRun": True,
            "workflowActions": action_updates,
            "composeImages": compose_updates,
            "refreshCommands": list(refresh_commands),
        }

    _ensure_git_clean(resolved_root)
    branch_name = branch or f"chore/supply-chain-refresh-{time.strftime('%Y%m%d%H%M%S')}"
    _switch_branch(resolved_root, branch_name)

    action_payload = (
        refresh_workflow_action_pins(resolved_root, apply=True)
        if refresh_actions
        else empty_refresh_payload
    )
    compose_payload = (
        refresh_compose_image_digests(resolved_root, apply=True)
        if refresh_compose_digests_enabled
        else empty_refresh_payload
    )
    action_updates = _updated_entries(action_payload, path="workflow_action_refresh")
    compose_updates = _updated_entries(compose_payload, path="compose_image_refresh")
    for command in refresh_commands:
        _run_refresh_command(resolved_root, command)

    quality_gate(resolved_root)
    sbom_path = _run_sbom_generation(resolved_root)

    add_result = _git(resolved_root, "add", "-A")
    if not add_result.ok:
        raise RuntimeError(
            add_result.stderr.strip() or add_result.stdout.strip() or "git add failed"
        )

    changed_files = _changed_files(resolved_root)
    if not changed_files:
        return {
            "ok": True,
            "branch": branch_name,
            "baseBranch": base_branch,
            "noChanges": True,
            "sbomPath": sbom_path,
            "workflowActions": action_updates,
            "composeImages": compose_updates,
        }

    commit_result = _git(resolved_root, "commit", "-m", commit_message)
    if not commit_result.ok:
        raise RuntimeError(
            commit_result.stderr.strip() or commit_result.stdout.strip() or "git commit failed"
        )

    pr_payload: dict[str, object] = {}
    if create_pr:
        pr_result = run_command(
            [
                "gh",
                "pr",
                "create",
                "--base",
                base_branch,
                "--head",
                branch_name,
                "--title",
                title,
                "--body",
                body,
            ],
            cwd=resolved_root,
            timeout_seconds=180,
        )
        if not pr_result.ok:
            raise RuntimeError(
                pr_result.stderr.strip() or pr_result.stdout.strip() or "gh pr create failed"
            )
        pr_payload["prUrl"] = pr_result.stdout.strip()

    return {
        "ok": True,
        "branch": branch_name,
        "baseBranch": base_branch,
        "changedFiles": changed_files,
        "sbomPath": sbom_path,
        "workflowActions": action_updates,
        "composeImages": compose_updates,
        **pr_payload,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse arguments for the supply-chain CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    add_repo_root_argument(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("inventory", help="List workflow action pins and compose image digests.")
    sub.add_parser("quality-gate", help="Run the repository quality gate.")

    refresh_actions = sub.add_parser(
        "refresh-actions", help="Refresh workflow action SHAs from pinned release tags."
    )
    refresh_actions.add_argument("--check", action="store_true")
    refresh_actions.add_argument("--github-api-base", default="https://api.github.com")

    refresh_compose = sub.add_parser(
        "refresh-compose-digests",
        help="Refresh compose image digests using docker buildx imagetools inspect.",
    )
    refresh_compose.add_argument("--check", action="store_true")

    propose = sub.add_parser(
        "propose-refresh",
        help="Refresh pinned inputs, run quality gates, and optionally open a PR.",
    )
    propose.add_argument("--branch")
    propose.add_argument("--base-branch", default="main")
    propose.add_argument("--create-pr", action="store_true")
    propose.add_argument("--skip-action-refresh", action="store_true")
    propose.add_argument("--skip-compose-refresh", action="store_true")
    propose.add_argument("--refresh-command", action="append", default=[])
    propose.add_argument(
        "--commit-message",
        default="chore: refresh pinned supply-chain inputs",
    )
    propose.add_argument(
        "--title",
        default="chore: refresh pinned supply-chain inputs",
    )
    propose.add_argument(
        "--body",
        default="Refresh pinned workflows and image digests, then rerun the repository quality gate and SBOM generation.",
    )
    propose.add_argument("--dry-run", action="store_true")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the supply-chain CLI."""
    args = parse_args(argv)
    try:
        if args.command == "inventory":
            print(json.dumps(inventory_pins(args.repo_root), indent=2, sort_keys=True))
            return 0
        if args.command == "quality-gate":
            print(json.dumps(quality_gate(args.repo_root), indent=2, sort_keys=True))
            return 0
        if args.command == "refresh-actions":
            payload = refresh_workflow_action_pins(
                args.repo_root,
                apply=not args.check,
                github_api_base=args.github_api_base,
            )
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 1 if args.check and payload["updated"] else 0
        if args.command == "refresh-compose-digests":
            payload = refresh_compose_image_digests(args.repo_root, apply=not args.check)
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 1 if args.check and payload["updated"] else 0
        if args.command == "propose-refresh":
            payload = propose_refresh(
                args.repo_root,
                branch=args.branch,
                base_branch=args.base_branch,
                create_pr=args.create_pr,
                refresh_actions=not args.skip_action_refresh,
                refresh_compose_digests_enabled=not args.skip_compose_refresh,
                refresh_commands=args.refresh_command,
                commit_message=args.commit_message,
                title=args.title,
                body=args.body,
                dry_run=args.dry_run,
            )
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
    except (RuntimeError, ValueError, requests.RequestException) as exc:
        print(json.dumps(ResultSummary(False, str(exc)).to_dict(), indent=2, sort_keys=True))
        return 1

    print(
        json.dumps(
            ResultSummary(False, f"unsupported supply-chain command: {args.command}").to_dict(),
            indent=2,
            sort_keys=True,
        )
    )
    return 2
