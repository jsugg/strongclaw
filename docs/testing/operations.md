# Testing Operations

## Canonical Commands

- Full suite: `uv run pytest -q`
- Unit lane: `uv run pytest -q -m unit`
- Integration lane: `uv run pytest -q -m integration`
- Contract lane: `uv run pytest -q -m contract`
- Framework lane only:
  `uv run pytest -q -m framework tests/suites/contracts/testing/framework`
- E2E lane: `uv run pytest -q -m e2e`
- Hypermemory lane: `uv run pytest -q -m hypermemory`
- Qdrant lane: `uv run pytest -q -m "hypermemory and qdrant"`

## Service Mode Commands

- Force Qdrant mock mode:
  `uv run pytest -q -m "hypermemory and qdrant" --mock qdrant`
- Force Qdrant real mode:
  `QDRANT_TEST_MODE=real uv run pytest -q -m "hypermemory and qdrant"`
- Provide an existing real endpoint:
  `TEST_QDRANT_URL=http://127.0.0.1:6333 uv run pytest -q -m "hypermemory and qdrant"`
- Override the managed Qdrant image:
  `TEST_QDRANT_IMAGE=ghcr.io/example/qdrant:test uv run pytest -q -m "hypermemory and qdrant"`

## Governance Checks

- Testing contracts: `uv run pytest -q tests/suites/contracts/testing`
- Fixture analysis:
  `uv run python -m tests.utils.scripts.analyze_fixtures --json`
- Safe timeout wrapper:
  `uv run python -m tests.utils.scripts.pytest_safe --timeout 600 -q -m integration`

## Common Triage

- If the monkeypatch governance contract fails, migrate the test to
  `TestContext` instead of expanding the allowlist by default.
- If docs or links move, rerun the repository docs contracts so relative-link
  and layout drift is caught early.
- If a test needs reusable setup by fixture injection, add or extend a fixture.
  If it needs reusable non-fixture logic, move that code into
  `tests/utils/helpers/`.
