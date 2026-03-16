# QMD worker notes

QMD-backed memory retrieval is enabled by the default rendered OpenClaw config.

The standard bootstrap path provisions QMD automatically. Re-run the bootstrap helper only when the backend is missing or needs repair:

```bash
./scripts/bootstrap/bootstrap_qmd.sh
```

Prewarm:
```bash
./scripts/workers/prewarm_qmd.sh
```
