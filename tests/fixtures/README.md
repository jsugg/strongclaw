# Test Fixture Layout

`tests/plugins/infrastructure/` contains the structural runtime for every test.
It owns the universal `TestContext`, framework env injection, tracked cwd changes, patch teardown,
profile handling, and infrastructure-owned pytest hooks.

`tests/fixtures/` contains domain-facing pytest activation surfaces.
The package is loaded through root `tests/conftest.py`, then aggregated by package-level
`pytest_plugins` registries:
- `tests.plugins.infrastructure`
- `tests.fixtures`
- `tests.fixtures.core`
- `tests.fixtures.platform`
- `tests.fixtures.hypermemory`

Leaf fixture modules should stay thin and expose fixtures that are meaningful to multiple tests or suites.
Tests should use fixture injection instead of importing from `tests.fixtures`.

`tests/utils/helpers/` contains reusable builders, subsystem runtimes, and support code.
Move implementation detail there when it is not itself structural infrastructure or a pytest fixture.
Import helper functions, fakes, and types from `tests.utils.helpers.*`.

Add a new fixture module when:
- A set of related fixtures belongs to a clear subsystem boundary.
- The fixture is reused across multiple tests or suites.
- The module can stay focused without becoming a general grab bag.

Add a new helper module when:
- The code constructs data, manages lifecycle, or performs AST/runtime analysis.
- The logic can be reused outside pytest fixture setup.
- The module can be tested directly as a normal Python API.

Add a contract test when:
- A repository policy must not silently drift.
- Fixture or helper placement matters for maintainability.
- CI and local test invocation rules need enforcement.

Three-layer pattern:
- Infrastructure modules own universal test runtime behavior.
- Fixture modules expose domain-facing pytest fixtures only.
- Helper modules hold typed implementation logic and subsystem runtime helpers.

Example:
- `tests/fixtures/hypermemory/workspace.py` exports `hypermemory_workspace_factory`.
- `tests/utils/helpers/hypermemory.py` implements the workspace builders and fake backends.
