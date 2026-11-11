"""CLI-only operator tools: governance, journaling, harness, intake, repo, and render surfaces.

Every test drives the real ``clawops`` entrypoint against scratch inputs and asserts on the
command's own observable outputs: exit codes, JSON payloads, files it writes, journal rows read
back through the CLI, and HTTP requests received by a loopback listener.
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import plistlib
import re
import shutil
import threading
from collections.abc import Generator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Final, cast

import yaml

from clawops.memory_profiles import MANAGED_MEMORY_PROFILE_IDS, MEMORY_PROFILES
from tests.utils.helpers.feature_verification import CommandOutcome, FeatureHost

type JsonObject = dict[str, object]

SHIPPED_POLICY: Final[Path] = Path("platform/configs/policy/policy.yaml")
HARNESS_DIR: Final[Path] = Path("platform/configs/harness")
HARNESS_SUITES: Final[tuple[str, ...]] = ("policy_regressions.yaml", "security_regressions.yaml")
CODE_REVIEW_WORKFLOW: Final[Path] = Path("platform/configs/workflows/code_review.yaml")
CODEBASE_CONTEXT_CONFIG: Final[Path] = Path("platform/configs/context/codebase.yaml")
ALLOWLIST_EXAMPLE: Final[Path] = Path("platform/configs/source-allowlists.example.yaml")
PLACEHOLDER_PATTERN: Final[re.Pattern[str]] = re.compile(r"__[A-Z][A-Z0-9_]*__")
SHA1_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{40}$")
SHA256_DIGEST_PATTERN: Final[re.Pattern[str]] = re.compile(r"^sha256:[0-9a-f]{64}$")
PNG_SIGNATURE: Final[bytes] = b"\x89PNG\r\n\x1a\n"
LOOPBACK_ENV: Final[dict[str, str]] = {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1"}
GIT_CONFIG_ARGS: Final[tuple[str, ...]] = (
    "-c",
    "user.name=Feature Verification",
    "-c",
    "user.email=featver@example.invalid",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "init.defaultBranch=main",
)
CHANNEL_CHECKS: Final[frozenset[str]] = frozenset(
    {
        "telegram-dm-policy",
        "telegram-group-policy",
        "telegram-allowlist",
        "whatsapp-dm-policy",
        "whatsapp-group-policy",
        "default-group-policy",
        "channel-docs-pairing",
        "channel-docs-allowlists",
        "telegram-guidance-pairing",
        "whatsapp-guidance-pairing",
        "rendered-telegram-allowlist",
        "rendered-whatsapp-allowlist",
    }
)


def _as_object(value: object) -> JsonObject:
    assert isinstance(value, dict), f"expected a JSON object, got {value!r}"
    return cast(JsonObject, value)


def _as_objects(value: object) -> list[JsonObject]:
    assert isinstance(value, list), f"expected a JSON array, got {value!r}"
    return [_as_object(item) for item in cast(list[object], value)]


def _ok_object(outcome: CommandOutcome) -> JsonObject:
    return _as_object(outcome.require_ok().json())


def _read_json(path: Path) -> object:
    assert path.is_file(), f"{path} was not written"
    return cast(object, json.loads(path.read_text(encoding="utf-8")))


def _read_jsonl(path: Path) -> list[JsonObject]:
    assert path.is_file(), f"{path} was not written"
    lines = path.read_text(encoding="utf-8").splitlines()
    return [_as_object(json.loads(line)) for line in lines if line.strip()]


def _write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _write_yaml(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _git(feature_host: FeatureHost, repo: Path, *arguments: str) -> CommandOutcome:
    return feature_host.run(
        ["git", *GIT_CONFIG_ARGS, "-C", str(repo), *arguments], timeout=60
    ).require_ok()


def _init_git_repo(feature_host: FeatureHost, repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    readme = repo / "README.md"
    if not readme.exists():
        readme.write_text("# feature verification fixture\n", encoding="utf-8")
    _git(feature_host, repo, "init", "-q")
    _git(feature_host, repo, "add", "-A")
    _git(feature_host, repo, "commit", "-q", "-m", "initial commit")


def _evaluate_policy(feature_host: FeatureHost, policy: Path, payload: Path) -> JsonObject:
    return _ok_object(
        feature_host.clawops("policy", "--policy", str(policy), "--input", str(payload))
    )


def _journal_begin(
    feature_host: FeatureHost, db: Path, *, target: str, payload: Path
) -> JsonObject:
    return _ok_object(
        feature_host.clawops(
            "op-journal",
            "begin",
            "--db",
            str(db),
            "--scope",
            "featver",
            "--kind",
            "webhook_post",
            "--trust-zone",
            "automation",
            "--target",
            target,
            "--payload-file",
            str(payload),
        )
    )


def _journal_transition(
    feature_host: FeatureHost, db: Path, op_id: str, status: str
) -> CommandOutcome:
    return feature_host.clawops(
        "op-journal", "transition", "--db", str(db), "--op-id", op_id, "--status", status
    )


def _list_stuck_ids(feature_host: FeatureHost, db: Path, older_than_ms: int) -> set[str]:
    stuck = feature_host.clawops(
        "op-journal", "list-stuck", "--db", str(db), "--older-than-ms", str(older_than_ms)
    ).require_ok()
    return {str(row["op_id"]) for row in _as_objects(stuck.json())}


def _approvals(
    feature_host: FeatureHost, command: str, db: Path, *arguments: str
) -> CommandOutcome:
    return feature_host.clawops("approvals", command, "--db", str(db), *arguments)


def _run_harness_suite(feature_host: FeatureHost, suite: str, output: Path) -> list[JsonObject]:
    outcome = feature_host.clawops(
        "harness", "--suite", str(HARNESS_DIR / suite), "--output", str(output), timeout=900
    ).require_ok()
    results = _read_jsonl(output)
    assert f"passed={len(results)} total={len(results)}" in outcome.stdout, outcome.describe()
    return results


def _suite_case_ids(feature_host: FeatureHost, suite: str) -> list[str]:
    document = _as_object(
        yaml.safe_load((feature_host.repo_root / HARNESS_DIR / suite).read_text("utf-8"))
    )
    return [str(case["id"]) for case in _as_objects(document["cases"])]


@dataclass(frozen=True, slots=True)
class _ReceivedRequest:
    method: str
    path: str
    content_type: str
    body: bytes


class _RecordingServer(ThreadingHTTPServer):
    """Loopback HTTP endpoint on an ephemeral port that records every request it answers."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _RecordingHandler)
        self._lock = threading.Lock()
        self._received: list[_ReceivedRequest] = []

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.server_port}{path}"

    def record(self, request: _ReceivedRequest) -> None:
        with self._lock:
            self._received.append(request)

    def received(self) -> list[_ReceivedRequest]:
        with self._lock:
            return list(self._received)


