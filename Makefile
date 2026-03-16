PYTHON ?= python3
PIP ?= $(PYTHON) -m pip
UV ?= uv

.PHONY: install dev fmt lint imports typecheck actionlint precommit dev-check test compile start-sidecars stop-sidecars render-config verify context-index run-harness backup

install:
	$(PIP) install -e .

dev:
	$(UV) sync --extra dev
	$(UV) run pre-commit install --install-hooks

fmt:
	$(UV) run isort src tests
	$(UV) run ruff check --fix src tests
	$(UV) run black src tests

lint:
	$(UV) run ruff check src tests

imports:
	$(UV) run isort --check-only src tests

typecheck:
	$(UV) run mypy
	$(UV) run pyright

actionlint:
	$(UV) run pre-commit run actionlint --all-files

precommit:
	$(UV) run pre-commit run --all-files || $(UV) run pre-commit run --all-files

dev-check: precommit
	$(UV) run pytest -q
	$(UV) run python -m compileall -q src tests

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
	./scripts/bootstrap/run_harness_smoke.sh ./.runs

backup:
	./scripts/recovery/backup_create.sh
