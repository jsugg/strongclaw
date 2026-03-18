# Strongclaw Remaining Gaps Implementation Plan

## Scope

This plan addresses the real remaining gaps in the current Strongclaw worktree after auditing the codebase against `plan.md`.

Verified current state:

- `PYTHONPATH=src pytest -q` passes with 189 tests on the current working tree.
- JSON5 overlays, Jira env cleanup, coverage summaries, release attestations, and baseline wrapper hardening are already implemented.
- The real remaining work is narrower:
  - finish wrapper transport policy tuning;
  - remove the remaining `ContextService` indexing inefficiency;
  - clean stale old-branding references;
  - re-baseline the roadmap so it matches the code.

## Goals

1. Enable retry behavior only where it is safe and deliberate.
2. Preserve wrapper replay and journal guarantees.
3. Remove the per-file metadata lookup bottleneck from `ContextService.index_with_stats()`.
4. Eliminate the last shipped references to the obsolete repository identity.
5. Replace the stale roadmap with one that reflects the current implementation.

## Workstream 1: Wrapper Transport Completion

### Current state

- Retry primitives already exist in `src/clawops/wrappers/base.py`.
- `JsonHttpClient` already supports bounded retries when a non-default `RetryPolicy` is passed.
- Structured transport and HTTP failure classes already exist.
- All shipped wrapper endpoints still use `RetryPolicy.no_retry(...)`.
- `JsonHttpClient` still uses a single flat timeout integer.
- Wrapper results expose flat fields such as `error_type`, `retryable`, and `request_attempts`, but not a nested `error` object.

### Files

- `src/clawops/wrappers/base.py`
- `src/clawops/wrappers/github.py`
- `src/clawops/wrappers/webhook.py`
- `tests/test_wrappers.py`

### Implementation

1. Introduce a small timeout configuration type in `base.py`.
   Use a shape such as `HttpTimeouts(connect_seconds, read_seconds)`.
   Keep backward compatibility so existing `JsonHttpClient(timeout=30)` call sites continue to work.

2. Update `JsonHttpClient.request()` to normalize timeout input.
   If the caller passes an integer, keep existing behavior.
   If the caller passes `HttpTimeouts`, convert it to the `(connect, read)` tuple expected by `requests`.

3. Keep unsafe endpoints on explicit no-retry policies.
   Leave these as no-retry by default:
   - `github.comment.create`
   - `github.pull_request.merge`
   - `webhook.post`

4. Enable safe retries only for GitHub label add.
   Change `LABELS_RETRY_POLICY` to a bounded retry policy such as:
   - `max_attempts=3`
   - retryable status codes `{429, 502, 503, 504}`
   - small base delay and jitter

5. Extend the wrapper result envelope without breaking callers.
   Preserve the current flat fields:
   - `error_type`
   - `retryable`
   - `request_attempts`
   Add a nested `error` mapping with stable keys such as:
   - `type`
   - `message`
   - `status_code`
   - `retryable`
   - `request_method`
   - `request_url`
   - `request_attempts`

6. Add best-effort header extraction for observability.
   Capture `Retry-After` and GitHub request IDs when present.
   Return them in the immediate wrapper result first.
   Only add journal columns if persistence across replay is required after review.

### Tests

Add or extend tests in `tests/test_wrappers.py` for:

- labels retrying once on a transient timeout and then succeeding;
- labels retrying on a retryable HTTP status and then succeeding;
- comments not retrying on timeout;
- merges not retrying on timeout or HTTP error;
- webhook remaining explicit no-retry;
- split timeout values being passed to `requests.request`;
- result envelopes containing both the legacy flat fields and the new nested `error` object.

### Acceptance criteria

- Label application retries only on configured transient failures.
- Comment, merge, and webhook operations never retry implicitly.
- Replay semantics remain unchanged: no duplicate side effects on reinvocation.
- Existing wrapper callers continue to work unchanged.
- New monitoring callers can read a structured `error` mapping.

### Risk

Medium. The main failure mode is widening retries onto non-idempotent endpoints. Keep policy assignment explicit at each endpoint constant.

## Workstream 2: ContextService Indexing Performance Completion

### Current state

- Incremental indexing is already correct.
- JSON stats output for `context index --json` already exists.
- The hot path still performs `SELECT ... WHERE path = ?` inside the per-file loop.

### Files

- `src/clawops/context_service.py`
- `tests/test_context_service.py`

### Implementation

1. Add a private helper that preloads current file metadata once at the start of `index_with_stats()`.
   Suggested mapping:
   - key: relative path
   - value: `(mtime_ns, size_bytes)`

