# Testing Docs

Strongclaw keeps contributor-facing testing documentation in this directory. This is the canonical source of truth for how tests are organized, authored, and run inside the repository.

Testing docs do not ship in `src/clawops/assets/...` unless a packaged runtime flow actually needs them.

## Start Here

- Read [authoring.md](authoring.md) when you are writing new tests, moving
existing coverage, or touching the pytest framework.
- Read [operations.md](operations.md) when you are running tests locally,
debugging CI, or checking governance commands.
- Read [../../tests/fixtures/README.md](../../tests/fixtures/README.md) for
fixture-package-specific guidance only.

## Canonical Rules

- Put tests under `tests/suites/{unit,integration,contracts,e2e,...}` and place
them in a subsystem-specific subdirectory when one exists.
- Treat `TestContext` as the default path for patching, environment mutation,
and temporary working-directory changes.
- Treat raw pytest `monkeypatch` as an exception-only tool: use it only when
`TestContext` patch/env APIs are clearly insufficient, and keep every such file
governed by the direct-monkeypatch contract.
- Keep pytest framework rules and contributor docs together under
`docs/testing/` instead of scattering them across packaged asset trees.
