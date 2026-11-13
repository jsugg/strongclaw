"""Install the first-party plugins OpenClaw must trust through `openclaw plugins install`.

OpenClaw 2026.6 grants trusted-plugin APIs (the ACP runtime's keyed session store) only to an
official package that its own installer recorded as an npm install; a `plugins.load.paths` copy
of the same package stays untrusted and cannot start ACP sessions. StrongClaw installs those
plugins with OpenClaw's installer at the exact version pinned in `platform/node-tools`.
"""

from __future__ import annotations

import dataclasses
import pathlib
from collections.abc import Mapping
from typing import Final, cast

from clawops.common import extract_json_document
from clawops.openclaw_plugins import FirstPartyPlugin, required_openclaw_installs
from clawops.platform_compat import NODE_TOOL_PINS
from clawops.strongclaw_runtime import CommandError, run_openclaw_command

INSPECT_TIMEOUT_SECONDS: Final[int] = 120
INSTALL_TIMEOUT_SECONDS: Final[int] = 900


@dataclasses.dataclass(frozen=True, slots=True)
class InstalledPluginState:
    """What `openclaw plugins inspect` reports for one plugin id."""

    installed: bool
    trusted: bool = False
    source: str | None = None
    version: str | None = None

    def matches(self, version: str) -> bool:
        """Return whether this is a trusted npm install of exactly *version*."""
        return self.installed and self.trusted and self.source == "npm" and self.version == version


def _mapping(value: object) -> Mapping[str, object]:
    """Return *value* as a string-keyed mapping, or an empty one."""
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def inspect_installed_plugin(repo_root: pathlib.Path, plugin_id: str) -> InstalledPluginState:
    """Return the install state OpenClaw records for *plugin_id*."""
    result = run_openclaw_command(
        repo_root,
        ["plugins", "inspect", plugin_id, "--json"],
        timeout_seconds=INSPECT_TIMEOUT_SECONDS,
    )
    if not result.ok:
        # `plugins inspect` exits non-zero for an id it does not know.
        return InstalledPluginState(installed=False)
    payload = _mapping(extract_json_document(result.stdout))
    plugin = _mapping(payload.get("plugin"))
    install = _mapping(payload.get("install"))
    if not install:
        return InstalledPluginState(installed=False)
    version = install.get("resolvedVersion") or install.get("version")
    source = install.get("source")
    return InstalledPluginState(
        installed=True,
        trusted=plugin.get("trustedOfficialInstall") is True,
        source=source if isinstance(source, str) else None,
        version=version if isinstance(version, str) else None,
    )


def _install(repo_root: pathlib.Path, plugin: FirstPartyPlugin, *, replace: bool) -> None:
    """Install *plugin* at its pinned version, replacing an existing install when asked."""
    arguments = ["plugins", "install", plugin.npm_spec, "--pin"]
    if replace:
        arguments.append("--force")
    result = run_openclaw_command(repo_root, arguments, timeout_seconds=INSTALL_TIMEOUT_SECONDS)
    if not result.ok:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise CommandError(
            f"`openclaw {' '.join(arguments)}` failed: {detail or 'no output'}", result=result
        )


def ensure_openclaw_plugin_installs(
    repo_root: pathlib.Path, config: Mapping[str, object]
) -> list[str]:
    """Install every trusted-install plugin *config* needs; return the ids (re)installed."""
    changed: list[str] = []
    for plugin in required_openclaw_installs(config):
        state = inspect_installed_plugin(repo_root, plugin.plugin_id)
        if state.matches(NODE_TOOL_PINS[plugin.package]):
            continue
        _install(repo_root, plugin, replace=state.installed)
        changed.append(plugin.plugin_id)
    return changed
