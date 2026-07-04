# Security Policy

## Supported surface

Security fixes cover the current `main` branch and the GitHub Release assets
generated from protected `v*` tags. Strongclaw does not publish to PyPI or a
hosted production deployment path today.

## Reporting a vulnerability

Use GitHub private vulnerability reporting or contact the maintainer listed in
`pyproject.toml`. Do not put secrets, live credentials, exploit payloads, or
private environment dumps in public issues.

Useful context for a report:

- affected component or workflow
- expected vs. observed behavior
- minimal reproduction steps
- whether the issue affects release artifacts, CI/CD policy, packaged runtime
  assets, plugins, or local operator secrets

## Security model

Read these docs before changing sensitive areas:

- [Security model](platform/docs/SECURITY_MODEL.md)
- [Secrets and environment](platform/docs/SECRETS_AND_ENV.md)
- [CI and security](platform/docs/CI_AND_SECURITY.md)
- [Release break-glass runbook](platform/docs/runbooks/release-break-glass.md)
