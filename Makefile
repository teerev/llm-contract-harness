# ──────────────────────────────────────────────────────────────────────
# llm-contract-harness — Quickstart Makefile
# ──────────────────────────────────────────────────────────────────────
# Usage:
#   make           — show available targets
#   make demo      — run the full demo end-to-end (plan → build)
#   make clean     — remove demo artifacts
#
# Prerequisites: Python ≥ 3.11, git, OPENAI_API_KEY in environment.
# ──────────────────────────────────────────────────────────────────────

SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

# ── Variables ────────────────────────────────────────────────────────

VENV     := .venv
PY       := $(VENV)/bin/python
PIP      := $(VENV)/bin/pip
LLMCH    := $(VENV)/bin/llmch

REPO     := my-project
SPEC     := ./examples/hangman.txt
WODIR    := wo
BRANCH   := factory/demo

# Minimum required Python version (major.minor)
MIN_PY   := 3.11

# ── Preflight helpers ────────────────────────────────────────────────

define check_python
	@command -v python3 >/dev/null 2>&1 || \
		{ echo ""; echo "  ✘  python3 not found on PATH."; \
		  echo "     Install Python $(MIN_PY)+ and try again."; echo ""; exit 1; }
	@python3 -c "import sys; v=sys.version_info; exit(0 if (v.major,v.minor)>=(3,11) else 1)" 2>/dev/null || \
		{ echo ""; echo "  ✘  Python ≥ $(MIN_PY) required (found $$(python3 --version 2>&1))."; echo ""; exit 1; }
endef

define check_git
	@command -v git >/dev/null 2>&1 || \
		{ echo ""; echo "  ✘  git not found on PATH."; echo ""; exit 1; }
endef

define check_api_key
	@test -n "$${OPENAI_API_KEY:-}" || \
		{ echo ""; \
		  echo "  ✘  OPENAI_API_KEY is not set."; \
		  echo "     export OPENAI_API_KEY=sk-..."; echo ""; exit 1; }
endef

define banner
	@echo ""; echo "  ── $(1) ──────────────────────────────────────────────"; echo ""
endef

# ── Targets ──────────────────────────────────────────────────────────

.PHONY: help bootstrap demo-repo plan run demo web-build web-dev smoke clean

help: ## Show this help
	@echo ""
	@echo "  llm-contract-harness — Quickstart"
	@echo ""
	@echo "  Targets:"
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*##"}; {printf "    %-14s %s\n", $$1, $$2}'
	@echo ""
	@echo "  Full demo (one command):  make demo"
	@echo "  Prerequisites: Python ≥ $(MIN_PY), git, OPENAI_API_KEY"
	@echo ""

bootstrap: ## Create .venv, install package + dev deps
	$(call check_python)
	$(call banner,bootstrap)
	@if [ ! -f "$(PY)" ]; then \
		echo "  Creating virtualenv at $(VENV)/..."; \
		python3 -m venv $(VENV); \
	else \
		echo "  $(VENV)/ already exists — reusing."; \
	fi
	@echo "  Upgrading pip..."
	@$(PIP) install --upgrade pip -q
	@echo "  Installing llm-contract-harness (editable + dev + web)..."
	@$(PIP) install -c requirements.lock -e ".[dev,web]" -q
	@echo ""
	@echo "  ✔  Installed.  Entrypoint: $(LLMCH)"
	@echo ""

demo-repo: ## Create ./my-project as a clean git repo
	$(call check_git)
	$(call banner,demo-repo)
	@if [ -d "$(REPO)/.git" ]; then \
		echo "  $(REPO)/ already exists — skipping."; \
	else \
		echo "  Creating $(REPO)/..."; \
		mkdir -p $(REPO); \
		git -C $(REPO) init -q; \
		git -C $(REPO) config user.email "demo@llmch.local"; \
		git -C $(REPO) config user.name  "llmch-demo"; \
		git -C $(REPO) commit --allow-empty -m "init" -q; \
		echo "  ✔  $(REPO)/ ready (1 empty commit)."; \
	fi
	@echo ""

plan: ## Plan: turn spec into work orders (writes to ./wo/)
	$(call check_api_key)
	$(call banner,plan)
	@if [ ! -f "$(LLMCH)" ]; then \
		echo "  ✘  $(LLMCH) not found. Run 'make bootstrap' first."; exit 1; \
	fi
	@echo "  Spec:   $(SPEC)"
	@echo "  Output: $(WODIR)/"
	@echo ""
	@$(LLMCH) plan --spec $(SPEC) --outdir $(WODIR)
	@echo ""