2. Replace the per-file SQLite metadata lookup with a dictionary lookup.
   Keep the rest of the indexing loop unchanged.

3. Preserve existing external behavior.
   Do not change:
   - `IndexStats` field names
   - CLI text output
   - CLI JSON output
   - stale-file pruning behavior

4. Keep the optimization narrowly scoped.
   Do not combine this PR with schema changes or new output fields.

### Tests

Keep the existing correctness test and add:

- a SQL-trace regression test proving the unchanged second run no longer issues one metadata `SELECT` per file;
- a multi-file unchanged-repo test so the optimization is validated on more than one path;
- a stale-file deletion regression to ensure preloading does not break pruning.

### Acceptance criteria

- First-run and second-run stats remain semantically identical to current behavior.
- The second run performs one preload metadata query instead of one per file.
- Stale files are still removed correctly.

### Risk

Low to medium. The main risk is breaking stale-file deletion or accidentally skipping updates.

## Workstream 3: Naming Cleanup

### Current state

Top-level docs are mostly rebranded, but stale references remain in shipped surfaces:

- `scripts/bootstrap/bootstrap_fork.sh`
- `scripts/bootstrap/verify_baseline.sh`
- `platform/workers/acpx/README.md`

### Files

- `scripts/bootstrap/bootstrap_fork.sh`
- `scripts/bootstrap/verify_baseline.sh`
- `platform/workers/acpx/README.md`
- `tests/test_automation_surfaces.py`

### Implementation

1. Make `bootstrap_fork.sh` derive its default target from the repo root.
   Preferred default:
   - `"$ROOT/repo/upstream"`
   This is safer than hardcoding a legacy `$HOME/Projects/openclaw-platform-bootstrap/...` path.

2. Update the baseline verification memory query in `verify_baseline.sh`.
   Replace the legacy search string with the current identity already used in docs, such as `ClawOps`.

3. Update the ACPX README example path.
   Point it at the current repo-local `repo/upstream` path under the Strongclaw checkout.

4. Expand the branding regression tests.
   The current top-level doc parity checks should also scan these remaining script and worker surfaces.

### Tests

Extend `tests/test_automation_surfaces.py` so it fails if shipped scripts or worker docs still contain:

- `openclaw-platform-bootstrap`
- `OpenClaw Platform Bootstrap`

### Acceptance criteria

- No shipped scripts or worker docs reference the obsolete repository identity.
- The baseline verification search string matches the current project identity used elsewhere.

### Risk

Low.

## Workstream 4: Roadmap Re-Baselining

### Current state

`plan.md` still describes already-finished work as future work.

### Files

- `plan.md`
- `plan2.md`

### Implementation

1. Decide whether `plan.md` is meant to ship.
   If yes, rewrite it into a current-state roadmap.
   If no, remove it from the root before merge.

2. Preserve `plan2.md` as the corrected engineering plan for the verified remaining gaps.

3. Update the roadmap to remove already-landed items:
   - full JSON5 overlay parsing;
   - Jira env cleanup;
   - coverage summary basics;
   - basic wrapper retry/error infrastructure;
   - JSON stats output for `ContextService`.

4. Replace them with the real remaining deltas:
   - safe endpoint retry enablement;
   - split wrapper timeouts;
   - optional structured nested wrapper error envelope;
   - `ContextService` metadata preload optimization;
   - naming cleanup.

### Acceptance criteria

- The roadmap matches the codebase on the same commit.
- There is no root-level planning document that misstates the implementation status of completed work.

### Risk

Low.

## Recommended PR Order

1. PR-A: naming cleanup + roadmap re-baselining
2. PR-B: wrapper transport completion
3. PR-C: `ContextService` metadata preload optimization
4. PR-D: wrapper observability persistence, only if request IDs or retry metadata must be stored in the journal

## Verification Gates

Run these checks for the full sequence and after each relevant PR:

```bash
PYTHONPATH=src pytest -q
python -m compileall -q src tests
pytest -q tests/test_wrappers.py tests/test_context_service.py tests/test_automation_surfaces.py
rg -n "openclaw-platform-bootstrap|OpenClaw Platform Bootstrap" . -g '!uv.lock' -g '!.git/**'
```

Expected outcomes:

- tests remain green;
- no obsolete branding remains in shipped docs/scripts;
- wrapper replay semantics remain intact;
- `ContextService` incremental behavior remains correct while issuing fewer metadata lookups.

## Out of Scope

These items are not remaining gaps and should not be reopened as part of this plan:

- implementing JSON5 overlay parsing;
- removing Jira env keys;
- adding coverage summaries to the security workflow;
- adding release SBOM and provenance attestations;
- adding baseline wrapper retry primitives;
- adding `context index --json`.