class _RecordingHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        cast(_RecordingServer, self.server).record(
            _ReceivedRequest(
                method="POST",
                path=self.path,
                content_type=self.headers.get("Content-Type", ""),
                body=body,
            )
        )
        reply = b'{"received": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@contextlib.contextmanager
def _recording_server() -> Generator[_RecordingServer, None, None]:
    server = _RecordingServer()
    thread = threading.Thread(target=server.serve_forever, name="featver-webhook", daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def test_policy_evaluates_shipped_rules(feature_host: FeatureHost) -> None:
    fixtures = HARNESS_DIR / "fixtures"
    approval = _evaluate_policy(
        feature_host, SHIPPED_POLICY, fixtures / "policy_github_approval.json"
    )
    assert approval["decision"] == "require_approval", approval
    assert approval["review_policy_id"] == "approval.actions.github.comment.create", approval
    assert approval["review_mode"] == "manual", approval
    assert approval["reasons"] == ["approval required by approval matrix"], approval

    webhook = _evaluate_policy(feature_host, SHIPPED_POLICY, fixtures / "policy_webhook_deny.json")
    assert webhook["decision"] == "deny", webhook
    assert webhook["reasons"] == ["target:webhook_url:not allowlisted"], webhook
    assert "review_mode" not in webhook, webhook

    browser = _evaluate_policy(feature_host, SHIPPED_POLICY, fixtures / "policy_browser_deny.json")
    assert browser["decision"] == "deny", browser
    assert browser["reasons"] == ["zone:coder:not in allowlist"], browser

    scratch = feature_host.scratch("policy")
    prod_webhook = _evaluate_policy(
        feature_host,
        SHIPPED_POLICY,
        _write_json(
            scratch / "prod-webhook.json",
            {
                "trust_zone": "automation",
                "action": "webhook.post",
                "category": "external_write",
                "target_kind": "webhook_url",
                "target": "https://example.internal/hooks/deploy",
                "environment": "prod",
            },
        ),
    )
    assert prod_webhook["decision"] == "require_approval", prod_webhook
    assert prod_webhook["matched_rules"] == ["approval-for-prod-webhook"], prod_webhook
    assert prod_webhook["review_reason"] == "production webhooks always require approval"

    browser_rule = _evaluate_policy(
        feature_host,
        SHIPPED_POLICY,
        _write_json(
            scratch / "browser-main-host.json",
            {
                "trust_zone": "admin",
                "action": "browser.navigate",
                "host_boundary": "main-control-plane",
            },
        ),
    )
    assert browser_rule["decision"] == "deny", browser_rule
    assert browser_rule["matched_rules"] == ["deny-browser-on-main-host"], browser_rule

    read_only = _write_json(
        scratch / "reader-search.json",
        {"trust_zone": "reader", "action": "memory.search", "category": "read_only"},
    )
    default_deny = _evaluate_policy(feature_host, SHIPPED_POLICY, read_only)
    assert default_deny == {"decision": "deny", "matched_rules": [], "reasons": ["default"]}

    # The shipped policy has no allow rule, so the allow path needs an operator default.
    shipped = _as_object(
        yaml.safe_load((feature_host.repo_root / SHIPPED_POLICY).read_text("utf-8"))
    )
    permissive = _write_yaml(
        scratch / "policy.yaml", {**shipped, "defaults": {"decision": "allow"}}
    )
    allowed = _evaluate_policy(feature_host, permissive, read_only)
    assert allowed == {"decision": "allow", "matched_rules": [], "reasons": ["default"]}
    still_gated = _evaluate_policy(
        feature_host, permissive, fixtures / "policy_github_approval.json"
    )
    assert still_gated["decision"] == "require_approval", still_gated


def test_op_journal_tracks_operation_lifecycle(feature_host: FeatureHost) -> None:
    scratch = feature_host.scratch("op-journal")
    db = scratch / "journal.sqlite"
    feature_host.clawops("op-journal", "init", "--db", str(db)).require_ok()
    assert db.is_file(), f"op-journal init did not create {db}"
    payload = _write_json(scratch / "payload.json", {"message": "deploy finished"})
    target = "https://example.internal/hooks/deploy"

    proposed = _journal_begin(feature_host, db, target=target, payload=payload)
    op_id = str(proposed["op_id"])
    assert proposed["status"] == "proposed" and proposed["attempt"] == 0, proposed
    assert json.loads(str(proposed["inputs_json"])) == {"message": "deploy finished"}, proposed
    replayed = _journal_begin(feature_host, db, target=target, payload=payload)
    assert replayed["op_id"] == op_id, "begin with identical inputs must be idempotent"

    pending = _ok_object(_journal_transition(feature_host, db, op_id, "pending_approval"))
    assert pending["status"] == "pending_approval", pending
    approved = _ok_object(
        feature_host.clawops(
            "op-journal",
            "approve",
            "--db",
            str(db),
            "--op-id",
            op_id,
            "--approved-by",
            "operator",
            "--note",
            "change window open",
        )
    )
    assert approved["status"] == "approved" and approved["approved_by"] == "operator", approved
    assert approved["approval_note"] == "change window open", approved
    running = _ok_object(_journal_transition(feature_host, db, op_id, "running"))
    assert running["status"] == "running" and running["attempt"] == 1, running

    assert op_id in _list_stuck_ids(feature_host, db, 0), "running op must be listed as stuck"
    assert op_id not in _list_stuck_ids(feature_host, db, 3_600_000), "fresh op is not stale"

    succeeded = _ok_object(_journal_transition(feature_host, db, op_id, "succeeded"))
    assert succeeded["status"] == "succeeded", succeeded
    assert op_id not in _list_stuck_ids(feature_host, db, 0), "terminal ops are never stuck"

    invalid = _journal_transition(feature_host, db, op_id, "running")
    assert not invalid.ok, invalid.describe()
    assert (
        "invalid operation transition: succeeded -> running" in invalid.stderr
    ), invalid.describe()


def test_approvals_review_queue_resolves_operations(feature_host: FeatureHost) -> None:
    scratch = feature_host.scratch("approvals")
    db = scratch / "journal.sqlite"
    payload = _write_json(scratch / "payload.json", {"message": "review me"})
    op_ids: list[str] = []
    for name in ("approve", "reject", "delegate"):
        op = _journal_begin(
            feature_host, db, target=f"https://example.internal/hooks/{name}", payload=payload
        )
        _journal_transition(feature_host, db, str(op["op_id"]), "pending_approval").require_ok()
        op_ids.append(str(op["op_id"]))
    approve_id, reject_id, delegate_id = op_ids

    queue_file = scratch / "queue.json"
    queue_outcome = _approvals(feature_host, "queue", db, "--output", str(queue_file)).require_ok()
    queue = _as_objects(queue_outcome.json())
    assert [str(row["op_id"]) for row in queue] == op_ids, queue
    assert all(row["status"] == "pending_approval" for row in queue), queue
    assert _read_json(queue_file) == queue, "queue --output must match the printed queue"

    shown = _ok_object(_approvals(feature_host, "show", db, "--op-id", approve_id))
    assert shown["op_id"] == approve_id and shown["status"] == "pending_approval", shown

    approved = _ok_object(
        _approvals(feature_host, "approve", db, "--op-id", approve_id, "--approved-by", "alice")
    )
    assert approved["status"] == "approved" and approved["review_status"] == "approved", approved
    assert approved["approved_by"] == "alice" and approved["reviewed_by"] == "alice", approved

    rejected = _ok_object(
        _approvals(
            feature_host,
            "reject",
            db,
            "--op-id",
            reject_id,
            "--reviewed-by",
            "bob",
            "--note",
            "target out of scope",
        )
    )
    assert rejected["status"] == "rejected" and rejected["review_status"] == "rejected", rejected
    assert rejected["last_error"] == "target out of scope", rejected

    delegated = _ok_object(
        _approvals(
            feature_host,
            "delegate",
            db,
            "--op-id",
            delegate_id,
            "--reviewed-by",
            "carol",
            "--to",
            "reviewer-acp-claude",
        )
    )
    assert delegated["status"] == "pending_approval", delegated
    assert delegated["review_status"] == "delegated", delegated
    assert delegated["review_target"] == "reviewer-acp-claude", delegated

    review_payload = _write_json(scratch / "review.json", {"summary": "change is safe"})
    review_artifact = scratch / "review.md"
    review_artifact.write_text("LGTM\n", encoding="utf-8")
    ingested = _ok_object(
        _approvals(
            feature_host,
            "ingest-review",
            db,
            "--op-id",
            delegate_id,
            "--reviewed-by",
            "reviewer-acp-claude",
            "--decision",
            "allow",
            "--payload-file",
            str(review_payload),
            "--artifact-path",
            str(review_artifact),
        )
    )
    assert ingested["status"] == "approved", ingested
    assert ingested["approved_by"] == "reviewer-acp-claude", ingested
    assert ingested["review_artifact_path"] == str(review_artifact), ingested
    assert json.loads(str(ingested["review_payload_json"])) == {
        "delegate_to": "reviewer-acp-claude",
        "review_decision": "allow",
        "summary": "change is safe",
    }, ingested

    final_queue = _approvals(feature_host, "queue", db).require_ok()
    assert _as_objects(final_queue.json()) == [], final_queue.describe()
    reapprove = _approvals(
        feature_host, "approve", db, "--op-id", reject_id, "--approved-by", "alice"
    )
    assert not reapprove.ok, "a rejected operation must not be approvable"


def test_harness_regression_suites_pass(feature_host: FeatureHost) -> None:
    scratch = feature_host.scratch("harness")
    for suite in HARNESS_SUITES:
        results = _run_harness_suite(feature_host, suite, scratch / f"{Path(suite).stem}.jsonl")
        assert [str(item["id"]) for item in results] == _suite_case_ids(feature_host, suite)
        failed = {
            str(item["id"]): item["failures"] for item in results if item["passed"] is not True
        }
        assert not failed, json.dumps(failed, indent=2)
        assert all(item["failures"] == [] for item in results), results


def test_charts_render_harness_results(feature_host: FeatureHost) -> None:
    scratch = feature_host.scratch("charts")
    results_path = scratch / "policy.jsonl"
    results = _run_harness_suite(feature_host, "policy_regressions.yaml", results_path)
    chart = scratch / "nested" / "harness.png"
    feature_host.clawops(
        "charts", "--input", str(results_path), "--output", str(chart)
    ).require_ok()
    assert chart.is_file(), f"charts did not write {chart}"
    data = chart.read_bytes()
    assert data.startswith(PNG_SIGNATURE), "chart output is not a PNG"
    assert len(data) > 4096, f"chart is suspiciously small ({len(data)} bytes)"
    width, height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    expected_ratio = max(6.0, len(results) * 1.2) / 4.0
    assert abs(width / height - expected_ratio) < 0.02, (width, height, expected_ratio)


def test_merge_json_deep_merges_overlays(feature_host: FeatureHost) -> None:
    scratch = feature_host.scratch("merge-json")
    base = _write_json(
        scratch / "base.json",
        {
            "gateway": {"port": 18789, "bind": "loopback", "auth": {"mode": "token"}},
            "agents": {"list": [{"id": "admin"}, {"id": "reader"}]},
            "keep": True,
        },
    )
    first = scratch / "10-gateway.json5"
    first.write_text(
        "{\n"
        "  // operator overlay: json5 comments and trailing commas are accepted\n"
        "  gateway: { port: 19001, auth: { token: 'from-overlay' } },\n"
        "  agents: { list: [{ id: 'coder' }] },\n"
        "}\n",
        encoding="utf-8",
    )
    second = _write_json(scratch / "20-extra.json", {"gateway": {"bind": "lan"}, "extra": {"n": 1}})
    output = scratch / "merged.json"
    feature_host.clawops(
        "merge-json",
        "--base",
        str(base),
        "--overlay",
        str(first),
        str(second),
        "--output",
        str(output),
    ).require_ok()
    assert _read_json(output) == {
        "gateway": {
            "port": 19001,
            "bind": "lan",
            "auth": {"mode": "token", "token": "from-overlay"},
        },
        "agents": {"list": [{"id": "coder"}]},
        "keep": True,
        "extra": {"n": 1},
    }

    duplicate = scratch / "duplicate.json5"
    duplicate.write_text('{"gateway": {"port": 1}, "gateway": {"port": 2}}\n', encoding="utf-8")
    rejected_output = scratch / "rejected.json"
    rejected = feature_host.clawops(
        "merge-json",
        "--base",
        str(base),
        "--overlay",
        str(duplicate),
        "--output",
        str(rejected_output),
    )
    assert not rejected.ok, "overlays with duplicate keys must be rejected"
    assert not rejected_output.exists(), "a rejected merge must not write output"


def test_allowlists_render_channel_fragment(feature_host: FeatureHost) -> None:
    scratch = feature_host.scratch("allowlists")
    output = scratch / "channels.json"
    outcome = feature_host.clawops(
        "allowlists", "--source", str(ALLOWLIST_EXAMPLE), "--output", str(output)
    ).require_ok()
    expected = {
        "channels": {
            "telegram": {"allowFrom": ["tg:12345678"]},
            "whatsapp": {"allowFrom": ["+5511999999999"]},
            "modelByChannel": {
                "telegram": {"12345678": "messaging"},
                "whatsapp": {"+5511999999999": "messaging"},
            },
        }
    }
    assert outcome.json() == expected, outcome.describe()
    assert _read_json(output) == expected

    invalid = _write_json(
        scratch / "invalid.json", {"telegram_allow": ["telegram:42"], "whatsapp_allow": ["12345"]}
    )
    rejected = feature_host.clawops(
        "allowlists", "--source", str(invalid), "--output", str(scratch / "rejected.json")
    )
    assert (
        not rejected.ok and "invalid WhatsApp E.164: 12345" in rejected.stderr
    ), rejected.describe()


def test_skills_intake_moves_bundle_through_stages(feature_host: FeatureHost) -> None:
    scratch = feature_host.scratch("skills")
    source = scratch / "incoming" / "weather-skill"
    (source / "scripts").mkdir(parents=True)
    (source / "SKILL.md").write_text("# Weather\nSummarize the forecast.\n", encoding="utf-8")
    script = "import subprocess\nsubprocess.run(['curl', 'https://example.invalid'])\n"
    (source / "scripts" / "fetch.py").write_text(script, encoding="utf-8")
    skills_root = scratch / "skills"
    resolved_root = skills_root.resolve()

    scan_report = scratch / "scan.json"
    scanned = _ok_object(
        feature_host.clawops(
            "skills", "scan", "--source", str(source), "--report", str(scan_report)
        )
    )
    assert scanned["status"] == "scanned", scanned
    assert scanned["bundlePath"] == source.resolve().as_posix(), scanned
    assert scanned["findingCounts"] == {"critical": 1, "high": 1}, scanned
    findings = _as_objects(scanned["findings"])
    assert {(str(item["rule"]), item["line"]) for item in findings} == {
        ("shell exfil", 2),
        ("python subprocess", 2),
    }, findings
    assert {str(item["path"]) for item in _as_objects(scanned["files"])} == {
        "SKILL.md",
        "scripts/fetch.py",
    }, scanned
    assert _read_json(scan_report) == scanned

    manifest = scratch / "manifest.json"
    quarantined = _ok_object(
        feature_host.clawops(
            "skills",
            "quarantine",
            "--source",
            str(source),
            "--report",
            str(manifest),
            "--quarantine-root",
            str(skills_root / "quarantine"),
        )
    )
    quarantine_copy = resolved_root / "quarantine" / "weather-skill"
    assert quarantined["status"] == "quarantined", quarantined
    assert quarantined["bundlePath"] == quarantine_copy.as_posix(), quarantined
    assert (quarantine_copy / "scripts" / "fetch.py").read_text(encoding="utf-8") == script
    assert (source / "SKILL.md").is_file(), "quarantine must copy, not move, the source"

    premature = feature_host.clawops(
        "skills",
        "promote",
        "--manifest",
        str(manifest),
        "--skills-root",
        str(skills_root),
        "--stage",
        "approved",
    )
    assert not premature.ok, "quarantined bundles must be reviewed before approval"
    assert "cannot transition quarantined -> approved" in premature.stderr, premature.describe()

    transitions = (("promote", "reviewed"), ("promote", "approved"), ("demote", "reviewed"))
    previous = quarantine_copy
    for command, stage in transitions:
        staged = _ok_object(
            feature_host.clawops(
                "skills",
                command,
                "--manifest",
                str(manifest),
                "--skills-root",
                str(skills_root),
                "--stage",
                stage,
            )
        )
        destination = resolved_root / stage / "weather-skill"
        assert staged["status"] == stage, staged
        assert staged["bundlePath"] == destination.as_posix(), staged
        assert (destination / "SKILL.md").is_file(), f"{destination} was not populated"
        assert not previous.exists(), f"{command} left the bundle behind at {previous}"
        previous = destination

    history = _as_objects(_as_object(_read_json(manifest))["stageHistory"])
    assert [(item["status"], item["reason"]) for item in history] == [
        ("quarantined", "initial_scan"),
        ("reviewed", "promote"),
        ("approved", "promote"),
        ("reviewed", "demote"),
    ], history


def test_worktree_lifecycle_creates_lists_and_prunes(feature_host: FeatureHost) -> None:
    root = feature_host.scratch("worktree")
    _init_git_repo(feature_host, root / "repo" / "upstream")
    managed_path = (root / "repo" / "worktrees" / "feature-x").resolve()

    initial = _ok_object(feature_host.clawops("worktree", "--repo-root", str(root), "list"))
    initial_entries = _as_objects(initial["worktrees"])
    assert len(initial_entries) == 1 and initial_entries[0]["managed"] is False, initial

    created = _ok_object(
        feature_host.clawops("worktree", "--repo-root", str(root), "new", "--branch", "feature-x")
    )
    entry = _as_object(created["created"])
    assert created["branch"] == "feature-x", created
    assert Path(str(entry["path"])).resolve() == managed_path, entry
    assert entry["branch"] == "feature-x" and entry["managed"] is True, entry
    assert (managed_path / "README.md").is_file(), "worktree was not checked out"

    duplicate = feature_host.clawops(
        "worktree", "--repo-root", str(root), "new", "--branch", "feature-x"
    )
    assert duplicate.returncode == 1, duplicate.describe()
    assert _as_object(duplicate.json())["ok"] is False, duplicate.describe()

    listed = _as_objects(
        _ok_object(feature_host.clawops("worktree", "--repo-root", str(root), "list"))["worktrees"]
    )
    managed = [item for item in listed if item["managed"] is True]
    assert len(listed) == 2 and [item["branch"] for item in managed] == ["feature-x"], listed

    shutil.rmtree(managed_path)
    pruned = _ok_object(feature_host.clawops("worktree", "--repo-root", str(root), "prune"))
    assert pruned["beforeCount"] == 2 and pruned["afterCount"] == 1, pruned
    remaining = _as_objects(pruned["worktrees"])
    assert all(item["managed"] is False for item in remaining), remaining


def test_repo_doctor_accepts_valid_layout(feature_host: FeatureHost) -> None:
    root = feature_host.scratch("repo-doctor")
    _init_git_repo(feature_host, root / "repo" / "upstream")
    (root / "repo" / "worktrees").mkdir()

    report = _ok_object(feature_host.clawops("repo", "--repo-root", str(root), "doctor"))
    checks = _as_object(report["checks"])
    assert report["ok"] is True and "errors" not in report, report
    assert checks["gitAvailable"] is True and checks["upstreamExists"] is True, checks
    assert checks["worktreesRootExists"] is True and checks["managedWorktrees"] == 0, checks
    assert checks["repoRoot"] == root.resolve().as_posix(), checks
    assert len(_as_objects(report["worktrees"])) == 1, report

    missing = feature_host.clawops(
        "repo", "--repo-root", str(root), "doctor", "--branch", "missing-branch"
    )
    missing_report = _as_object(missing.json())
    assert missing.returncode == 1 and missing_report["ok"] is False, missing.describe()
    assert any(
        "missing managed worktree for branch missing-branch" in str(error)
        for error in cast(list[object], missing_report["errors"])
    ), missing_report


def _code_review_base(feature_host: FeatureHost, name: str) -> Path:
    base = feature_host.scratch(name)
    for relative in (SHIPPED_POLICY, CODEBASE_CONTEXT_CONFIG):
        (base / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(feature_host.repo_root / relative, base / relative)
    notes = base / "docs" / "auth-changes.md"
    notes.parent.mkdir(parents=True)
    # Verbatim query phrase: multi-term FTS hits are dropped by the contentless files_fts JOIN.
    notes.write_text(
        "# Auth notes\n\nPlease review recent auth changes before merge.\n", encoding="utf-8"
    )
    return base


def _workflow_steps(outcome: CommandOutcome) -> list[tuple[str, str, str]]:
    rows = [line.split("\t") for line in outcome.stdout.splitlines() if "\t" in line]
    assert all(len(row) == 3 for row in rows), outcome.describe()
    return [(row[0], row[1], row[2]) for row in rows]


def test_workflow_code_review_records_step_results(feature_host: FeatureHost) -> None:
    workflow = _as_object(
        yaml.safe_load((feature_host.repo_root / CODE_REVIEW_WORKFLOW).read_text(encoding="utf-8"))
    )
    step_names = [str(step["name"]) for step in _as_objects(workflow["steps"])]
    assert step_names == ["init journal", "policy preflight", "dry context pack"], step_names

    base = _code_review_base(feature_host, "code-review")
    state_dir = feature_host.scratch("code-review-state")
    run = feature_host.clawops(
        "workflow",
        "--workflow",
        str(CODE_REVIEW_WORKFLOW),
        "--base-dir",
        str(base),
        env={"STRONGCLAW_STATE_DIR": str(state_dir)},
        timeout=600,
    ).require_ok()
    steps = _workflow_steps(run)
    assert [(name, status) for name, status, _ in steps] == [
        (name, "ok") for name in step_names
    ], run.describe()
    journal_message, decision_message, pack_message = (message for _, _, message in steps)
    journal = base.resolve() / ".clawops" / "op_journal.sqlite"
    assert journal_message == f"journal={journal}", run.describe()
    assert journal.is_file(), f"journal_init did not create {journal}"
    assert decision_message == "require_approval", run.describe()
    pack_path = Path(pack_message.removeprefix("context packed -> "))
    assert pack_message.startswith("context packed -> "), run.describe()
    assert pack_path.resolve().is_relative_to(state_dir.resolve()), pack_path
    pack = pack_path.read_text(encoding="utf-8")
    assert "- query: review recent auth changes" in pack, pack
    assert "### docs/auth-changes.md" in pack, pack

    dry_base = _code_review_base(feature_host, "code-review-dry")
    dry_state = feature_host.scratch("code-review-dry-state")
    dry_run = feature_host.clawops(
        "workflow",
        "--workflow",
        str(CODE_REVIEW_WORKFLOW),
        "--base-dir",
        str(dry_base),
        "--dry-run",
        env={"STRONGCLAW_STATE_DIR": str(dry_state)},
    ).require_ok()
    dry_journal = dry_base.resolve() / ".clawops" / "op_journal.sqlite"
    assert _workflow_steps(dry_run) == [
        ("init journal", "ok", f"journal={dry_journal}"),
        ("policy preflight", "ok", "require_approval"),
        ("dry context pack", "ok", "dry-run context pack"),
    ], dry_run.describe()
    assert not dry_journal.exists(), "--dry-run must not create the journal"
    assert not any(dry_state.iterdir()), "--dry-run must not write context packs"


def test_webhook_wrapper_posts_once_after_approval(feature_host: FeatureHost) -> None:
    scratch = feature_host.scratch("webhook")
    db = scratch / "journal.sqlite"
    body = {"event": "deploy.finished", "build": 42}
    payload = _write_json(scratch / "payload.json", body)
    with _recording_server() as listener:
        url = listener.url("/hooks/deploy")
        policy = _write_yaml(
            scratch / "policy.yaml",
            {
                "defaults": {"decision": "deny"},
                "zones": {
                    "automation": {
                        "allow_actions": ["webhook.post"],
                        "allow_categories": ["external_write"],
                    }
                },
                "approval": {"require_for_actions": ["webhook.post"]},
                "allowlists": {"webhook_url": [url]},
            },
        )

        def wrapper(*arguments: str) -> CommandOutcome:
            return feature_host.clawops(
                "wrapper",
                "webhook",
                "--db",
                str(db),
                "--policy",
                str(policy),
                *arguments,
                env=LOOPBACK_ENV,
                timeout=120,
            )

        def propose(target: str) -> CommandOutcome:
            return wrapper(
                "--scope",
                "featver",
                "--trust-zone",
                "automation",
                "--url",
                target,
                "--payload-file",
                str(payload),
            )

        proposal = _ok_object(propose(url))
        op_id = str(proposal["op_id"])
        assert proposal["status"] == "pending_approval", proposal
        assert proposal["accepted"] is True and proposal["executed"] is False, proposal
        assert _as_object(proposal["decision"])["decision"] == "require_approval", proposal
        assert Path(str(proposal["review_artifact_path"])).is_file(), proposal
        assert listener.received() == [], "nothing may be sent before approval"

        early = _ok_object(wrapper("--op-id", op_id, "--execute-approved"))
        assert early["status"] == "pending_approval" and early["executed"] is False, early
        assert listener.received() == [], "an unapproved op must not be sent"

        _approvals(
            feature_host, "approve", db, "--op-id", op_id, "--approved-by", "ops"
        ).require_ok()
        executed = _ok_object(wrapper("--op-id", op_id, "--execute-approved"))
        assert executed["ok"] is True and executed["executed"] is True, executed
        assert executed["status"] == "succeeded" and executed["status_code"] == 200, executed
        assert executed["request_method"] == "POST" and executed["request_url"] == url, executed

        replay = _ok_object(wrapper("--op-id", op_id, "--execute-approved"))
        assert replay["status"] == "succeeded" and replay["status_code"] == 200, replay

        denied = propose(listener.url("/hooks/not-allowlisted"))
        denied_result = _as_object(denied.json())
        assert denied.returncode == 1 and denied_result["accepted"] is False, denied.describe()
        assert denied_result["status"] == "failed", denied_result

        received = listener.received()
    assert len(received) == 1, f"listener received {len(received)} requests, expected exactly 1"
    request = received[0]
    assert (request.method, request.path) == ("POST", "/hooks/deploy"), request
    assert request.content_type == "application/json", request
    assert json.loads(request.body) == body, request
    journal_row = _ok_object(_approvals(feature_host, "show", db, "--op-id", op_id))
    assert journal_row["status"] == "succeeded" and journal_row["attempt"] == 1, journal_row
    assert journal_row["result_status_code"] == 200, journal_row
    assert journal_row["result_body_excerpt"] == '{"received": true}', journal_row


def test_github_wrapper_dry_run_plans_without_executing(feature_host: FeatureHost) -> None:
    scratch = feature_host.scratch("github")
    db = scratch / "journal.sqlite"
    repo = "your-org/openclaw-platform"
    target = f"github://{repo}/issues/7"
    common = ("--db", str(db), "--scope", "featver", "--trust-zone", "coder", "--repo", repo)

    comment = _ok_object(
        feature_host.clawops(
            "wrapper",
            "github",
            *common,
            "--policy",
            str(SHIPPED_POLICY),
            "--operation",
            "comment",
            "--issue-number",
            "7",
            "--body",
            "Feature verification comment",
            "--dry-run",
        )
    )
    assert comment["status"] == "pending_approval", comment
    assert comment["accepted"] is True and comment["executed"] is False, comment
    assert _as_object(comment["decision"])["decision"] == "require_approval", comment

    allow_policy = _write_yaml(
        scratch / "policy.yaml",
        {
            "defaults": {"decision": "allow"},
            "zones": {"coder": {"allow_actions": ["github.issue.labels.add"]}},
            "allowlists": {"github_repo": [repo]},
        },
    )
    labels = _ok_object(
        feature_host.clawops(
            "wrapper",
            "github",
            *common,
            "--policy",
            str(allow_policy),
            "--operation",
            "labels",
            "--issue-number",
            "7",
            "--label",
            "needs-review",
            "--label",
            "featver",
            "--dry-run",
        )
    )
    assert labels["status"] == "approved" and labels["dry_run"] is True, labels
    assert labels["executed"] is False and "status_code" not in labels, labels

    expected_inputs = {
        str(comment["op_id"]): ("github_comment", {"body": "Feature verification comment"}),
        str(labels["op_id"]): ("github_issue_labels", {"labels": ["needs-review", "featver"]}),
    }
    for op_id, (kind, inputs) in expected_inputs.items():
        row = _ok_object(_approvals(feature_host, "show", db, "--op-id", op_id))
        assert row["kind"] == kind and row["normalized_target"] == target, row
        assert json.loads(str(row["inputs_json"])) == inputs, row
        assert row["attempt"] == 0 and row["result_status_code"] is None, row
        assert row["result_request_url"] is None and row["result_ok"] is None, row
    queued = _as_objects(_approvals(feature_host, "queue", db).require_ok().json())
    assert [row["op_id"] for row in queued] == [comment["op_id"]], queued


def test_supply_chain_inventory_lists_pins(feature_host: FeatureHost) -> None:
    inventory = _ok_object(feature_host.clawops("supply-chain", "inventory"))
    assert inventory["ok"] is True, inventory
    assert inventory["repoRoot"] == feature_host.repo_root.resolve().as_posix(), inventory
    actions = _as_objects(inventory["workflowActions"])
    images = _as_objects(inventory["composeImages"])
    assert actions and images, inventory
    file_lines: dict[str, list[str]] = {}

    def line_at(relative: object, line: object) -> str:
        key = str(relative)
        if key not in file_lines:
            file_lines[key] = (
                (feature_host.repo_root / key).read_text(encoding="utf-8").splitlines()
            )
        return file_lines[key][cast(int, line) - 1]

    for pin in actions:
        assert str(pin["path"]).startswith(".github/workflows/"), pin
        assert SHA1_PATTERN.fullmatch(str(pin["ref"])), f"action not pinned to a commit: {pin}"
        assert f"{pin['action']}@{pin['ref']}" in line_at(pin["path"], pin["line"]), pin
    for pin in images:
        assert str(pin["path"]).startswith("platform/compose/"), pin
        assert SHA256_DIGEST_PATTERN.fullmatch(str(pin["digest"])), pin
        assert f"{pin['image']}@{pin['digest']}" in line_at(pin["path"], pin["line"]), pin


def test_supply_chain_propose_refresh_dry_run_plans_updates(feature_host: FeatureHost) -> None:
    root = feature_host.scratch("supply-chain")
    old_digest = f"sha256:{'1' * 64}"
    new_digest = f"sha256:{'2' * 64}"
    workflow = root / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    # No version-tag comment, so the action refresh resolves nothing over the network.
    workflow.write_text(
        f"jobs:\n  build:\n    steps:\n      - uses: actions/checkout@{'a' * 40}\n",
        encoding="utf-8",
    )
    compose = root / "platform" / "compose" / "docker-compose.yaml"
    compose.parent.mkdir(parents=True)
    compose_text = f"services:\n  db:\n    image: postgres:16-alpine@{old_digest}\n"
    compose.write_text(compose_text, encoding="utf-8")
    _init_git_repo(feature_host, root)

    shim_dir = feature_host.scratch("supply-chain-shim")
    shim_log = shim_dir / "docker.log"
    docker_shim = shim_dir / "docker"
    docker_shim.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> '{shim_log}'\n"
        f"printf 'Name:      %s\\nDigest:    {new_digest}\\n' \"$4\"\n",
        encoding="utf-8",
    )
    docker_shim.chmod(0o755)
    path = os.environ.get("PATH", "")
    head_before = _git(feature_host, root, "rev-parse", "HEAD").stdout.strip()

    plan = _ok_object(
        feature_host.clawops(
            "supply-chain",
            "--repo-root",
            str(root),
            "propose-refresh",
            "--dry-run",
            "--create-pr",
            "--refresh-command",
            "uv lock",
            env={"PATH": f"{shim_dir}:{path}"},
        )
    )
    assert plan["ok"] is True and plan["dryRun"] is True, plan
    assert plan["workflowActions"] == [], plan
    assert plan["composeImages"] == [
        {
            "path": "platform/compose/docker-compose.yaml",
            "line": 3,
            "image": "postgres:16-alpine",
            "from": old_digest,
            "to": new_digest,
        }
    ], plan
    assert plan["refreshCommands"] == ["uv lock"], plan
    assert "prUrl" not in plan and "branch" not in plan, plan
    assert shim_log.read_text(encoding="utf-8").splitlines() == [
        "buildx imagetools inspect postgres:16-alpine"
    ]
    assert compose.read_text(encoding="utf-8") == compose_text, "dry-run must not rewrite pins"
    assert _git(feature_host, root, "status", "--porcelain").stdout.strip() == ""
    assert _git(feature_host, root, "rev-parse", "HEAD").stdout.strip() == head_before
    branches = _git(feature_host, root, "branch", "--format=%(refname:short)").stdout.split()
    assert branches == ["main"], f"dry-run must not create branches: {branches}"


def test_structured_logs_emit_json_events(feature_host: FeatureHost) -> None:
    project = feature_host.scratch("structured-logs")
    (project / "src").mkdir()
    (project / "src" / "service.py").write_text("def handler() -> int:\n    return 1\n")
    (project / "README.md").write_text("# Service\n", encoding="utf-8")
    index_args = (
        "context",
        "codebase",
        "index",
        "--config",
        str(CODEBASE_CONTEXT_CONFIG),
        "--repo",
        str(project),
        "--scale",
        "small",
        "--json",
    )

    enabled = feature_host.clawops(*index_args, env={"CLAWOPS_STRUCTURED_LOGS": "1"})
    stats = _ok_object(enabled)
    records = [
        _as_object(json.loads(line)) for line in enabled.stderr.splitlines() if line.startswith("{")
    ]
    index_events = [item for item in records if item["event"] == "clawops.context.codebase.index"]
    assert len(index_events) == 1, enabled.describe()
    event = index_events[0]
    assert event["provider"] == "codebase" and event["scale"] == "small", event
    assert event["repo"] == project.resolve().as_posix(), event
    assert event["indexed_files"] == stats["indexed_files"] == 2, (event, stats)
    assert isinstance(event["elapsed_ms"], int), event
    assert "clawops.context.codebase.index" not in enabled.stdout, "logs belong on stderr"

    (project / "src" / "extra.py").write_text("VALUE = 2\n", encoding="utf-8")
    disabled = feature_host.clawops(*index_args, env={"CLAWOPS_STRUCTURED_LOGS": "0"})
    assert _ok_object(disabled)["indexed_files"] == 1, disabled.describe()
    assert '"event"' not in disabled.stderr, disabled.describe()


def test_baseline_harness_smoke_writes_results(feature_host: FeatureHost) -> None:
    runs_dir = feature_host.scratch("baseline-runs")
    payload = _ok_object(
        feature_host.clawops("baseline", "--runs-dir", str(runs_dir), "harness-smoke", timeout=900)
    )
    assert payload == {"ok": True, "runsDir": str(runs_dir)}, payload
    for output, suite in (
        ("security.jsonl", "security_regressions.yaml"),
        ("policy.jsonl", "policy_regressions.yaml"),
    ):
        results = _read_jsonl(runs_dir / output)
        assert [str(item["id"]) for item in results] == _suite_case_ids(feature_host, suite)
        assert all(item["passed"] is True for item in results), results


def test_verify_platform_channels_passes(feature_host: FeatureHost) -> None:
    report = _ok_object(feature_host.clawops("verify-platform", "channels", "--asset-root", "."))
    checks = _as_objects(report["checks"])
    assert report["name"] == "channels" and report["ok"] is True, report
    assert {str(check["name"]) for check in checks} == CHANNEL_CHECKS, report
    assert all(check["ok"] is True for check in checks), report

    drifted = _write_json(
        feature_host.scratch("channels") / "30-channels.json",
        {
            "channels": {
                "telegram": {"dmPolicy": "open", "groupPolicy": "allowlist", "allowFrom": ["tg:1"]},
                "whatsapp": {"dmPolicy": "pairing", "groupPolicy": "allowlist"},
                "defaults": {"groupPolicy": "allowlist"},
            }
        },
    )
    failing = feature_host.clawops(
        "verify-platform", "channels", "--asset-root", ".", "--overlay", str(drifted)
    )
    failing_report = _as_object(failing.json())
    failed = {
        str(check["name"]) for check in _as_objects(failing_report["checks"]) if not check["ok"]
    }
    assert failing.returncode == 1 and failing_report["ok"] is False, failing.describe()
    assert failed == {"telegram-dm-policy"}, failing_report


def test_config_lists_managed_memory_profiles(feature_host: FeatureHost) -> None:
    home = feature_host.scratch("config-home")
    listing = _ok_object(
        feature_host.clawops(
            "config",
            "--asset-root",
            ".",
            "--home-dir",
            str(home),
            "memory",
            "--list-profiles",
            "--json",
        )
    )
    profiles = _as_objects(listing["profiles"])
    assert [profile["id"] for profile in profiles] == list(MANAGED_MEMORY_PROFILE_IDS), listing
    for profile in profiles:
        spec = MEMORY_PROFILES[str(profile["id"])]
        assert profile["renderProfile"] == spec.render_profile, profile
        assert profile["description"] == spec.description, profile
    unmanaged = {profile_id for profile_id, spec in MEMORY_PROFILES.items() if not spec.managed}
    assert unmanaged.isdisjoint(str(profile["id"]) for profile in profiles), listing
    assert not any(home.iterdir()), "listing profiles must not write into the home dir"


def test_render_openclaw_config_merges_overlay(feature_host: FeatureHost) -> None:
    scratch = feature_host.scratch("render")
    home = scratch / "home"
    home.mkdir()
    config_dir = scratch / "strongclaw-config"
    isolation = {
        "STRONGCLAW_CONFIG_DIR": str(config_dir),
        "STRONGCLAW_DATA_DIR": str(scratch / "strongclaw-data"),
        "STRONGCLAW_STATE_DIR": str(scratch / "strongclaw-state"),
    }
    common = (
        "render-openclaw-config",
        "--asset-root",
        ".",
        "--profile",
        "openclaw-default",
        "--home-dir",
        str(home),
        "--user-timezone",
        "UTC",
    )
    base_path = scratch / "base.json"
    feature_host.clawops(*common, "--output", str(base_path), env=isolation).require_ok()
    base = _as_object(_read_json(base_path))

    overlay = scratch / "90-operator.json5"
    overlay.write_text(
        "{\n"
        "  // operator overlay\n"
        "  agents: { defaults: {\n"
        "    imageMaxDimensionPx: 800,\n"
        "    heartbeat: { every: '30m' },\n"
        "    workspace: '__CODER_WORKSPACE__',\n"
        "  } },\n"
        "}\n",
        encoding="utf-8",
    )
    merged_path = scratch / "openclaw.json"
    approvals_path = scratch / "exec-approvals.json"
    rendered = feature_host.clawops(
        *common,
        "--overlay",
        str(overlay),
        "--output",
        str(merged_path),
        "--exec-approvals-output",
        str(approvals_path),
        env=isolation,
    ).require_ok()
    assert f"Rendered {merged_path.resolve()}" in rendered.stdout, rendered.describe()
    assert f"Rendered {approvals_path.resolve()}" in rendered.stdout, rendered.describe()

    expected = copy.deepcopy(base)
    defaults = _as_object(_as_object(expected["agents"])["defaults"])
    admin_workspace = Path(str(defaults["workspace"]))
    defaults["imageMaxDimensionPx"] = 800
    _as_object(defaults["heartbeat"])["every"] = "30m"
    defaults["workspace"] = (admin_workspace.parent / "coder").as_posix()
    merged_text = merged_path.read_text(encoding="utf-8")
    assert json.loads(merged_text) == expected, "overlay must change only the keys it sets"
    assert PLACEHOLDER_PATTERN.search(merged_text) is None, merged_text

    approvals_text = approvals_path.read_text(encoding="utf-8")
    approvals = _as_object(json.loads(approvals_text))
    rules = _as_objects(approvals["rules"])
    assert approvals["version"] == 1 and [rule["id"] for rule in rules] == ["workspace-tests"]
    prefixes = cast(list[str], _as_object(rules[0]["match"])["cwdPrefixes"])
    assert prefixes[0] == feature_host.repo_root.resolve().as_posix(), prefixes
    assert all(Path(prefix).is_absolute() for prefix in prefixes), prefixes
    assert PLACEHOLDER_PATTERN.search(approvals_text) is None, approvals_text
    for name in ("hypermemory.yaml", "hypermemory.sqlite.yaml"):
        assert (config_dir / "memory" / name).is_file(), f"{name} was not materialized"


def test_services_render_every_template(feature_host: FeatureHost) -> None:
    home = feature_host.scratch("services-home")
    state_dir = feature_host.scratch("services-state")
    managers: dict[str, tuple[Path, list[str]]] = {
        "systemd": (
            home / ".config" / "systemd" / "user",
            sorted(
                path.name
                for pattern in ("*.service", "*.timer")
                for path in (feature_host.repo_root / "platform" / "systemd").glob(pattern)
            ),
        ),
        "launchd": (
            home / "Library" / "LaunchAgents",
            sorted(
                path.stem
                for path in (feature_host.repo_root / "platform" / "launchd").glob("*.template")
            ),
        ),
    }
    for manager, (output_dir, expected_names) in managers.items():
        payload = _ok_object(
            feature_host.clawops(
                "services",
                "--asset-root",
                ".",
                "--state-dir",
                str(state_dir),
                "--service-manager",
                manager,
                "render",
                env={"HOME": str(home)},
            )
        )
        assert payload["serviceManager"] == manager, payload
        assert Path(str(payload["outputDir"])).resolve() == output_dir.resolve(), payload
        assert Path(str(payload["stateDir"])).resolve() == state_dir.resolve(), payload
        rendered = [Path(item) for item in cast(list[str], payload["renderedFiles"])]
        assert sorted(path.name for path in rendered) == expected_names, payload
        corpus = ""
        for path in rendered:
            text = path.read_text(encoding="utf-8")
            leftover = PLACEHOLDER_PATTERN.findall(text)
            assert not leftover, f"{path} kept placeholders {sorted(set(leftover))}"
            if manager == "launchd":
                label = _as_object(plistlib.loads(text.encode("utf-8")))["Label"]
                assert f"{label}.plist" == path.name, path
            else:
                section = "[Timer]" if path.suffix == ".timer" else "[Service]"
                assert section in text, f"{path} has no {section} section"
            corpus += text
        assert feature_host.repo_root.resolve().as_posix() in corpus, manager
        assert state_dir.resolve().as_posix() in corpus, manager
