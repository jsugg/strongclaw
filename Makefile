PYTHON ?= python3
PIP ?= $(PYTHON) -m pip

.PHONY: install test compile start-sidecars stop-sidecars render-config verify context-index run-harness backup

install:
	$(PIP) install -e .

test:
	PYTHONPATH=src pytest -q

compile:
	PYTHONPATH=src $(PYTHON) -m compileall -q src tests

render-config:
	./scripts/bootstrap/render_openclaw_config.sh

start-sidecars:
	./scripts/ops/launch_sidecars_with_varlock.sh

stop-sidecars:
	./scripts/ops/stop_sidecars.sh

verify:
	./scripts/bootstrap/verify_baseline.sh

context-index:
	clawops context index --config platform/configs/context/context-service.yaml --repo .

run-harness:
	clawops harness run --suite platform/configs/harness/policy_regressions.yaml --output ./.runs/policy.jsonl

backup:
	./scripts/recovery/backup_create.sh
