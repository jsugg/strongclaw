# Release break-glass runbook

This repo is currently maintained by `jsugg` alone. Release controls therefore
must preserve an audited admin escape hatch instead of requiring a second human
reviewer that does not exist yet.

## Normal release path

1. Land release changes on `main` after the strict `Verdict` check passes.
2. As `jsugg`, use the ruleset bypass to create a `v*` tag that matches the
   package version. Other actors cannot create matching tags; nobody may update
   or delete them through the normal path.
3. Let the merged `main` commit receive a successful `Verdict` check.
4. Let the release workflow enter the `release` environment.
5. Wait out the 15-minute environment timer.
6. Publish only if the release tag preflight passes:
   - tag matches package metadata
   - tag commit is reachable from `origin/main`
   - tag commit has a successful `Verdict` check run
   - release metadata, checksums, SBOM, and attestations are generated

## Break-glass conditions

Use admin bypass only when waiting would materially harm users or the release
process itself is blocked by GitHub/service failure. Do not use it to skip a
known failing `Verdict`, failed artifact verification, or an unexplained tag
preflight failure.

Acceptable examples:

- GitHub environment wait timer is stuck or delayed beyond the release window.
- GitHub deployment environment UI/API is degraded, but all local and CI release
  gates already passed.
- A bad release tag must be superseded by a new tag after documenting why the
  old tag is unusable.

## Required audit note

After any bypass, open or update an issue with:

- reason for bypass
- actor: `jsugg`
- release tag and commit SHA
- workflow run URL
- artifact manifest hash
- user impact
- follow-up corrective action

## Explicit non-goals while solo

Do not enable these until a second trusted maintainer exists:

- required release-environment reviewer
- prevent self-review on an environment with only `jsugg` as reviewer
- admin-enforced rules with no tested bypass path

Pull-request approval and CODEOWNER review remain required for non-admin
contributors; they are not admin-enforced while `jsugg` is the sole admin.
