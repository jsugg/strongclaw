# Testing Operations

## Canonical Commands

- Full suite: `uv run pytest -q`
- Unit lane: `uv run pytest -q -m unit`
- Integration lane: `uv run pytest -q -m integration`
- Contract lane: `uv run pytest -q -m contract`
- Framework lane: `uv run pytest -q -m framework tests/suites/framework`
- E2E lane: `uv run pytest -q -m e2e`
- Hypermemory lane: `uv run pytest -q -m hypermemory`
- Qdrant lane: `uv run pytest -q -m "hypermemory and qdrant"`

## DualMode Commands

- Force Qdrant mock mode: `uv run pytest -q -m "hypermemory and qdrant" --mock qdrant`
- Force Qdrant real mode: `QDRANT_TEST_MODE=real uv run pytest -q -m "hypermemory and qdrant"`
- Provide an existing real endpoint: `TEST_QDRANT_URL=http://127.0.0.1:6333 uv run pytest -q -m "hypermemory and qdrant"`

## Governance Checks

- Framework contracts: `uv run pytest -q tests/suites/contracts/testing`
- Fixture analysis: `uv run python -m tests.utils.scripts.analyze_fixtures --json`
- Safe timeout wrapper: `uv run python -m tests.utils.scripts.pytest_safe --timeout 600 -q -m integration`

## Adding Coverage

Add a new test in the suite that matches its behavior:
- `tests/suites/unit/...` for isolated behavior
- `tests/suites/integration/...` for cross-module or service-backed behavior
- `tests/suites/contracts/...` for repository and governance rules
- `tests/suites/framework/...` for explicit pytest-framework self-checks
- `tests/suites/e2e/...` for black-box CLI and workflow-shaped coverage

Add a new fixture when pytest injection is the public entrypoint.
Add a new helper when the logic should be reusable outside fixture setup.
Import reusable support code from `tests.utils.helpers`, not from `tests.fixtures`.

Capability markers stay module-local.
Structural markers come from the suite path layout and should not be added manually.
