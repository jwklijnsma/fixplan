# fixplan: common tasks. `make` or `make help` lists them.

PYTHON ?= python3
BUMP   ?= patch
IMAGE  ?= fixplan:dev

.DEFAULT_GOAL := help

.PHONY: help install test ui-test docs serve docker version release release-patch release-minor release-major release-dry-run

help: ## show this list
	@grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-16s %s\n", $$1, $$2}'
	@echo
	@echo "  release examples: make release | make release BUMP=minor | make release VERSION=1.0.0"

install: ## install for development (editable, with the linters and pytest)
	$(PYTHON) -m pip install -e ".[dev,lint]"

test: ## run the test suite
	$(PYTHON) -m pytest -q

serve: ## start the web UI on localhost:8766
	$(PYTHON) -m fixplan serve --open

ui-test: ## browser test of the web UI (needs: pip install -e ".[ui]" && playwright install chromium)
	$(PYTHON) -m pytest -q tests/test_ui.py

docs: ## preview the docs site on http://localhost:8000 (MkDocs Material)
	$(PYTHON) -m pip install -q -r requirements-docs.txt && mkdocs serve

docker: ## build the Docker image locally (fixplan:dev)
	docker build -t $(IMAGE) .

version: ## print the current version
	@sed -n 's/^__version__ = "\(.*\)"$$/\1/p' src/fixplan/__init__.py

release: ## start the release workflow on GitHub (BUMP=patch|minor|major or VERSION=X.Y.Z)
	@command -v gh >/dev/null || { echo "make release needs the GitHub CLI: https://cli.github.com"; exit 1; }
	gh workflow run release.yml --ref main -f bump=$(BUMP) $(if $(VERSION),-f version=$(VERSION)) $(if $(DRY_RUN),-f dry_run=true)
	@echo "Started. Following the run (Ctrl-C stops watching, not the release)…"
	@sleep 5
	@gh run watch --exit-status "$$(gh run list --workflow=release.yml --limit 1 --json databaseId --jq '.[0].databaseId')"

release-patch: ## release X.Y.Z+1
	@$(MAKE) --no-print-directory release BUMP=patch

release-minor: ## release X.Y+1.0
	@$(MAKE) --no-print-directory release BUMP=minor

release-major: ## release X+1.0.0
	@$(MAKE) --no-print-directory release BUMP=major

release-dry-run: ## rehearse the release on GitHub: test and build everything, publish nothing
	@$(MAKE) --no-print-directory release DRY_RUN=1
