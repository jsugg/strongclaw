# QMD worker notes

QMD is enabled only after the baseline is stable.

Install:
```bash
brew install bun sqlite
bun install -g https://github.com/tobi/qmd
which qmd
```

Prewarm:
```bash
./scripts/workers/prewarm_qmd.sh
```
