# Test Fixture Layout

`tests/fixtures/` contains pytest activation surfaces.
Each module should stay thin and expose fixtures that are meaningful to multiple tests or suites.
The package is loaded through root `tests/conftest.py`; tests should use fixture injection instead of importing from `tests.fixtures`.

`tests/utils/helpers/` contains reusable builders, runtimes, and support code.
Move implementation detail there when it is not itself a pytest fixture.
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

Two-layer pattern:
- Fixture modules expose pytest fixtures only.
- Helper modules hold typed implementation logic and runtime helpers.

Example:
- `tests/fixtures/hypermemory.py` exports `hypermemory_workspace_factory`.
- `tests/utils/helpers/hypermemory.py` implements the workspace builders and fake backends.
