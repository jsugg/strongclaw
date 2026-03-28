"""Strongclaw-first bootstrap profile resolution for devflow."""

from __future__ import annotations

import dataclasses
import pathlib
import tomllib
from collections.abc import Sequence
from typing import Final, cast

from clawops.common import load_json, load_yaml
from clawops.runtime_assets import resolve_asset_path
from clawops.typed_values import as_mapping, as_mapping_list, as_string, as_string_list

DEFAULT_BOOTSTRAP_PATHS: Final[tuple[pathlib.Path, ...]] = (
    resolve_asset_path("platform/configs/devflow/bootstrap/strongclaw.yaml"),
    resolve_asset_path("platform/configs/devflow/bootstrap/defaults.yaml"),
)


@dataclasses.dataclass(frozen=True, slots=True)
class BootstrapMatch:
    """Match criteria for one bootstrap profile."""

    files: tuple[str, ...]
    python_project_name: str | None = None
    package_json_name: str | None = None
    go_mod_module: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class BootstrapProfile:
    """Resolved bootstrap profile contract."""

    profile_id: str
    commands: dict[str, tuple[tuple[str, ...], ...]]
    match: BootstrapMatch
    source_path: pathlib.Path

    def command_groups(self) -> tuple[str, ...]:
        """Return the declared command groups in insertion order."""
        return tuple(self.commands)

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe bootstrap profile."""
        return {
            "id": self.profile_id,
            "match": {
                "files": list(self.match.files),
                "python_project_name": self.match.python_project_name,
                "package_json_name": self.match.package_json_name,
                "go_mod_module": self.match.go_mod_module,
            },
            "commands": {
                name: [list(command) for command in commands]
                for name, commands in self.commands.items()
            },
            "source_path": self.source_path.as_posix(),
        }


def _load_pyproject_name(path: pathlib.Path) -> str | None:
    """Return the project name from ``pyproject.toml`` when present."""
    if not path.exists():
        return None
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    project = as_mapping(payload.get("project", {}), path="pyproject.toml.project")
    name = project.get("name")
    return name if isinstance(name, str) else None


def _load_package_json_name(path: pathlib.Path) -> str | None:
    """Return the package name from ``package.json`` when present."""
    if not path.exists():
        return None
    payload = load_json(path)
    mapping = as_mapping(payload, path="package.json")
    name = mapping.get("name")
    return name if isinstance(name, str) else None


def _load_go_module(path: pathlib.Path) -> str | None:
    """Return the ``go.mod`` module declaration when present."""
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("module "):
            return stripped.removeprefix("module ").strip()
    return None


def _parse_profile(payload: object, *, source_path: pathlib.Path) -> BootstrapProfile:
    """Return a validated bootstrap profile."""
    mapping = as_mapping(payload, path=f"bootstrap profile {source_path.name}")
    profile_id = as_string(mapping.get("id"), path=f"{source_path.name}.profiles[].id")
    match_payload = as_mapping(mapping.get("match", {}), path=f"{profile_id}.match")
    commands_payload = as_mapping(mapping.get("commands", {}), path=f"{profile_id}.commands")
    commands: dict[str, tuple[tuple[str, ...], ...]] = {}
    for name, value in commands_payload.items():
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError(f"{profile_id}.commands.{name} must be a sequence of commands")
        commands[name] = tuple(
            tuple(as_string_list(command, path=f"{profile_id}.commands.{name}[{index}]"))
            for index, command in enumerate(cast(Sequence[object], value))
        )
    return BootstrapProfile(
        profile_id=profile_id,
        commands=commands,
        match=BootstrapMatch(
            files=as_string_list(match_payload.get("files", []), path=f"{profile_id}.match.files"),
            python_project_name=(
                as_string(
                    match_payload.get("python_project_name"),
                    path=f"{profile_id}.match.python_project_name",
                )
                if match_payload.get("python_project_name") is not None
                else None
            ),
            package_json_name=(
                as_string(
                    match_payload.get("package_json_name"),
                    path=f"{profile_id}.match.package_json_name",
                )
                if match_payload.get("package_json_name") is not None
                else None
            ),
            go_mod_module=(
                as_string(
                    match_payload.get("go_mod_module"), path=f"{profile_id}.match.go_mod_module"
                )
                if match_payload.get("go_mod_module") is not None
                else None
            ),
        ),
        source_path=source_path,
    )


def load_bootstrap_profiles(
    paths: tuple[pathlib.Path, ...] = DEFAULT_BOOTSTRAP_PATHS,
) -> tuple[BootstrapProfile, ...]:
    """Load bootstrap profiles in precedence order."""
    profiles: list[BootstrapProfile] = []
    for raw_path in paths:
        source_path = raw_path.expanduser().resolve()
        payload = as_mapping(load_yaml(source_path), path=f"bootstrap catalog {source_path.name}")
        schema_version = payload.get("schema_version")
        if schema_version != 1:
            raise ValueError(
                f"unsupported bootstrap schema version in {source_path}: {schema_version!r}"
            )
        for entry in as_mapping_list(
            payload.get("profiles", []), path=f"{source_path.name}.profiles"
        ):
            profiles.append(_parse_profile(entry, source_path=source_path))
    return tuple(profiles)


def _profile_matches(profile: BootstrapProfile, repo_root: pathlib.Path) -> bool:
    """Return whether one bootstrap profile matches the repository."""
    for relative_path in profile.match.files:
        if not (repo_root / relative_path).exists():
            return False
    if profile.match.python_project_name is not None:
        if _load_pyproject_name(repo_root / "pyproject.toml") != profile.match.python_project_name:
            return False
    if profile.match.package_json_name is not None:
        if _load_package_json_name(repo_root / "package.json") != profile.match.package_json_name:
            return False
    if profile.match.go_mod_module is not None:
        if _load_go_module(repo_root / "go.mod") != profile.match.go_mod_module:
            return False
    return True


def resolve_bootstrap_profile(
    repo_root: pathlib.Path,
    *,
    paths: tuple[pathlib.Path, ...] = DEFAULT_BOOTSTRAP_PATHS,
) -> BootstrapProfile:
    """Resolve the first matching bootstrap profile."""
    resolved_repo_root = repo_root.expanduser().resolve()
    for profile in load_bootstrap_profiles(paths):
        if _profile_matches(profile, resolved_repo_root):
            return profile
    raise LookupError(f"no devflow bootstrap profile matched repository: {resolved_repo_root}")
