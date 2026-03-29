DEFAULT_GOAL := help

PYTHON ?= python3
PIP ?= $(PYTHON) -m pip
UV ?= uv
RUN ?= $(UV) run
PRE_COMMIT ?= $(RUN) pre-commit
PYTEST ?= $(UV) run --locked pytest
DEV_SYNC_FLAGS ?= --locked
CONTEXT_CONFIG ?= platform/configs/context/codebase.yaml
CONTEXT_SCALE ?= small
REPO_DIR ?= .
RUNS_DIR ?=
SETUP_ARGS ?=
DOCTOR_ARGS ?=
PREFERRED_PYTHON := $(shell $(PYTHON) src/clawops/platform_compat.py --field preferred_project_python_version 2>/dev/null)
UV_SYNC := $(UV) sync $(if $(PREFERRED_PYTHON),--python $(PREFERRED_PYTHON),)

.PHONY: help install setup doctor dev dev-shell fmt lint imports typecheck actionlint shellcheck precommit dev-check test test-unit test-integration test-contracts test-framework test-e2e test-hypermemory test-qdrant test-all test-governance compile start-sidecars stop-sidecars render-config verify context-index run-harness backup

help: ## Show available targets.
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z0-9_.-]+:.*## / {printf "%-16s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Sync the managed project environment.
	$(UV_SYNC) $(DEV_SYNC_FLAGS)

setup: ## Run the guided StrongClaw setup workflow.
	$(RUN) clawops setup $(SETUP_ARGS)

doctor: ## Run the deep StrongClaw readiness scan.
	$(RUN) clawops doctor $(DOCTOR_ARGS)

dev: ## Sync the locked dev environment and install pre-commit hooks.
	$(UV_SYNC) $(DEV_SYNC_FLAGS)
	$(PRE_COMMIT) install --install-hooks

dev-shell: install ## Open an interactive dev shell with repo-backed isolated runtime enabled.
	@bash -lc 'source "$(CURDIR)/scripts/dev-env.sh" && cd "$(CURDIR)" && exec "$${SHELL:-/bin/bash}" -i'

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
	$(PYTEST) -q

test-unit: ## Run the unit pytest lane.
	$(PYTEST) -q -m unit

test-integration: ## Run the integration pytest lane.
	$(PYTEST) -q -m integration

test-contracts: ## Run the contract pytest lane.
	$(PYTEST) -q -m contract

test-framework: ## Run the explicit pytest framework lane.
	$(PYTEST) -q -m framework tests/suites/contracts/testing/framework

test-e2e: ## Run the end-to-end pytest lane.
	$(PYTEST) -q -m e2e

test-hypermemory: ## Run the hypermemory pytest lane.
	$(PYTEST) -q -m hypermemory

test-qdrant: ## Run the Qdrant-backed hypermemory pytest lane.
	QDRANT_TEST_MODE=real $(PYTEST) -q -m "hypermemory and qdrant"

test-all: ## Run the full pytest suite.
	$(PYTEST) -q

test-governance: ## Run testing-governance contracts and fixture analysis.
	$(PYTEST) -q tests/suites/contracts/testing
	$(RUN) python -m tests.utils.scripts.analyze_fixtures --json

compile: ## Compile source and tests in the managed dev environment.
	$(RUN) python -m compileall -q src tests

render-config: ## Render the OpenClaw config bundle.
	./bin/clawops-dev render-openclaw-config

start-sidecars: ## Launch the sidecar services.
	$(RUN) clawops ops sidecars up

stop-sidecars: ## Stop the sidecar services.
	$(RUN) clawops ops sidecars down

verify: ## Run the baseline verification flow.
	$(RUN) clawops baseline verify

context-index: ## Build the repo lexical context index.
	$(RUN) clawops context codebase index --scale $(CONTEXT_SCALE) --config $(CONTEXT_CONFIG) --repo $(REPO_DIR)

run-harness: ## Execute the harness smoke suite.
	$(RUN) clawops baseline harness-smoke --runs-dir $(if $(RUNS_DIR),$(RUNS_DIR),.tmp/harness)

backup: ## Create a recovery backup bundle.
	$(RUN) clawops recovery backup-create
