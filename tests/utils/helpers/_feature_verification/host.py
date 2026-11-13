"""Black-box access to a provisioned feature-verification host.

Tests drive the product only through its public entrypoints (``clawops``, ``openclaw`` via
Varlock, ``acpx``) as an operator would, with an environment scrubbed of harness variables so a
nested product run (for example ``clawops baseline verify`` running pytest) never re-enters
the feature suite.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, cast

from clawops.app_paths import strongclaw_node_tools_dir
from clawops.strongclaw_runtime import (
    load_env_assignments,
    load_openclaw_config,
    resolve_openclaw_config_path,
    varlock_env_dir,
    varlock_local_env_file,
    wrap_command_with_varlock,
)
from tests.utils.helpers._feature_verification.envvars import (
    CONTEXT_ENV,
    FAKE_MODEL_DIR_ENV,
    LANE_ENV,
    MANIFEST_ENV,
    WORK_DIR_ENV,
)
from tests.utils.helpers._feature_verification.manifest import (
    FeatureManifest,
    FeatureVerificationError,
    Lane,
    load_manifest,
)
from tests.utils.helpers._feature_verification.processes import fetch_journal, load_state
from tests.utils.helpers._feature_verification.redaction import redact, secret_values
from tests.utils.helpers._fresh_host.models import FreshHostContext
from tests.utils.helpers._fresh_host.storage import load_context

_HARNESS_ENV_PREFIXES: Final[tuple[str, ...]] = ("FEATURE_VERIFICATION_", "PYTEST_")
_FRAMEWORK_ENV_KEYS: Final[frozenset[str]] = frozenset({"TEST_ID", "RESOURCE_PREFIX", "WORKER_ID"})
_JSON_START: Final[re.Pattern[str]] = re.compile(r"(?m)^[ \t]*[\[{]")
_OUTPUT_TAIL_CHARS: Final[int] = 3000
_GATEWAY_ADMIN_CALL_SCRIPT: Final[Path] = Path(__file__).with_name("gateway_admin_call.mjs")


def default_manifest_path(repo_root: Path) -> Path:
    """Return the checked-in feature matrix path."""
    return repo_root / "tests" / "feature_matrix.yaml"


def extract_json(text: str) -> object:
    """Return the first JSON document embedded in mixed command output."""
    decoder = json.JSONDecoder()
    for match in _JSON_START.finditer(text):
        try:
            payload, _ = decoder.raw_decode(text[match.start() :].lstrip())
        except json.JSONDecodeError:
            continue
        return cast(object, payload)
    raise AssertionError(f"no JSON document in command output: {text[-_OUTPUT_TAIL_CHARS:]!r}")


def reply_texts(payload: object) -> list[str]:
    """Collect every ``payloads[].text`` from an ``openclaw agent --json`` envelope."""
    texts: list[str] = []
    if isinstance(payload, dict):
        mapping = cast(dict[str, object], payload)
        for key, value in mapping.items():
            if key == "payloads" and isinstance(value, list):
                texts.extend(
                    str(cast(dict[str, object], item).get("text"))
                    for item in cast(list[object], value)
                    if isinstance(item, dict) and cast(dict[str, object], item).get("text")
                )
            else:
                texts.extend(reply_texts(value))
    elif isinstance(payload, list):
        for item in cast(list[object], payload):
            texts.extend(reply_texts(item))
    return texts


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """Captured result of one product command."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float

    @property
    def ok(self) -> bool:
        """Return whether the command exited 0."""
        return self.returncode == 0

    @property
    def output(self) -> str:
        """Return stdout and stderr combined."""
        return f"{self.stdout}\n{self.stderr}"

    def describe(self) -> str:
        """Return a bounded, already-redacted description for assertion messages."""
        return (
            f"`{' '.join(self.argv)}` exited {self.returncode} after {self.duration_seconds:.1f}s\n"
            f"stdout tail:\n{self.stdout[-_OUTPUT_TAIL_CHARS:]}\n"
            f"stderr tail:\n{self.stderr[-_OUTPUT_TAIL_CHARS:]}"
        )

    def require_ok(self) -> CommandOutcome:
        """Assert a zero exit status."""
        assert self.ok, self.describe()
        return self

    def json(self) -> object:
        """Parse the first JSON document from stdout."""
        return extract_json(self.stdout)


