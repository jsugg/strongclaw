# Testing Framework

## Lane Model

Strongclaw uses four primary pytest lanes:
- `unit`: isolated behavior and small-surface regression checks
- `integration`: cross-module or service-shaped behavior
- `contracts`: repository policies, docs parity, and CI/test-governance rules
- `e2e`: black-box CLI and workflow-shaped orchestration coverage

Capability markers are additive and remain module-local:
- `hypermemory`
- `qdrant`
- `network_local`

Structural markers are assigned from the suite path layout in `tests/conftest.py`.

## Fixture and Helper Split

Use `tests/fixtures/` for pytest fixture plugins only.
Use `tests/utils/helpers/` for builders, lifecycle helpers, AST tooling, runtime support, and any non-fixture API.

Keep root `tests/conftest.py` lean:
- structural marker assignment
- framework CLI options
- shared fixture plugin registration

Root `tests/conftest.py` registers the shared fixture plugins once via `pytest_plugins`.
Tests consume fixtures by name through pytest injection and should not import from `tests.fixtures`.
Tests that need reusable builders, fakes, or types should import them from `tests.utils.helpers`.

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
- `QDRANT_TEST_MODE=real uv run pytest -q -m "hypermemory and qdrant"`

## Adopted Patterns

The repository intentionally adopts a small subset of the AIOA testing architecture:
- worker-aware per-test identity
- deterministic tracked cleanup through `TestContext`
- explicit environment and patch isolation helpers
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

Current governance covers:
- root bootstrap shape
- workflow pytest invocation policy
- test-context cleanup invariants
- environment and patch isolation
- runtime helper behavior
- fixture-analysis health checks
- testing documentation presence
