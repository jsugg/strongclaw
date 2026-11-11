"""Contract checks for shipped sidecar compose pins and the OTel redaction pipeline."""

from __future__ import annotations

import re
from typing import Any, cast

import yaml

from tests.utils.helpers.repo import REPO_ROOT

_COMPOSE_DIR = REPO_ROOT / "platform" / "compose"
_COLLECTOR_CONFIG = REPO_ROOT / "platform" / "configs" / "otel" / "collector.yaml"
# Dependabot's docker-compose scan does not pick up these variants, so pins can drift.
_VARIANT_BASES: tuple[tuple[str, str], ...] = (
    ("docker-compose.aux-stack.ci-hosted-macos.yaml", "docker-compose.aux-stack.yaml"),
    ("docker-compose.browser-lab.ci-hosted-macos.yaml", "docker-compose.browser-lab.yaml"),
)
_CONTEXT_PATH = re.compile(r"replace_all_patterns\((?P<path>[a-z_.]+),")


def _yaml(path_name: str) -> dict[str, Any]:
    payload = yaml.safe_load((_COMPOSE_DIR / path_name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return cast(dict[str, Any], payload)


def _service_images(path_name: str) -> dict[str, str]:
    services = cast(dict[str, dict[str, Any]], _yaml(path_name)["services"])
    return {name: str(spec["image"]) for name, spec in services.items() if "image" in spec}


def test_hosted_macos_compose_variants_pin_the_same_images_as_their_base() -> None:
    for variant, base in _VARIANT_BASES:
        base_images = _service_images(base)
        for service, image in _service_images(variant).items():
            if service in base_images:
                assert image == base_images[service], (variant, service)


def test_otel_collector_keeps_secret_redaction_on_the_trace_pipeline() -> None:
    config = cast(dict[str, Any], yaml.safe_load(_COLLECTOR_CONFIG.read_text(encoding="utf-8")))
    traces = cast(dict[str, list[str]], config["service"]["pipelines"]["traces"])
    processors = traces["processors"]

    assert processors.index("attributes/scrub") < processors.index("batch")
    assert processors.index("transform/redact") < processors.index("batch")
    assert "otlp_http/langfuse" in traces["exporters"]
    assert not any(key.startswith("otlphttp") for key in config["exporters"])

    transform = cast(dict[str, Any], config["processors"]["transform/redact"])
    for group_key, context in (("trace_statements", "span"), ("log_statements", "log")):
        for group in cast(list[dict[str, Any]], transform[group_key]):
            assert group["context"] == context
            for statement in cast(list[str], group["statements"]):
                match = _CONTEXT_PATH.search(statement)
                assert match is not None, statement
                assert match["path"] == f"{context}.attributes", statement


def test_browser_lab_playwright_service_serves_cdp_on_its_published_port() -> None:
    worker_dir = REPO_ROOT / "platform" / "workers" / "browser-lab"
    launcher = worker_dir / "start-cdp.sh"
    script = launcher.read_text(encoding="utf-8")
    assert "--remote-debugging-port=9222" in script
    assert "--remote-debugging-address=0.0.0.0" in script
    assert "--proxy-server=" in script
    for path_name in (
        "docker-compose.browser-lab.yaml",
        "docker-compose.browser-lab.ci-hosted-macos.yaml",
    ):
        services = cast(dict[str, dict[str, Any]], _yaml(path_name)["services"])
        playwright = services["browserlab-playwright"]
        assert playwright["command"] == ["bash", "/workspace/browser-lab/start-cdp.sh"], path_name
        assert "127.0.0.1:9222:9222" in playwright["ports"], path_name
        assert (
            "../workers/browser-lab:/workspace/browser-lab:ro" in playwright["volumes"]
        ), path_name
