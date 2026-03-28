# Testing Operations

## Canonical Commands

- Full suite: `uv run pytest -q`
- Unit lane: `uv run pytest -q -m unit`
- Integration lane: `uv run pytest -q -m integration`
- E2E lane: `uv run pytest -q -m e2e`
- Contract lane: `uv run pytest -q -m contract`
- Framework lane only:
  `uv run pytest -q -m framework tests/suites/contracts/testing/framework`
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

## Contributor Dev Shell

- Source the repo-local developer environment:
  `source scripts/dev-env.sh`
- Or open a prepared shell in one step:
  `make dev-shell`
- After either flow, use `clawops-dev ...` to run the repo checkout against
  repo-backed assets without changing the default installed/runtime behavior.

## Common Triage

- If the monkeypatch governance contract fails, migrate the test to
  `TestContext` instead of expanding the allowlist by default.
- If docs or links move, rerun the repository docs contracts so relative-link
  and layout drift is caught early.
- If a test needs reusable setup by fixture injection, add or extend a fixture.
- If it needs reusable non-fixture logic, move that code into
`tests/utils/helpers/` or `tests/utils/<some dir>/<some file>`. Depending on the nature of the util's logic you need/if it's more than a helper, put it somewhere else under the `tests/utils/` tree where it fits best (e.g., factories, builders, scripts, validators, data seeders, logging utils, polling code, etc, shouldn't live in `tests/utils/helpers/`. Use your common sense and follow best practices.
- Note: If the code is **not** a util but more like a framework core functionality instead, it might need to go under other folder under `tests/`. E.g., test data (for instance, DB dumps), settings (framework-wide settings), etc.
