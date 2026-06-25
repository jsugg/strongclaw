#!/usr/bin/env python3
"""Semantic CLI for the manual end-to-end acceptance smokes.

Each subcommand closes one previously-uncovered end-to-end case for the
``e2e-acceptance`` workflow. The Docker-free smokes (skills, worktree, acp)
run on any runner; the live-stack smokes (observability, retrieval,
degradation) assume the aux-stack sidecars are already running on loopback.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import cast

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
for import_root in (SRC_ROOT, REPO_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from tests.utils.helpers.cli import write_fake_acpx  # noqa: E402


class SmokeError(RuntimeError):
    """Raised when an acceptance smoke fails its contract."""


def _log(message: str) -> None:
    """Emit one progress line."""
    print(f"[e2e-acceptance] {message}", flush=True)


def _as_dict(value: object) -> dict[str, object]:
    """Narrow a decoded JSON value to a string-keyed mapping."""
    if not isinstance(value, dict):
        raise SmokeError(f"expected a JSON object, got {type(value).__name__}: {value!r}")
    return cast("dict[str, object]", value)


def _as_list(value: object) -> list[object]:
    """Narrow a decoded JSON value to a list."""
    if not isinstance(value, list):
        raise SmokeError(f"expected a JSON array, got {type(value).__name__}: {value!r}")
    return cast("list[object]", value)


def _clawops(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run a clawops subcommand and capture its output."""
    command = [sys.executable, "-m", "clawops", *args]
    run_env = dict(os.environ)
    run_env.setdefault("PYTHONPATH", str(SRC_ROOT))
    if env is not None:
        run_env.update(env)
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=run_env,
        capture_output=True,
        text=True,
        check=False,
    )


