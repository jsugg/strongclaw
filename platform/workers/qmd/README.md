# QMD worker notes

QMD-backed memory retrieval is enabled by the default rendered OpenClaw config.

The standard bootstrap path provisions QMD automatically. Re-run the bootstrap helper only when the backend is missing or needs repair:

```bash
clawops config memory --set-profile openclaw-qmd --output ~/.openclaw/openclaw.json
```

Prewarm:
```bash
qmd status
```
