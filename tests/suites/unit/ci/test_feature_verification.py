"""Unit coverage for the feature-verification harness and its fresh-host scenarios."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers import fresh_host
from tests.utils.helpers._fresh_host import linux as fresh_host_linux
from tests.utils.helpers.feature_verification import (
    ACP_STUB_REPLY_PREFIX,
    REPLY_PREFIX,
    FeatureResult,
    FeatureVerificationError,
    LaneResults,
    build_report,
    canned_reply,
    deterministic_embedding,
    extract_json,
    load_lane_results,
    load_manifest,
    register_provider,
    render_markdown,
)
from tests.utils.helpers.repo import REPO_ROOT

_STUB_AGENT = (
    REPO_ROOT / "tests" / "utils" / "helpers" / "_feature_verification" / "acp_stub_agent.py"
)
_MANIFEST_HEADER = """\
schema_version: 1
lanes:
  lane-a: {platform: linux, profile: hypermemory, scenario: linux-features, activate_services: true, description: a}
  lane-b: {platform: macos, profile: openclaw-default, scenario: macos-features, activate_services: true, description: b}
features:
"""
_VALID_FEATURES = """\
  - id: alpha.one
    category: cat-a
    title: Alpha one
    call_sites: [src/clawops/cli.py:1]
    lanes: [lane-a, lane-b]
    verification: live
    test: tests/suites/e2e/feature_verification/test_x.py::test_alpha
  - id: beta.two
    category: cat-b
    title: Beta two
    call_sites: [src/clawops/cli.py:2]
    status: unverifiable
    reason: Needs a third-party account that CI does not have.
