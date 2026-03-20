DEFAULT_GOAL := help

PYTHON ?= python3
PIP ?= $(PYTHON) -m pip
UV ?= uv
RUN ?= $(UV) run
PRE_COMMIT ?= $(RUN) pre-commit
PYTEST ?= $(UV) run --locked --extra dev pytest
DEV_SYNC_FLAGS ?= --locked
CONTEXT_CONFIG ?= platform/configs/context/context-service.yaml
REPO_DIR ?= .
RUNS_DIR ?=
SETUP_ARGS ?=
DOCTOR_ARGS ?=

.PHONY: help install setup doctor dev fmt lint imports typecheck actionlint shellcheck precommit dev-check test compile start-sidecars stop-sidecars render-config verify context-index run-harness backup

help: ## Show available targets.
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z0-9_.-]+:.*## / {printf "%-16s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Sync the managed project environment.
	$(UV) sync $(DEV_SYNC_FLAGS)

setup: ## Run the guided StrongClaw setup workflow.
	PYTHONPATH=src $(RUN) clawops setup $(SETUP_ARGS)

doctor: ## Run the deep StrongClaw readiness scan.
	PYTHONPATH=src $(RUN) clawops doctor $(DOCTOR_ARGS)

dev: ## Sync the locked dev environment and install pre-commit hooks.
	$(UV) sync $(DEV_SYNC_FLAGS) --extra dev
	$(PRE_COMMIT) install --install-hooks

fmt: ## Apply import sorting, lint autofixes, and formatting.
	$(RUN) isort src tests
	$(RUN) ruff check --fix src tests
	$(RUN) black src tests

lint: ## Run Ruff in check mode.
	$(RUN) ruff check src tests

imports: ## Verify import ordering.
	$(RUN) isort --check-only src tests

typecheck: ## Run mypy and pyright.
	$(RUN) mypy
	$(RUN) pyright

actionlint: ## Run the actionlint pre-commit hook.
	$(PRE_COMMIT) run actionlint --all-files

shellcheck: ## Run ShellCheck through the pre-commit hook.
	$(PRE_COMMIT) run shellcheck --all-files

precommit: ## Apply mutating hooks, then verify the full pre-commit stack.
	$(PRE_COMMIT) run end-of-file-fixer --all-files
	$(PRE_COMMIT) run trailing-whitespace --all-files
	$(PRE_COMMIT) run isort --all-files
	$(PRE_COMMIT) run ruff-check --all-files
	$(PRE_COMMIT) run black --all-files
	$(PRE_COMMIT) run shellcheck --all-files
	$(PRE_COMMIT) run --all-files

dev-check: precommit ## Run pre-commit, tests, and a compile smoke.
	$(PYTEST) -q
	$(RUN) python -m compileall -q src tests

test: ## Run pytest in the managed dev environment.
	PYTHONPATH=src $(PYTEST) -q

compile: ## Compile source and tests in the managed dev environment.
	PYTHONPATH=src $(RUN) python -m compileall -q src tests

render-config: ## Render the OpenClaw config bundle.
	./scripts/bootstrap/render_openclaw_config.sh

start-sidecars: ## Launch the sidecar services.
	./scripts/ops/launch_sidecars_with_varlock.sh

stop-sidecars: ## Stop the sidecar services.
	./scripts/ops/stop_sidecars.sh

verify: ## Run the baseline verification flow.
	./scripts/bootstrap/verify_baseline.sh

context-index: ## Build the repo lexical context index.
	$(RUN) clawops context index --config $(CONTEXT_CONFIG) --repo $(REPO_DIR)

run-harness: ## Execute the harness smoke suite.
	./scripts/bootstrap/run_harness_smoke.sh $(RUNS_DIR)

backup: ## Create a recovery backup bundle.
	./scripts/recovery/backup_create.sh
