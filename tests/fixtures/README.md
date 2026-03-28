# Test Fixture Layout

Contributor-facing testing rules live under
[`docs/testing/`](../../docs/testing/README.md).
This file is only for fixture-package-specific guidance.

`tests/plugins/infrastructure/` contains the structural runtime for every test.
It owns the universal `TestContext`, framework env injection, tracked cwd
changes, patch teardown, profile handling, and infrastructure-owned pytest
hooks.

`tests/fixtures/` contains domain-facing pytest activation surfaces.
The package is loaded through root `tests/conftest.py`, then aggregated by
package-level `pytest_plugins` registries:

- `tests.plugins.infrastructure`
- `tests.fixtures`
- `tests.fixtures.core`
- `tests.fixtures.platform`
- `tests.fixtures.hypermemory`

Leaf fixture modules should stay thin and expose fixtures that are meaningful
to multiple tests or suites.
Tests should use fixture injection instead of importing from `tests.fixtures`.

Use [../../docs/testing/authoring.md](../../docs/testing/authoring.md) for
lane placement, governance rules, and `TestContext` guidance.
