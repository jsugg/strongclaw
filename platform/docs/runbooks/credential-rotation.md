# Credential rotation runbook

Rotate immediately on:
- pairing/bootstrap advisories
- token leakage
- plugin compromise
- browser-lab incident
- trust-boundary split or host migration

Steps:
1. update source-of-truth secret manager
2. update `.env` material
3. `varlock load`
4. restart sidecars / gateway
5. run verification
