# Authoring Tests

## Lane Model

Strongclaw uses four primary default pytest lanes:

- `unit`: isolated behavior and small-surface regression checks
- `integration`: cross-module or service-shaped behavior
- `contracts`: repository policies, and CI/test-governance rules
- `e2e`: black-box CLI and workflow-shaped orchestration coverage

The repository also maintains an explicit `framework` lane for pytest-framework
self-checks. Framework tests live under
`tests/suites/contracts/testing/framework/` and are excluded from default runs.
Use that lane only for pytest bootstrap and plugin-registration behavior.

Monkeypatch governance is a default contract, not a framework-only self-check.
The direct-monkeypatch contract lives under `tests/suites/contracts/testing/`
so ordinary pytest runs fail when new unmanaged `monkeypatch` usage appears in
suite code.

Capability markers are additive and remain module-local:

- `hypermemory`
- `qdrant`
- `network_local`

Structural markers are assigned from the suite path layout in
`tests/conftest.py`.

## Placement Rules

Add a new test in the suite that matches its behavior:

- `tests/suites/unit/...` for isolated behavior
- `tests/suites/integration/...` for cross-module or service-backed behavior
- `tests/suites/contracts/...` for repository and governance rules
- `tests/suites/contracts/testing/framework/...` for explicit pytest-framework
  self-checks
- `tests/suites/e2e/...` for black-box CLI and workflow-shaped coverage

Prefer a dedicated subsystem directory inside each lane when one already
exists.

## Runtime Boundaries

Use `tests/plugins/infrastructure/` for structural test runtime behavior such
as `TestContext`, environment management, patch management, profile
registration, and framework-owned pytest hooks.

Use `tests/fixtures/` for domain-facing pytest fixture plugins only.

Use `tests/utils/helpers/` for builders, subsystem runtimes, AST tooling, and
other reusable support code that is not itself a fixture or pytest framework
surface.

Tests consume fixtures by name through pytest injection and should not import
from `tests.fixtures`.

## Preferred Authoring Path

Environment mutation, working-directory changes, and patching should flow
through the infrastructure runtime:

- use `test_context.patch.patch(...)` or `patch_object(...)`
- use `test_context.env.set(...)`, `remove(...)`, `update(...)`, or
  `prepend_path(...)`
- use `test_context.chdir(...)` for temporary working-directory changes

Do not add new ordinary-suite tests that depend on raw `monkeypatch` unless the
test is an explicit governed exception.

## Service Resolution

Service-backed tests resolve mode with this precedence:

1. CLI: `--mock <service>`
2. Environment: `<SERVICE>_TEST_MODE`
3. Marker kwargs: `@pytest.mark.<service>(mode="real")`
4. Default mode

Current service support:

- `qdrant`

## Governance

Framework policy lives under `tests/suites/contracts/testing/`.
Add a contract test when a rule must stay true even if the implementation
changes.

Pytest-framework registration and bootstrap topology lives under
`tests/suites/contracts/testing/framework/`.