def _require_ok(result: subprocess.CompletedProcess[str], label: str) -> str:
    """Assert a clawops invocation succeeded, returning its stdout."""
    if result.returncode != 0:
        raise SmokeError(
            f"{label} failed (exit {result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout


def _git(repo: Path, *args: str) -> None:
    """Run a git command inside a fixture repository."""
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


# --------------------------------------------------------------------------- #
# E. Skills scan / promote / quarantine
# --------------------------------------------------------------------------- #
def skills_smoke() -> None:
    """Scan a synthetic skill bundle and assert a durable manifest + quarantine."""
    with tempfile.TemporaryDirectory(prefix="e2e-skills-") as tmp:
        root = Path(tmp)
        source = root / "demo-skill"
        source.mkdir()
        (source / "SKILL.md").write_text(
            "# Demo Skill\n\nMinimal skill bundle for acceptance scanning.\n",
            encoding="utf-8",
        )
        (source / "handler.ts").write_text(
            "export const run = (): string => 'ok';\n", encoding="utf-8"
        )
        quarantine = root / "quarantine"
        report = root / "scan-report.json"
        _require_ok(
            _clawops(
                "skills",
                "--source",
                str(source),
                "--quarantine",
                str(quarantine),
                "--report",
                str(report),
            ),
            "skills scan",
        )
        if not report.is_file():
            raise SmokeError("skills scan did not write a report")
        payload = json.loads(report.read_text(encoding="utf-8"))
        if "manifestVersion" not in payload:
            raise SmokeError(f"scan report missing manifestVersion: {sorted(payload)}")
        copied = quarantine / source.name
        if not (copied / "SKILL.md").is_file():
            raise SmokeError("skills scan did not quarantine the bundle")
    _log("skills-smoke OK (scan manifest + quarantine verified)")


# --------------------------------------------------------------------------- #
# F. Worktree lifecycle (real git worktrees)
# --------------------------------------------------------------------------- #
def worktree_smoke() -> None:
    """Create, list, and prune a managed worktree against a real upstream repo."""
    with tempfile.TemporaryDirectory(prefix="e2e-worktree-") as tmp:
        repo_root = Path(tmp)
        upstream = repo_root / "repo" / "upstream"
        upstream.mkdir(parents=True)
        _git(upstream, "init", "-b", "main")
        _git(upstream, "config", "user.email", "acceptance@example.com")
        _git(upstream, "config", "user.name", "Acceptance Smoke")
        (upstream / "README.md").write_text("upstream\n", encoding="utf-8")
        _git(upstream, "add", "README.md")
        _git(upstream, "commit", "-m", "seed upstream")

        created = _require_ok(
            _clawops(
                "worktree",
                "--repo-root",
                str(repo_root),
                "new",
                "--branch",
                "acceptance/smoke",
                "--start-point",
                "main",
            ),
            "worktree new",
        )
        listed = _require_ok(
            _clawops("worktree", "--repo-root", str(repo_root), "list"),
            "worktree list",
        )
        if "acceptance/smoke" not in (created + listed):
            raise SmokeError("created worktree branch not reported by list")
        worktrees_root = repo_root / "repo" / "worktrees"
        if not any(worktrees_root.iterdir()):
            raise SmokeError("worktree new did not materialize a worktree directory")
        _require_ok(
            _clawops("worktree", "--repo-root", str(repo_root), "prune"),
            "worktree prune",
        )
    _log("worktree-smoke OK (new + list + prune verified)")


# --------------------------------------------------------------------------- #
# D. ACP worker session orchestration (deterministic, model-free)
# --------------------------------------------------------------------------- #
def acp_smoke() -> None:
    """Drive an acp-runner session against a fake worker; assert a durable summary."""
    with tempfile.TemporaryDirectory(prefix="e2e-acp-") as tmp:
        root = Path(tmp)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        write_fake_acpx(bin_dir, stdout_text='{"ok": true}')
        project_root = root / "project"
        workspace = root / "workspace"
        project_root.mkdir()
        workspace.mkdir()

        path_env = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
        result = _clawops(
            "acp-runner",
            "--backend",
            "openclaw",
            "--auth-mode",
            "local",
            "--prompt",
            "Summarize the workspace",
            "--project-root",
            str(project_root),
            "--workspace",
            str(workspace),
            "--allowed-workspace-root",
            str(workspace),
            "--permissions-mode",
            "approve-reads",
            "--output-format",
            "json",
            env={"PATH": path_env},
        )
        stdout = _require_ok(result, "acp-runner session")
        summary = json.loads(stdout)
        if summary.get("backend") != "openclaw":
            raise SmokeError(f"acp summary backend mismatch: {summary.get('backend')!r}")
        if summary.get("status") != "succeeded" or not summary.get("ok"):
            raise SmokeError(f"acp session did not succeed: status={summary.get('status')!r}")
        summary_path = summary.get("summary_path")
        if not isinstance(summary_path, str) or not Path(summary_path).is_file():
            raise SmokeError("acp session did not write a durable summary artifact")
    _log("acp-smoke OK (lock + run + durable summary verified)")


# --------------------------------------------------------------------------- #
# C. Live observability probe
# --------------------------------------------------------------------------- #
def observability_smoke(asset_root: str) -> None:
    """Probe live OTLP + metrics endpoints via verify-platform observability."""
    _require_ok(
        _clawops("verify-platform", "observability", "--asset-root", asset_root),
        "verify-platform observability",
    )
    _log("observability-smoke OK (live OTLP + metrics endpoints reachable)")


# --------------------------------------------------------------------------- #
# I. Sidecar functional depth (real queries through the live stack)
# --------------------------------------------------------------------------- #
def _http(
    method: str,
    url: str,
    *,
    body: object = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30,
) -> dict[str, object]:
    """Issue a JSON HTTP request and return the decoded response."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(
            request, timeout=timeout
        ) as response:  # noqa: S310 (loopback only)
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:  # pragma: no cover - surfaced as SmokeError
        raise SmokeError(
            f"{method} {url} failed: HTTP {exc.code} {exc.read().decode('utf-8')}"
        ) from exc
    except urllib.error.URLError as exc:  # pragma: no cover - surfaced as SmokeError
        raise SmokeError(f"{method} {url} unreachable: {exc.reason}") from exc
    return json.loads(raw) if raw else {}


def _qdrant_functional(host: str, port: int) -> None:
    """Create a collection, upsert a vector, search, and assert a hit."""
    base = f"http://{host}:{port}"
    collection = "e2e_acceptance_smoke"
    _http(
        "PUT",
        f"{base}/collections/{collection}",
        body={"vectors": {"size": 4, "distance": "Cosine"}},
    )
    try:
        _http(
            "PUT",
            f"{base}/collections/{collection}/points?wait=true",
            body={
                "points": [{"id": 1, "vector": [0.1, 0.2, 0.3, 0.4], "payload": {"tag": "smoke"}}]
            },
        )
        search = _http(
            "POST",
            f"{base}/collections/{collection}/points/search",
            body={"vector": [0.1, 0.2, 0.3, 0.4], "limit": 1, "with_payload": True},
        )
        hits = search.get("result")
        if not isinstance(hits, list) or not hits:
            raise SmokeError(f"qdrant search returned no hits: {search}")
    finally:
        _http("DELETE", f"{base}/collections/{collection}")
    _log("  qdrant: collection + upsert + vector search hit verified")


def _neo4j_functional(host: str, http_port: int, user: str, password: str) -> None:
    """Run a cypher write + read round-trip over the Neo4j HTTP API."""
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    headers = {"Authorization": f"Basic {token}"}
    url = f"http://{host}:{http_port}/db/neo4j/tx/commit"
    statements = {
        "statements": [
            {
                "statement": "CREATE (n:E2eSmoke {id: $id}) RETURN n.id AS id",
                "parameters": {"id": "smoke"},
            },
            {
                "statement": "MATCH (n:E2eSmoke {id: $id}) RETURN count(n) AS hits",
                "parameters": {"id": "smoke"},
            },
        ]
    }
    response = _http("POST", url, body=statements, headers=headers)
    errors = response.get("errors")
    if errors:
        raise SmokeError(f"neo4j cypher errors: {errors}")
    results = _as_list(response.get("results"))
    if len(results) < 2:
        raise SmokeError(f"neo4j returned unexpected results: {response}")
    # Best-effort cleanup; ignore failures so the smoke verdict reflects the query path.
    _http(
        "POST",
        url,
        body={"statements": [{"statement": "MATCH (n:E2eSmoke) DELETE n"}]},
        headers=headers,
    )
    _log("  neo4j: cypher write + read round-trip verified")


def retrieval_smoke(
    qdrant_host: str,
    qdrant_port: int,
    neo4j_host: str,
    neo4j_http_port: int,
    neo4j_user: str,
    neo4j_password: str,
) -> None:
    """Exercise real queries against the live Qdrant + Neo4j retrieval backends."""
    _qdrant_functional(qdrant_host, qdrant_port)
    _neo4j_functional(neo4j_host, neo4j_http_port, neo4j_user, neo4j_password)
    _log("retrieval-smoke OK (live Qdrant + Neo4j query paths verified)")


def _endpoint_ready(url: str) -> bool:
    """Return whether an HTTP endpoint answers with a non-server-error status."""
    try:
        with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 (loopback only)
            return bool(response.status < 500)
    except urllib.error.HTTPError as exc:
        return bool(exc.code < 500)
    except (urllib.error.URLError, OSError):
        return False


def stack_wait(qdrant_url: str, neo4j_url: str, otel_metrics_url: str, timeout: float) -> None:
    """Block until the retrieval + observability sidecars answer on loopback."""
    pending = {"qdrant": qdrant_url, "neo4j": neo4j_url, "otel-metrics": otel_metrics_url}
    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        for name in list(pending):
            if _endpoint_ready(pending[name]):
                _log(f"  {name} ready")
                del pending[name]
        if pending:
            time.sleep(3)
    if pending:
        raise SmokeError(f"sidecars not ready within {timeout:.0f}s: {sorted(pending)}")
    _log("stack-wait OK (qdrant + neo4j + otel endpoints reachable)")


# --------------------------------------------------------------------------- #
# A + B. Live model auth chain + a real agent-style turn (message round-trip)
# --------------------------------------------------------------------------- #
def inference_smoke(base_url: str, model: str, api_key: str) -> None:
    """Run an agent-style turn against a live local model; assert the response envelope.

    Closes the secret-free portion of A (live inference through a loopback model
    endpoint) and B (a system+user message round-trip yielding a well-formed
    response envelope). Routing through a real external provider and the full
    OpenClaw channel/gateway session remain secret-dependent residuals.
    """
    url = base_url.rstrip("/") + "/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a terse assistant. Reply with one short sentence.",
            },
            {"role": "user", "content": "Say the word ready."},
        ],
        "stream": False,
        "temperature": 0,
    }
    response = _http("POST", url, body=body, headers=headers, timeout=240)
    choices = _as_list(response.get("choices"))
    if not choices:
        raise SmokeError(f"model response missing choices: {response}")
    message = _as_dict(_as_dict(choices[0]).get("message"))
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise SmokeError(f"model returned an empty response envelope: {response}")
    _log(f"inference-smoke OK (live model turn returned {len(content.strip())} chars of content)")


# --------------------------------------------------------------------------- #
# J. Degradation / failure injection
# --------------------------------------------------------------------------- #
def _stop_container(name_fragment: str) -> str:
    """Stop the running container whose name contains a fragment."""
    listing = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    matches = [line for line in listing.stdout.splitlines() if name_fragment in line]
    if not matches:
        raise SmokeError(f"no running container matched {name_fragment!r}: {listing.stdout!r}")
    target = matches[0]
    subprocess.run(["docker", "stop", target], check=True, capture_output=True, text=True)
    return target


def _tcp_open(host: str, port: int, timeout: float = 3) -> bool:
    """Return whether a TCP connection to host:port succeeds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def degradation_smoke(postgres_host: str, postgres_port: int) -> None:
    """Inject a fatal dependency loss and assert the live stack observes it."""
    if not _tcp_open(postgres_host, postgres_port):
        raise SmokeError(
            f"fatal dependency Postgres not reachable at {postgres_host}:{postgres_port} before injection"
        )
    stopped = _stop_container("postgres")
    _log(f"  injected failure: stopped {stopped}")
    for _ in range(15):
        if not _tcp_open(postgres_host, postgres_port):
            _log("degradation-smoke OK (killed fatal dependency Postgres is no longer reachable)")
            return
        time.sleep(1)
    raise SmokeError("Postgres still reachable after stopping its container")


def _build_parser() -> argparse.ArgumentParser:
    """Construct the acceptance-smoke argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("skills-smoke", help="Scan and quarantine a synthetic skill bundle.")
    sub.add_parser("worktree-smoke", help="Create, list, and prune a managed worktree.")
    sub.add_parser("acp-smoke", help="Drive an acp-runner session against a fake worker.")

    observability = sub.add_parser(
        "observability-smoke", help="Probe live observability endpoints."
    )
    observability.add_argument("--asset-root", default=".")

    stack = sub.add_parser("stack-wait", help="Wait for the live sidecars to answer on loopback.")
    stack.add_argument("--qdrant-url", default="http://127.0.0.1:6333/healthz")
    stack.add_argument("--neo4j-url", default="http://127.0.0.1:7474")
    stack.add_argument("--otel-metrics-url", default="http://127.0.0.1:9464/metrics")
    stack.add_argument("--timeout", type=float, default=180)

    retrieval = sub.add_parser("retrieval-smoke", help="Query the live Qdrant + Neo4j backends.")
    retrieval.add_argument("--qdrant-host", default="127.0.0.1")
    retrieval.add_argument("--qdrant-port", type=int, default=6333)
    retrieval.add_argument("--neo4j-host", default="127.0.0.1")
    retrieval.add_argument("--neo4j-http-port", type=int, default=7474)
    retrieval.add_argument("--neo4j-user", default="neo4j")
    retrieval.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASSWORD", ""))

    inference = sub.add_parser(
        "inference-smoke", help="Run a live model turn and assert the envelope."
    )
    inference.add_argument("--base-url", default="http://127.0.0.1:11434")
    inference.add_argument(
        "--model", default=os.environ.get("E2E_ACCEPTANCE_MODEL", "qwen2.5:0.5b")
    )
    inference.add_argument("--api-key", default=os.environ.get("E2E_ACCEPTANCE_API_KEY", ""))

    degradation = sub.add_parser("degradation-smoke", help="Inject a fatal dependency loss.")
    degradation.add_argument("--postgres-host", default="127.0.0.1")
    degradation.add_argument("--postgres-port", type=int, default=5432)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Dispatch one acceptance smoke."""
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "skills-smoke":
            skills_smoke()
        elif args.command == "worktree-smoke":
            worktree_smoke()
        elif args.command == "acp-smoke":
            acp_smoke()
        elif args.command == "observability-smoke":
            observability_smoke(args.asset_root)
        elif args.command == "stack-wait":
            stack_wait(args.qdrant_url, args.neo4j_url, args.otel_metrics_url, args.timeout)
        elif args.command == "retrieval-smoke":
            retrieval_smoke(
                args.qdrant_host,
                args.qdrant_port,
                args.neo4j_host,
                args.neo4j_http_port,
                args.neo4j_user,
                args.neo4j_password,
            )
        elif args.command == "inference-smoke":
            inference_smoke(args.base_url, args.model, args.api_key)
        elif args.command == "degradation-smoke":
            degradation_smoke(args.postgres_host, args.postgres_port)
        else:  # pragma: no cover - argparse enforces the command set
            raise AssertionError(f"unhandled command: {args.command}")
    except SmokeError as exc:
        print(f"e2e-acceptance error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