run: ## Run all work orders against the demo repo
	$(call check_api_key)
	$(call check_git)
	$(call banner,run)
	@if [ ! -f "$(LLMCH)" ]; then \
		echo "  ✘  $(LLMCH) not found. Run 'make bootstrap' first."; exit 1; \
	fi
	@if [ ! -d "$(REPO)/.git" ]; then \
		echo "  ✘  $(REPO)/ is not a git repo. Run 'make demo-repo' first."; exit 1; \
	fi
	@if [ ! -d "$(WODIR)" ]; then \
		echo "  ✘  $(WODIR)/ not found. Run 'make plan' first."; exit 1; \
	fi
	@echo "  Repo:     $(REPO)/"
	@echo "  Workdir:  $(WODIR)/"
	@echo "  Branch:   $(BRANCH)"
	@echo ""
	@$(LLMCH) run-all --repo $(REPO) --workdir $(WODIR) \
		--branch $(BRANCH) --create-branch
	@echo ""

demo: ## Full demo: bootstrap → demo-repo → plan → run
	@$(MAKE) bootstrap
	@$(MAKE) demo-repo
	@$(MAKE) plan
	@$(MAKE) run
	$(call banner,done)
	@echo "  ✔  Demo complete. Inspect results:"
	@echo "       cd $(REPO) && git log --oneline"
	@echo "       ls artifacts/"
	@echo ""

web-build: ## Build the React frontend (production)
	$(call banner,web-build)
	@command -v npm >/dev/null 2>&1 || \
		{ echo "  ✘  npm not found. Install Node.js 20+."; exit 1; }
	@cd web/ui && npm install --ignore-scripts -q 2>/dev/null && npm run build
	@echo ""
	@echo "  ✔  Built → web/ui/dist/"
	@echo ""

web-dev: ## Start backend + frontend dev servers
	$(call check_api_key)
	$(call banner,web-dev)
	@echo "  Starting FastAPI backend (port $(or $(LLMCH_PORT),8000))..."
	@echo "  Starting Vite dev server..."
	@echo ""
	@echo "  Tip: press Ctrl-C to stop both."
	@echo ""
	@trap 'kill 0' INT; \
		$(PY) -m web.server.main & \
		cd web/ui && npm run dev & \
		wait

smoke: ## Smoke test: run a trivial spec end-to-end (requires running web server)
	$(call check_api_key)
	$(call banner,smoke)
	@echo "  Spec: examples/smoke.txt"
	@echo "  Submitting to http://127.0.0.1:$${LLMCH_PORT:-8000} ..."
	@echo ""
	@PROMPT=$$(cat examples/smoke.txt) && \
	RUN_ID=$$(curl -sf -X POST "http://127.0.0.1:$${LLMCH_PORT:-8000}/api/v1/runs" \
		-H "Content-Type: application/json" \
		-d "{\"prompt\": \"$$PROMPT\"}" \
		| $(PY) -c "import sys,json; print(json.load(sys.stdin)['run_id'])") && \
	echo "  run_id: $$RUN_ID" && \
	echo "  Polling for completion (up to 10 min)..." && \
	for i in $$(seq 1 120); do \
		STATUS=$$(curl -sf "http://127.0.0.1:$${LLMCH_PORT:-8000}/api/v1/runs/$$RUN_ID" \
			| $(PY) -c "import sys,json; print(json.load(sys.stdin)['status'])"); \
		if [ "$$STATUS" = "complete" ]; then \
			echo "  status: complete"; \
			echo ""; \
			echo "  ✔  Smoke test passed."; echo ""; \
			exit 0; \
		elif [ "$$STATUS" = "failed" ]; then \
			echo "  status: failed"; \
			echo ""; \
			echo "  ✘  Pipeline failed."; echo ""; \
			exit 1; \
		fi; \
		sleep 5; \
	done; \
	echo "  ✘  Timed out after 10 minutes."; exit 1

clean: ## Remove demo artifacts (wo/, artifacts/, my-project/)
	$(call banner,clean)
	@echo "  Removing:"
	@for d in $(WODIR) artifacts $(REPO); do \
		if [ -e "$$d" ]; then \
			echo "    $$d/"; \
		fi; \
	done
	@echo ""
	@rm -rf $(WODIR) artifacts $(REPO)
	@echo "  ✔  Cleaned."
	@echo ""