@dataclass(slots=True)
class FeatureHost:
    """The provisioned host one feature-verification lane runs against."""

    lane: Lane
    manifest: FeatureManifest = field(repr=False)
    context: FreshHostContext = field(repr=False)
    work_dir: Path
    fake_model_dir: Path | None
    _secrets: tuple[str, ...] = field(default_factory=tuple, repr=False)

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> FeatureHost:
        """Build the host from the lane environment exported by ``run-lane``."""
        env = os.environ if environ is None else environ
        lane_id = env.get(LANE_ENV, "").strip()
        context_file = env.get(CONTEXT_ENV, "").strip()
        if not lane_id or not context_file:
            raise FeatureVerificationError(f"{LANE_ENV} and {CONTEXT_ENV} must both be set")
        context = load_context(Path(context_file))
        manifest_path = Path(
            env.get(MANIFEST_ENV, "").strip()
            or default_manifest_path(Path(context.repo_root)).as_posix()
        )
        manifest = load_manifest(manifest_path)
        if lane_id not in manifest.lanes:
            raise FeatureVerificationError(f"lane {lane_id!r} is not in {manifest_path}")
        work_dir = Path(env.get(WORK_DIR_ENV, "").strip() or Path(context.tmp_root) / "featver")
        work_dir.mkdir(parents=True, exist_ok=True)
        fake_model_raw = env.get(FAKE_MODEL_DIR_ENV, "").strip()
        return cls(
            lane=manifest.lanes[lane_id],
            manifest=manifest,
            context=context,
            work_dir=work_dir,
            fake_model_dir=Path(fake_model_raw) if fake_model_raw else None,
        )

    @property
    def repo_root(self) -> Path:
        """Return the StrongClaw checkout under test."""
        return Path(self.context.repo_root)

    @property
    def home(self) -> Path:
        """Return the HOME the product was installed into."""
        return Path(self.context.app_home)

    @property
    def python(self) -> Path:
        """Return the managed-environment interpreter created by bootstrap."""
        return self.repo_root / ".venv" / "bin" / "python"

    def scratch(self, name: str) -> Path:
        """Return a fresh per-test scratch directory."""
        path = self.work_dir / f"{name}-{time.time_ns()}"
        path.mkdir(parents=True)
        return path

    def env_file(self) -> Path:
        """Return the Varlock local env file the operator edits."""
        return varlock_local_env_file(self.repo_root)

    def env_dir(self) -> Path:
        """Return the Varlock env directory."""
        return varlock_env_dir(self.repo_root)

    def env_values(self) -> dict[str, str]:
        """Return the operator env contract assignments."""
        return load_env_assignments(self.env_file())

    def _refresh_secrets(self) -> None:
        self._secrets = secret_values(self.env_file())

    def redact(self, text: str) -> str:
        """Replace known secret values in *text*."""
        return redact(text, self._secrets)

    def product_env(self, overrides: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return the operator environment with harness variables removed."""
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in _FRAMEWORK_ENV_KEYS and not key.startswith(_HARNESS_ENV_PREFIXES)
        }
        if overrides:
            env.update(overrides)
        return env

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float = 300,
        env: Mapping[str, str] | None = None,
        input_text: str | None = None,
    ) -> CommandOutcome:
        """Run one command and capture its redacted output."""
        self._refresh_secrets()
        started = time.monotonic()
        command = tuple(str(part) for part in argv)
        try:
            completed = subprocess.run(  # noqa: S603 - argv built by the harness
                command,
                cwd=cwd or self.repo_root,
                env=self.product_env(env),
                input=input_text,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            return CommandOutcome(
                argv=command,
                returncode=-9,
                stdout=self.redact(stdout),
                stderr=self.redact(f"{stderr}\n[harness] timed out after {timeout}s"),
                duration_seconds=time.monotonic() - started,
            )
        return CommandOutcome(
            argv=command,
            returncode=completed.returncode,
            stdout=self.redact(completed.stdout),
            stderr=self.redact(completed.stderr),
            duration_seconds=time.monotonic() - started,
        )

    def clawops(
        self,
        *arguments: str,
        cwd: Path | None = None,
        timeout: float = 300,
        env: Mapping[str, str] | None = None,
    ) -> CommandOutcome:
        """Run the managed ``clawops`` CLI."""
        return self.run(
            [str(self.python), "-m", "clawops", *arguments], cwd=cwd, timeout=timeout, env=env
        )

    def openclaw(
        self,
        *arguments: str,
        cwd: Path | None = None,
        timeout: float = 300,
        env: Mapping[str, str] | None = None,
    ) -> CommandOutcome:
        """Run ``openclaw`` exactly as StrongClaw does: inside ``varlock run``."""
        argv = wrap_command_with_varlock(self.repo_root, ["openclaw", *arguments])
        return self.run(argv, cwd=cwd, timeout=timeout, env=env)

    def gateway_admin_call(
        self, method: str, params: Mapping[str, object], *, timeout: float = 120
    ) -> CommandOutcome:
        """Call one Gateway method as an operator.admin client, inside ``varlock run``.

        The client keeps its own device identity under the lane's work dir, so it pairs as a
        separate operator device on its first loopback connect, the way an operator's console
        does, instead of asking to upgrade the CLI device's scopes.
        """
        identity_dir = self.work_dir / "gateway-admin-client"
        identity_dir.mkdir(parents=True, exist_ok=True)
        openclaw_package = strongclaw_node_tools_dir(home_dir=self.home) / "node_modules/openclaw"
        argv = wrap_command_with_varlock(
            self.repo_root,
            [
                "node",
                str(_GATEWAY_ADMIN_CALL_SCRIPT),
                str(openclaw_package),
                method,
                json.dumps(dict(params)),
                str(int(timeout * 1000)),
            ],
        )
        overrides = {
            "OPENCLAW_STATE_DIR": str(identity_dir),
            "OPENCLAW_CONFIG_PATH": str(resolve_openclaw_config_path(self.repo_root)),
        }
        return self.run(argv, timeout=timeout + 30, env=overrides)

    def clawops_managed(
        self,
        *arguments: str,
        cwd: Path | None = None,
        timeout: float = 300,
        env: Mapping[str, str] | None = None,
    ) -> CommandOutcome:
        """Run ``clawops`` inside ``varlock run``, as StrongClaw's own runtime callers do."""
        argv = wrap_command_with_varlock(
            self.repo_root, [str(self.python), "-m", "clawops", *arguments]
        )
        return self.run(argv, cwd=cwd, timeout=timeout, env=env)

    def fake_model_requests(self) -> list[dict[str, object]]:
        """Return every request the fake model server has answered so far."""
        if self.fake_model_dir is None:
            raise FeatureVerificationError(f"{FAKE_MODEL_DIR_ENV} is not set for this lane")
        state = load_state(self.fake_model_dir)
        ports = state.get("ports")
        if not isinstance(ports, list) or not ports:
            raise FeatureVerificationError("fake model state has no ports")
        return fetch_journal(str(state.get("host", "127.0.0.1")), int(cast(int, ports[0])))

    def rendered_config(self) -> dict[str, object]:
        """Return the rendered OpenClaw config."""
        return dict(load_openclaw_config(resolve_openclaw_config_path(self.repo_root)))
