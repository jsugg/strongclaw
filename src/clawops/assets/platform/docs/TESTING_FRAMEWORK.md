# Testing Framework

## Lane Model

Strongclaw uses four primary default pytest lanes:
- `unit`: isolated behavior and small-surface regression checks
- `integration`: cross-module or service-shaped behavior
- `contracts`: repository policies, docs parity, and CI/test-governance rules
- `e2e`: black-box CLI and workflow-shaped orchestration coverage

The repository also maintains an explicit `framework` lane for pytest-framework self-checks.
Framework tests live under `tests/suites/contracts/testing/framework/` and are excluded from
default runs via the project pytest configuration. Run them explicitly when changing pytest
bootstrap, plugin registration, or framework governance behavior.

Capability markers are additive and remain module-local:
- `hypermemory`
- `qdrant`
- `network_local`

Structural markers are assigned from the suite path layout in `tests/conftest.py`.

## Fixture and Helper Split

Use `tests/plugins/infrastructure/` for structural test runtime behavior such as `TestContext`,
environment management, patch management, profile registration, and framework-owned pytest hooks.
Use `tests/fixtures/` for domain-facing pytest fixture plugins only.
Use `tests/utils/helpers/` for builders, subsystem runtime helpers, AST tooling, and other
non-structural support code.

Keep root `tests/conftest.py` lean:
- structural marker assignment
- shared plugin registration
- path fixtures that are meaningful across suites

Root `tests/conftest.py` registers the infrastructure runtime and the shared fixture package via
`pytest_plugins`.
`tests/plugins/infrastructure/__init__.py` owns framework CLI options, universal `TestContext`,
managed env injection, patch teardown, and named runtime profiles.
`tests/fixtures/__init__.py` aggregates domain packages, and domain package `__init__.py` files
aggregate their leaf fixture modules.
Tests consume fixtures by name through pytest injection and should not import from `tests.fixtures`.
Tests that need reusable builders, fakes, or types should import them from `tests.utils.helpers`.
Environment mutation and patching should flow through the infrastructure runtime, for example
`prepend_path`, `TestContext.env`, and `TestContext.patch`, instead of direct `monkeypatch`
usage in ordinary suite code.

## DualMode Service Resolution

Service-backed tests resolve mode with this precedence:
1. CLI: `--mock <service>`
2. Environment: `<SERVICE>_TEST_MODE`
3. Marker kwargs: `@pytest.mark.<service>(mode="real")`
4. Default mode

Current service support:
- `qdrant`

Examples:
- `uv run pytest -q -m unit`
- `uv run pytest -q -m "hypermemory and qdrant" --mock qdrant`
- `uv run pytest -q -m e2e`
- `uv run pytest -q -m framework tests/suites/contracts/testing/framework`
- `QDRANT_TEST_MODE=real uv run pytest -q -m "hypermemory and qdrant"`

## Adopted Patterns

The repository intentionally adopts a small subset of the AIOA testing architecture:
- worker-aware per-test identity
- deterministic tracked cleanup through `TestContext`
- universal `TestContext` creation for every test
- infrastructure-owned environment and patch isolation
- named infrastructure profiles for repeated runtime setup
- narrow runtime helpers for network and Qdrant integration coverage
- contract tests and static analysis for framework governance

## Rejected Patterns

The repository intentionally does not adopt these patterns at current scale:
- plugin dependency graphs
- a monolithic framework control plane in root `conftest.py`
- registry-heavy data factories
- pytest-embedded documentation generation
- parallel-safety abstractions before xdist is an actual requirement

## Growth Triggers

Revisit the design if any of these become true:
- more than one helper module grows past the line-count guardrail
- three or more service runtimes need ordered lifecycle orchestration
- xdist becomes part of the default CI matrix
- fixture ownership becomes ambiguous across multiple domains

## Governance Contracts

Framework policy lives under `tests/suites/contracts/testing/`.
Add a contract test when a rule must stay true even if the implementation changes.

Pytest-framework registration and bootstrap topology lives under
`tests/suites/contracts/testing/framework/`. Use that lane for assertions about recursive plugin
registration, explicit framework-only behavior, and other tests that should not run in the default
suite.

Current governance covers:
- root bootstrap shape
- workflow pytest invocation policy
- test-context cleanup invariants
- environment and patch isolation
- runtime helper behavior
- fixture-analysis health checks
- testing documentation presence
