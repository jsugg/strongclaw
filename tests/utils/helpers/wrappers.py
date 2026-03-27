"""Shared helpers for wrapper contract tests."""

from __future__ import annotations

import dataclasses
import json
import pathlib
from collections.abc import Callable

from clawops.op_journal import OperationJournal
from clawops.policy_engine import PolicyEngine
from clawops.wrappers.base import WrapperContext
from clawops.wrappers.github import (
    add_labels,
    create_comment,
    execute_github_approved,
    merge_pull_request,
)
from clawops.wrappers.webhook import execute_webhook_approved, invoke_webhook
from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers.journal import create_journal
from tests.utils.helpers.policy import write_policy_file

type InvokeWrapper = Callable[[WrapperContext, str], dict[str, object]]
type ExecuteWrapper = Callable[[WrapperContext, str], dict[str, object]]
type AllowlistValue = Callable[[str], str]


def _empty_env() -> dict[str, str]:
    """Return an empty wrapper environment override mapping."""
    return {}


@dataclasses.dataclass(frozen=True, slots=True)
class WrapperSpec:
    """Wrapper-specific lifecycle contract inputs."""

    name: str
    action: str
    category: str
    allowlist_key: str
    allowed_input: str
    denied_input: str
    normalized_target: str
    kind: str
    payload: dict[str, object]
    invoke: InvokeWrapper
    execute: ExecuteWrapper
    allowlist_value: AllowlistValue
    env: dict[str, str] = dataclasses.field(default_factory=_empty_env)


def _identity(value: str) -> str:
    return value


def _invoke_webhook(ctx: WrapperContext, url: str) -> dict[str, object]:
    return invoke_webhook(
        ctx=ctx,
        url=url,
        payload_body={"ok": True},
        scope="test",
        trust_zone="automation",
    )


def _invoke_github_comment(ctx: WrapperContext, repo: str) -> dict[str, object]:
    return create_comment(
        ctx=ctx,
        repo=repo,
        issue_number=123,
        body="hello",
        scope="test",
        trust_zone="automation",
    )


def _invoke_github_labels(ctx: WrapperContext, repo: str) -> dict[str, object]:
    return add_labels(
        ctx=ctx,
        repo=repo,
        issue_number=123,
        labels=["needs-review", "triaged"],
        scope="test",
        trust_zone="automation",
    )


def _invoke_github_merge(ctx: WrapperContext, repo: str) -> dict[str, object]:
    return merge_pull_request(
        ctx=ctx,
        repo=repo,
        pull_number=123,
        merge_method="squash",
        commit_title="Merge ready",
        commit_message="automerge",
        scope="test",
        trust_zone="automation",
    )


def _execute_webhook(ctx: WrapperContext, op_id: str) -> dict[str, object]:
    return execute_webhook_approved(ctx=ctx, op_id=op_id)


def _execute_github(ctx: WrapperContext, op_id: str) -> dict[str, object]:
    return execute_github_approved(ctx=ctx, op_id=op_id)


SPECS = (
    WrapperSpec(
        name="webhook",
        action="webhook.post",
        category="external_write",
        allowlist_key="webhook_url",
        allowed_input="https://example.internal/hooks/deploy",
        denied_input="https://evil.invalid/hooks/deploy",
        normalized_target="https://example.internal/hooks/deploy",
        kind="webhook_post",
        payload={"ok": True},
        invoke=_invoke_webhook,
        execute=_execute_webhook,
        allowlist_value=_identity,
    ),
    WrapperSpec(
        name="github-comment",
        action="github.comment.create",
        category="external_write",
        allowlist_key="github_repo",
        allowed_input="example/repo",
        denied_input="evil/repo",
        normalized_target="github://example/repo/issues/123",
        kind="github_comment",
        payload={"body": "hello"},
        invoke=_invoke_github_comment,
        execute=_execute_github,
        allowlist_value=_identity,
        env={"GITHUB_TOKEN": "test-token"},
    ),
    WrapperSpec(
        name="github-labels",
        action="github.issue.labels.add",
        category="external_write",
        allowlist_key="github_repo",
        allowed_input="example/repo",
        denied_input="evil/repo",
        normalized_target="github://example/repo/issues/123",
        kind="github_issue_labels",
        payload={"labels": ["needs-review", "triaged"]},
        invoke=_invoke_github_labels,
        execute=_execute_github,
        allowlist_value=_identity,
        env={"GITHUB_TOKEN": "test-token", "CLAWOPS_HTTP_RETRY_MODE": "safe"},
    ),
    WrapperSpec(
        name="github-merge",
        action="github.pull_request.merge",
        category="irreversible",
        allowlist_key="github_repo",
        allowed_input="example/repo",
        denied_input="evil/repo",
        normalized_target="github://example/repo/pulls/123/merge",
        kind="github_pull_merge",
        payload={
            "merge_method": "squash",
            "commit_title": "Merge ready",
            "commit_message": "automerge",
        },
        invoke=_invoke_github_merge,
        execute=_execute_github,
        allowlist_value=_identity,
        env={"GITHUB_TOKEN": "test-token"},
    ),
)


def build_context(
    tmp_path: pathlib.Path,
    spec: WrapperSpec,
    *,
    require_approval: bool,
    dry_run: bool = False,
) -> tuple[WrapperContext, OperationJournal]:
    """Create a wrapper context and journal for a given wrapper spec."""
    policy_path = tmp_path / f"{spec.name}-policy.yaml"
    policy: dict[str, object] = {
        "defaults": {"decision": "allow"},
        "zones": {
            "automation": {
                "allow_actions": [spec.action],
                "allow_categories": [spec.category],
            }
        },
        "allowlists": {
            spec.allowlist_key: [spec.allowlist_value(spec.allowed_input)],
        },
    }
    if require_approval:
        policy["approval"] = {"require_for_actions": [spec.action]}
    write_policy_file(policy_path, policy)

    journal = create_journal(tmp_path / f"{spec.name}-journal.sqlite")
    ctx = WrapperContext(
        policy_engine=PolicyEngine.from_file(policy_path), journal=journal, dry_run=dry_run
    )
    return ctx, journal


def configure_wrapper_environment(spec: WrapperSpec, test_context: TestContext) -> None:
    """Set per-wrapper environment variables for one test."""
    if spec.env:
        test_context.env.update(spec.env)


def allow_decision_json() -> str:
    """Return a canonical allow decision payload."""
    return json.dumps(
        {
            "decision": "allow",
            "matched_rules": [],
            "reasons": [],
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def expected_failure_attempts(spec: WrapperSpec) -> int:
    """Return expected request attempts for a wrapper failure path."""
    return 3 if spec.name == "github-labels" else 1


def expected_failure_retryable(spec: WrapperSpec) -> bool:
    """Return whether a wrapper failure should be tagged retryable."""
    return spec.name == "github-labels"
