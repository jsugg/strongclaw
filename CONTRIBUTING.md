# Contributing

## Local checks

Use the locked `uv` environment from the repo root.

Fast checks:

```bash
make lint
make imports
make typecheck
make test-contracts
```

Before review, run the repository quality surface when practical:

```bash
make ci
```

`make ci` may update local report artifacts such as coverage XML and
`.tmp/launch-readiness`, but it does not run mutating format hooks or edit source
files. `make precommit` and `make dev-check` run mutating format/import hooks.
Keep those separate from check-only validation when you need a non-mutating
source audit.

Hosted Docker, macOS fresh-host, live model, and release acceptance coverage
remain CI-owned; do not make those mandatory local pre-push steps.

## Pull requests

- Keep CI/CD workflow logic thin; put nontrivial behavior in `tests/scripts/`
  helpers with unit or contract coverage.
- Preserve `.github/required-checks.json`: the only required branch-protection
  API status context is `Verdict`.
- Update docs and contract tests in the same change when touching workflows,
  release artifacts, security policy, dependency manifests, or packaged runtime
  assets.
- `CODEOWNERS` intentionally points to `@jsugg` only while the repo has one
  maintainer. Non-admin contributions require that CODEOWNER's approval;
  `enforceAdmins=false` preserves the documented solo-admin break-glass path.
- Do not patch vendored third-party `platform/plugins/memory-lancedb-pro`.
  Strongclaw maintains only `platform/plugins/strongclaw-hypermemory`; vendor
  changes must arrive through an explicit upstream pin/update.
