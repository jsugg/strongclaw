"""Detect CI commands that install OpenClaw/ACPX (or any npm tree) outside a committed lock."""

from __future__ import annotations

import ast
import dataclasses
import re
import subprocess
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Final, cast

import yaml

CI_WORKFLOW_PREFIX: Final[str] = ".github/"
CI_PYTHON_PREFIXES: Final[tuple[str, ...]] = (
    "tests/scripts/",
    "tests/utils/helpers/_ci_workflows/",
)
_CI_YAML_SUFFIXES: Final[frozenset[str]] = frozenset({".yml", ".yaml"})
# Every npm verb that resolves a dependency tree from the registry instead of a lockfile.
_NPM_RESOLVING_INSTALL_VERBS: Final[frozenset[str]] = frozenset(
    {
        "add",
        "i",
        "in",
        "ins",
        "inst",
        "insta",
        "instal",
        "install",
        "install-test",
        "isnt",
        "isnta",
        "isntal",
        "isntall",
        "it",
    }
)
_NPM_EXEC_VERBS: Final[frozenset[str]] = frozenset({"exec", "x"})
_NPM_OPTIONS_WITH_VALUE: Final[frozenset[str]] = frozenset(
    {"--cache", "--prefix", "--registry", "--userconfig", "--workspace", "-C", "-w"}
)
_NPM_LOCK_ONLY_FLAG: Final[str] = "--package-lock-only"
_NODE_TOOL_SPEC_PATTERN: Final[re.Pattern[str]] = re.compile(r"^(?:openclaw|acpx)(?:@|$)")
_NPM_COMMAND_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w./-])(npm|npx)(?![\w.-])([^\n;&|`]*)"
)
_ARGV_EXPRESSION_PLACEHOLDER: Final[str] = "<expr>"


@dataclasses.dataclass(frozen=True, slots=True)
class FloatingNodeInstall:
    """One CI command that would resolve npm packages outside a committed lock."""

    path: Path
    command: str


def _npm_subcommand(arguments: Sequence[str]) -> tuple[int, str | None]:
    """Return the index and name of the npm subcommand, skipping global options."""
    skip_value = False
    for index, token in enumerate(arguments):
        if skip_value:
            skip_value = False
            continue
        if token in _NPM_OPTIONS_WITH_VALUE:
            skip_value = True
            continue
        if not token.startswith("-"):
            return index, token
    return len(arguments), None


def _names_node_tool(arguments: Sequence[str]) -> bool:
    """Return whether any argument is an OpenClaw/ACPX package spec."""
    return any(
        _NODE_TOOL_SPEC_PATTERN.match(token.removeprefix("--package=")) is not None
        for token in arguments
    )


def _is_floating_node_command(executable: str, arguments: Sequence[str]) -> bool:
    """Return whether one npm/npx invocation bypasses the committed lock."""
    if executable == "npx":
        return _names_node_tool(arguments)
    index, subcommand = _npm_subcommand(arguments)
    if subcommand in _NPM_RESOLVING_INSTALL_VERBS:
        return _NPM_LOCK_ONLY_FLAG not in arguments
    if subcommand in _NPM_EXEC_VERBS:
        return _names_node_tool(arguments[index + 1 :])
    return False


def find_floating_node_commands(text: str) -> list[str]:
    """Return every npm/npx command in shell-like *text* that resolves outside a lock.

    Any resolving `npm install` is reported, not only ones that name OpenClaw/ACPX: CI often
    passes the spec through a variable, so the package cannot be identified from the text.
    """
    commands: list[str] = []
    for match in _NPM_COMMAND_PATTERN.finditer(text):
        arguments = [token.strip("\"'") for token in match.group(2).split()]
        if _is_floating_node_command(match.group(1), arguments):
            commands.append(match.group(0).strip())
    return commands


def _yaml_strings(node: object) -> Iterator[str]:
    """Yield every string value in a loaded YAML document."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in cast(dict[object, object], node).values():
            yield from _yaml_strings(value)
    elif isinstance(node, list):
        for item in cast(list[object], node):
            yield from _yaml_strings(item)


def _python_command_texts(source: str, *, filename: str) -> Iterator[str]:
    """Yield string literals and argv-style list/tuple literals rendered as shell text."""
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value
        elif isinstance(node, (ast.List, ast.Tuple)):
            yield " ".join(
                (
                    element.value
                    if isinstance(element, ast.Constant) and isinstance(element.value, str)
                    else _ARGV_EXPRESSION_PLACEHOLDER
                )
                for element in node.elts
            )


def _tracked_ci_files(repo_root: Path) -> list[Path]:
    """Return repo-relative tracked files under the CI workflow and CI helper prefixes."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z", "--"]
        + [CI_WORKFLOW_PREFIX, *CI_PYTHON_PREFIXES],
        check=True,
        capture_output=True,
    )
    return [Path(raw.decode("utf-8")) for raw in result.stdout.split(b"\0") if raw]


def _command_texts(relative: Path, text: str) -> Iterator[str]:
    """Yield the shell-like texts one CI file can execute."""
    posix = relative.as_posix()
    if posix.startswith(CI_WORKFLOW_PREFIX) and relative.suffix in _CI_YAML_SUFFIXES:
        yield from _yaml_strings(yaml.safe_load(text))
    elif posix.startswith(CI_PYTHON_PREFIXES) and relative.suffix == ".py":
        yield from _python_command_texts(text, filename=posix)


def find_floating_node_installs(repo_root: Path) -> list[FloatingNodeInstall]:
    """Return lock-bypassing npm/npx commands in CI workflows and their Python helpers."""
    findings: dict[FloatingNodeInstall, None] = {}
    for relative in _tracked_ci_files(repo_root):
        path = repo_root / relative
        if not path.is_file():
            continue
        for candidate in _command_texts(relative, path.read_text(encoding="utf-8")):
            for command in find_floating_node_commands(candidate):
                findings[FloatingNodeInstall(path=relative, command=command)] = None
    return list(findings)