"""


def _write_manifest(tmp_path: Path, features: str = _VALID_FEATURES) -> Path:
    path = tmp_path / "feature_matrix.yaml"
    path.write_text(_MANIFEST_HEADER + features, encoding="utf-8")
    return path


def test_canned_reply_echoes_tokens_from_the_latest_user_turn() -> None:
    assert canned_reply("ctx", "please answer FV-abc123 now") == f"{REPLY_PREFIX} FV-abc123"
    assert canned_reply("ctx", "no token here") == f"{REPLY_PREFIX} acknowledged"


def test_canned_reply_answers_reflection_and_extraction_prompts() -> None:
    reflection = canned_reply("## Invariants\n## Derived\nMEMORY REFLECTION", "")
    assert "FEATVER_REFLECTION_INVARIANT" in reflection and "## Derived" in reflection
    extraction = canned_reply("Analyze ... extract memories worth long-term preservation.", "")
    assert json.loads(extraction) == {"memories": []}


def test_deterministic_embedding_is_stable_and_unit_length() -> None:
    first = deterministic_embedding("alpha", 64)
    assert first == deterministic_embedding("alpha", 64)
    assert first != deterministic_embedding("beta", 64)
    assert len(first) == 64
    assert abs(sum(value * value for value in first) - 1.0) < 1e-3


@pytest.mark.network_local
def test_fake_model_server_serves_openai_and_ollama_shapes() -> None:
    import urllib.request

    from tests.utils.helpers.feature_verification import FakeModelServer

    server = FakeModelServer(host="127.0.0.1", ports=[0])
    server.start()
    base = f"http://127.0.0.1:{server.ports[0]}"
    try:

        def _post(path: str, body: object) -> bytes:
            request = urllib.request.Request(
                f"{base}{path}", data=json.dumps(body).encode(), method="POST"
            )
            with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
                return cast(bytes, response.read())

        chat = json.loads(
            _post("/v1/chat/completions", {"messages": [{"role": "user", "content": "FV-1234"}]})
        )
        assert chat["choices"][0]["message"]["content"] == f"{REPLY_PREFIX} FV-1234"
        stream = _post(
            "/v1/chat/completions",
            {"stream": True, "messages": [{"role": "user", "content": "FV-5678"}]},
        ).decode()
        assert f"{REPLY_PREFIX} FV-5678" in stream and stream.rstrip().endswith("data: [DONE]")
        embeddings = json.loads(_post("/v1/embeddings", {"input": ["a", "b"], "dimensions": 8}))
        assert [len(item["embedding"]) for item in embeddings["data"]] == [8, 8]
        ollama = json.loads(_post("/api/embed", {"input": ["a b", "c"], "dimensions": 4}))
        assert [len(vector) for vector in ollama["embeddings"]] == [4, 4]
        assert ollama["prompt_eval_count"] == 3
        with urllib.request.urlopen(
            f"{base}/__featver/requests", timeout=5
        ) as response:  # noqa: S310
            journal = json.loads(response.read())["requests"]
        assert [entry["path"] for entry in journal] == [
            "/v1/chat/completions",
            "/v1/chat/completions",
            "/v1/embeddings",
            "/api/embed",
        ]
    finally:
        server.stop()


def test_acp_stub_agent_speaks_acp_and_reports_env_presence_only() -> None:
    messages: list[dict[str, object]] = [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": 1}},
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "authenticate",
            "params": {"methodId": "openai-api-key"},
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/new",
            "params": {"cwd": "/", "mcpServers": []},
        },
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "session/prompt",
            "params": {"sessionId": "s", "prompt": [{"type": "text", "text": "FV-stub"}]},
        },
    ]
    completed = subprocess.run(
        [sys.executable, str(_STUB_AGENT), "--persona", "codex"],
        input="\n".join(json.dumps(message) for message in messages) + "\n",
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
        env={"PATH": "/usr/bin:/bin", "OPENAI_API_KEY": "secret-value-never-echoed"},
    )
    replies = [json.loads(line) for line in completed.stdout.splitlines()]
    methods = [method["id"] for method in replies[0]["result"]["authMethods"]]
    assert methods == ["chatgpt", "codex-api-key", "openai-api-key"]
    update = next(reply for reply in replies if reply.get("method") == "session/update")
    text = update["params"]["update"]["content"]["text"]
    assert text.startswith(ACP_STUB_REPLY_PREFIX)
    assert "auth=openai-api-key" in text and "OPENAI_API_KEY:present" in text
    assert "secret-value-never-echoed" not in completed.stdout
    assert replies[-1] == {"jsonrpc": "2.0", "id": 3, "result": {"stopReason": "end_turn"}}


def test_extract_json_skips_leading_noise() -> None:
    assert extract_json('warning: banner\n{"ok": true}\ntrailer') == {"ok": True}
    with pytest.raises(AssertionError):
        extract_json("no json at all")


def test_register_provider_stores_the_api_key_as_an_env_secret_ref(tmp_path: Path) -> None:
    config = tmp_path / "openclaw.json"
    config.write_text(json.dumps({"models": {"providers": {"other": {"api": "x"}}}}), "utf-8")

    register_provider(config)

    providers = json.loads(config.read_text(encoding="utf-8"))["models"]["providers"]
    assert providers["other"] == {"api": "x"}
    assert providers["featver"]["apiKey"] == {
        "source": "env",
        "provider": "default",
        "id": "FEATVER_PROVIDER_API_KEY",
    }


def test_load_manifest_accepts_valid_matrix_and_derives_platforms(tmp_path: Path) -> None:
    manifest = load_manifest(_write_manifest(tmp_path))
    alpha = manifest.feature("alpha.one")
    assert manifest.platforms(alpha) == ("linux", "macos")
    assert manifest.features_for_lane("lane-b") == (alpha,)
    assert manifest.feature("beta.two").status == "unverifiable"


@pytest.mark.parametrize(
    ("features", "message"),
    [
        (_VALID_FEATURES.replace("lanes: [lane-a, lane-b]", "lanes: [lane-z]"), "unknown lanes"),
        (
            _VALID_FEATURES.replace(
                "    reason: Needs",
                "    test: tests/suites/e2e/feature_verification/test_x.py::test_beta\n    reason: Needs",
            ),
            "unverifiable",
        ),
        (_VALID_FEATURES.replace("verification: live", "verification: maybe"), "verification"),
        (_VALID_FEATURES.replace("src/clawops/cli.py:1", "src/clawops/cli.py"), "file:line"),
        (_VALID_FEATURES.replace("id: beta.two", "id: alpha.one"), "duplicate"),
    ],
)
def test_load_manifest_rejects_invalid_entries(tmp_path: Path, features: str, message: str) -> None:
    with pytest.raises(FeatureVerificationError, match=message):
        load_manifest(_write_manifest(tmp_path, features))


def _lane(lane: str, platform: str, outcome: str, message: str = "") -> LaneResults:
    return LaneResults(
        lane=lane,
        platform=platform,
        profile="p",
        started_at="t0",
        versions={"openclaw": "OpenClaw 2026.3.13", "acpx": "0.3.0"},
        results=[
            FeatureResult(
                feature="alpha.one",
                test="tests/suites/e2e/feature_verification/test_x.py::test_alpha",
                outcome=outcome,  # type: ignore[arg-type]
                duration_seconds=1.0,
                message=message,
            )
        ],
    )


def test_report_requires_every_lane_to_pass(tmp_path: Path) -> None:
    manifest = load_manifest(_write_manifest(tmp_path))
    passing = build_report(
        manifest,
        {
            "lane-a": _lane("lane-a", "linux", "passed"),
            "lane-b": _lane("lane-b", "macos", "passed"),
        },
    )
    assert passing.counts == {"PASS": 1, "FAIL": 0, "UNVERIFIABLE": 1}
    one_lane_missing = build_report(manifest, {"lane-a": _lane("lane-a", "linux", "passed")})
    assert one_lane_missing.counts == {"PASS": 0, "FAIL": 1, "UNVERIFIABLE": 1}
    failing = build_report(
        manifest,
        {
            "lane-a": _lane("lane-a", "linux", "failed", "boom | pipe"),
            "lane-b": _lane("lane-b", "macos", "passed"),
        },
    )
    markdown = render_markdown(failing)
    assert "| `alpha.one` | cat-a | FAIL |" in markdown and "boom \\| pipe" in markdown
    assert "OpenClaw 2026.3.13" in markdown


def test_report_maps_unverifiable_skips_and_plain_skips(tmp_path: Path) -> None:
    manifest = load_manifest(_write_manifest(tmp_path))
    lanes = {
        "lane-a": _lane("lane-a", "linux", "skipped", "unverifiable: provider outage"),
        "lane-b": _lane("lane-b", "macos", "skipped", "unverifiable: provider outage"),
    }
    assert build_report(manifest, lanes).counts == {"PASS": 0, "FAIL": 0, "UNVERIFIABLE": 2}
    lanes["lane-b"] = _lane("lane-b", "macos", "skipped", "some other skip")
    assert build_report(manifest, lanes).counts["FAIL"] == 1


def test_lane_results_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "feature-results-lane-a.json"
    _lane("lane-a", "linux", "error", "teardown").write(path)
    loaded = load_lane_results(path)
    assert loaded.results[0].verdict == "FAIL" and loaded.versions["acpx"] == "0.3.0"


def test_feature_scenarios_install_into_the_runner_home(
    tmp_path: Path, test_context: TestContext
) -> None:
    test_context.apply_profiles("fresh_host_push")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = fresh_host.prepare_context(
        scenario_id="linux-features",
        profile="hypermemory",
        repo_root=workspace,
        runner_temp=tmp_path / "runner-temp",
        workspace=workspace,
        github_env_file=None,
    )
    assert Path(context.app_home) == Path.home()
    assert context.xdg_runtime_dir is not None and context.xdg_runtime_dir.startswith("/run/user/")
    reloaded = fresh_host.load_context(Path(context.context_path))
    assert reloaded.use_host_home is True


def test_legacy_context_files_default_the_new_fields(
    tmp_path: Path, test_context: TestContext
) -> None:
    test_context.apply_profiles("fresh_host_push")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = fresh_host.prepare_context(
        scenario_id="linux",
        repo_root=workspace,
        runner_temp=tmp_path / "runner-temp",
        workspace=workspace,
        github_env_file=None,
    )
    payload = json.loads(Path(context.context_path).read_text(encoding="utf-8"))
    del payload["use_host_home"]
    Path(context.context_path).write_text(json.dumps(payload), encoding="utf-8")
    reloaded = fresh_host.load_context(Path(context.context_path))
    assert reloaded.use_host_home is False
    assert Path(reloaded.app_home) == Path(context.tmp_root) / "home"


@pytest.mark.parametrize(
    ("scenario_id", "expects_flag"), [("linux", True), ("linux-features", False)]
)
def test_linux_setup_only_skips_activation_when_the_scenario_says_so(
    tmp_path: Path, test_context: TestContext, scenario_id: str, expects_flag: bool
) -> None:
    test_context.apply_profiles("fresh_host_push")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = fresh_host.prepare_context(
        scenario_id=scenario_id,
        repo_root=workspace,
        runner_temp=tmp_path / "runner-temp",
        workspace=workspace,
        github_env_file=None,
    )
    calls: list[list[str]] = []

    def _record(command: list[str], **_: object) -> None:
        calls.append(command)

    test_context.patch.patch_object(fresh_host_linux, "run_command", new=_record)
    command = fresh_host_linux.linux_setup(context)
    assert calls == [command]
    assert ("--no-activate-services" in command) is expects_flag
