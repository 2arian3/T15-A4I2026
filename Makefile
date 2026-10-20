# Curbside: ADK agent on Agent Runtime (Agent Engine), Gemini via Vertex AI.
#
# Every setting can be overridden on the command line, e.g.
#   make deploy REGION=us-east1
#   make run Q="Which tracts are farthest from a safety-net clinic?"

PROJECT_ID     ?= qwiklabs-gcp-04-e3eaf2b4f0c3
# Agent Runtime is regional (it has no "global" location) ...
REGION         ?= us-central1
# ... while Gemini is called on the global endpoint.
MODEL_LOCATION ?= global
MODEL          ?= gemini-3.8-flash
AGENT_NAME     ?= curbside
SA_NAME        ?= curbside-agent
DATASET        ?= a4i_health
SA_EMAIL       := $(SA_NAME)@$(PROJECT_ID).iam.gserviceaccount.com
Q              ?= Hello! What can you help me with, and what can't you tell me about a single neighborhood?
CONFIRM        ?=
# Local web server (make web / make serve / make frontend)
HOST           ?= 127.0.0.1
PORT           ?= 8000
FRONTEND_NAME  ?= curbside-ui
FRONTEND_PORT  ?= 8002

# Production Agent Runtime (shared). Sandbox is a separate engine so a personal
# deploy cannot overwrite curbside. Override the suffix with DEV_ID=...
DEV_ID         ?= $(shell id -u)
DEV_AGENT_NAME ?= curbside-dev-$(DEV_ID)
METADATA       := deployment_metadata.json
DEV_METADATA   := deployment_metadata.dev.json
AGENT_ID        = $(shell python3 -c "import json;print(json.load(open('$(METADATA)')).get('remote_agent_runtime_id') or '')" 2>/dev/null)
DEV_AGENT_ID    = $(shell python3 -c "import json;print(json.load(open('$(DEV_METADATA)')).get('remote_agent_runtime_id') or '')" 2>/dev/null)

SA_ENV := PROJECT_ID=$(PROJECT_ID) SA_NAME=$(SA_NAME) DATASET=$(DATASET)

.DEFAULT_GOAL := help
.PHONY: help install env check auth setup setup-dry-run dev web serve frontend run test test-integration \
        lint deploy deploy-dry-run deploy-frontend deploy-frontend-dry-run status list test-remote destroy destroy-sa destroy-all \
        deploy-dev deploy-dev-dry-run status-dev test-remote-dev destroy-dev

help: ## Show this help
	@echo "Curbside agent  (project=$(PROJECT_ID) region=$(REGION) model=$(MODEL)@$(MODEL_LOCATION))"
	@echo "  production : $(AGENT_NAME)  ($(METADATA))"
	@echo "  sandbox    : $(DEV_AGENT_NAME)  ($(DEV_METADATA))"
	@echo
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# --- Local setup ---------------------------------------------------------

install: ## Install agents-cli and the project's Python dependencies (uv)
	@command -v uv >/dev/null || { echo "uv not found: https://docs.astral.sh/uv/getting-started/installation/"; exit 1; }
	uv tool install --upgrade google-agents-cli
	uv sync --extra lint

env: ## Write .env for local runs (Vertex AI, this project)
	@printf '%s\n' \
		"# Local development configuration (gitignored). Regenerate with: make env" \
		"GOOGLE_GENAI_USE_ENTERPRISE=true" \
		"GOOGLE_CLOUD_PROJECT=$(PROJECT_ID)" \
		"GOOGLE_CLOUD_LOCATION=$(MODEL_LOCATION)" \
		"CURBSIDE_MODEL=$(MODEL)" > .env
	@echo "Wrote .env"

auth: ## Log in to gcloud and set Application Default Credentials
	gcloud auth login
	gcloud auth application-default login
	gcloud config set project $(PROJECT_ID)
	gcloud auth application-default set-quota-project $(PROJECT_ID)

check: ## Check that gcloud, uv and agents-cli are ready
	@command -v gcloud >/dev/null || { echo "gcloud not found"; exit 1; }
	@command -v uv >/dev/null || { echo "uv not found (make install)"; exit 1; }
	@command -v agents-cli >/dev/null || { echo "agents-cli not found (make install)"; exit 1; }
	@echo "gcloud account : $$(gcloud config get-value account 2>/dev/null)"
	@echo "gcloud project : $$(gcloud config get-value project 2>/dev/null) (Makefile uses $(PROJECT_ID))"
	@gcloud auth application-default print-access-token >/dev/null 2>&1 \
		&& echo "ADC            : ok" || echo "ADC            : missing (make auth)"

# --- Service account -----------------------------------------------------

setup: ## Enable APIs, create the service account, grant its roles (idempotent)
	$(SA_ENV) bash scripts/setup_sa.sh

setup-dry-run: ## Print what `make setup` would run
	DRY_RUN=1 $(SA_ENV) bash scripts/setup_sa.sh

# --- Develop and test locally ---------------------------------------------

dev: ## Open the local ADK playground (adk web)
	agents-cli playground

web: ## Run the ADK web UI locally at http://HOST:PORT (default 127.0.0.1:8000)
	@test -f .env || $(MAKE) --no-print-directory env
	@echo "ADK web UI: http://$(HOST):$(PORT)/dev-ui/  (Ctrl+C to stop)"
	uv run --env-file .env adk web --host $(HOST) --port $(PORT) .

serve: ## Run the same server Agent Engine runs, locally, with auto-reload
	@test -f .env || $(MAKE) --no-print-directory env
	@echo "Server: http://$(HOST):$(PORT)  (dev UI at /dev-ui/, Ctrl+C to stop)"
	uv run --env-file .env uvicorn agent.fast_api_app:app --host $(HOST) --port $(PORT) --reload

frontend: ## Run the Leaflet frontend + Agent Engine proxy locally (default 127.0.0.1:8002)
	@echo "Curbside UI: http://$(HOST):$(FRONTEND_PORT)  (Ctrl+C to stop)"
	uv run uvicorn agent.proxy_server:app --host $(HOST) --port $(FRONTEND_PORT) --reload

run: ## Ask the local agent one question: make run Q="..."
	agents-cli run "$(Q)"

test: ## Run unit tests (no GCP credentials needed)
	uv run pytest tests/unit -q

test-integration: ## Run integration tests (call Gemini on Vertex AI)
	uv run pytest tests/integration -q

lint: ## Lint with ruff
	uv run --extra lint ruff check agent scripts tests
	uv run --extra lint ruff format --check agent scripts tests

# --- Deploy to Agent Runtime ----------------------------------------------

DEPLOY_COMMON = --project $(PROJECT_ID) --region $(REGION) \
	--service-account $(SA_EMAIL) --no-agent-identity \
	--env-vars GOOGLE_CLOUD_LOCATION=$(MODEL_LOCATION),CURBSIDE_MODEL=$(MODEL) \
	--no-confirm-project
DEPLOY_FLAGS = $(DEPLOY_COMMON) --service-name $(AGENT_NAME)
DEV_DEPLOY_FLAGS = $(DEPLOY_COMMON) --service-name $(DEV_AGENT_NAME)
WITH_DEV_META = bash scripts/with_cli_metadata.sh $(DEV_METADATA)

deploy: test ## Production: unit tests, then create/update curbside
	agents-cli deploy $(DEPLOY_FLAGS)

deploy-dry-run: ## Print the production deploy without running it
	agents-cli deploy $(DEPLOY_FLAGS) --dry-run

deploy-dev: test ## Sandbox: unit tests, then create/update curbside-dev-<uid>
	$(WITH_DEV_META) agents-cli deploy $(DEV_DEPLOY_FLAGS)

deploy-dev-dry-run: ## Print the sandbox deploy without running it
	$(WITH_DEV_META) agents-cli deploy $(DEV_DEPLOY_FLAGS) --dry-run

status: ## Check a production deploy that is still in progress
	agents-cli deploy --status --project $(PROJECT_ID) --region $(REGION) --no-confirm-project

status-dev: ## Check a sandbox deploy that is still in progress
	$(WITH_DEV_META) agents-cli deploy --status --project $(PROJECT_ID) --region $(REGION) --no-confirm-project

list: ## List Agent Runtime deployments in the project
	agents-cli deploy --list --project $(PROJECT_ID) --region $(REGION) --no-confirm-project

test-remote: ## Ask production curbside one question: make test-remote Q="..."
	@test -n "$(AGENT_ID)" || { echo "No deployed agent in $(METADATA); run make deploy"; exit 1; }
	agents-cli run --url https://$(REGION)-aiplatform.googleapis.com/v1/$(AGENT_ID) --mode adk "$(Q)"

test-remote-dev: ## Ask sandbox curbside-dev one question: make test-remote-dev Q="..."
	@test -n "$(DEV_AGENT_ID)" || { echo "No sandbox agent in $(DEV_METADATA); run make deploy-dev"; exit 1; }
	agents-cli run --url https://$(REGION)-aiplatform.googleapis.com/v1/$(DEV_AGENT_ID) --mode adk "$(Q)"

# --- Deploy Frontend to Cloud Run -----------------------------------------

FRONTEND_ENGINE_URL = https://$(REGION)-aiplatform.googleapis.com/v1/$(AGENT_ID):streamQuery
FRONTEND_DEPLOY_CMD = gcloud run deploy $(FRONTEND_NAME) \
	--source . \
	--project $(PROJECT_ID) \
	--region $(REGION) \
	--service-account $(SA_EMAIL) \
	--allow-unauthenticated \
	--quiet \
	--command uv \
	--args "run,uvicorn,agent.proxy_server:app,--host,0.0.0.0,--port,8080" \
	--set-env-vars "REASONING_ENGINE_URL=$(FRONTEND_ENGINE_URL),GOOGLE_CLOUD_PROJECT=$(PROJECT_ID),REGION=$(REGION)"

deploy-frontend: test ## Deploy the Leaflet frontend + proxy to Cloud Run
	@test -n "$(AGENT_ID)" || { echo "No deployed agent in $(METADATA); run make deploy"; exit 1; }
	gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com --project $(PROJECT_ID)
	$(FRONTEND_DEPLOY_CMD)

deploy-frontend-dry-run: ## Print the Cloud Run frontend deploy command without running it
	@echo $(FRONTEND_DEPLOY_CMD)

# --- Tear down -------------------------------------------------------------

destroy: ## Delete production curbside (asks first; CONFIRM=yes to skip)
	uv run python scripts/destroy_agent.py --project $(PROJECT_ID) --region $(REGION) \
		--name $(AGENT_NAME) --metadata $(METADATA) $(if $(filter yes,$(CONFIRM)),--yes,)

destroy-dev: ## Delete sandbox curbside-dev-<uid> only (asks first; CONFIRM=yes to skip)
	uv run python scripts/destroy_dev.py --project $(PROJECT_ID) --region $(REGION) \
		--name $(DEV_AGENT_NAME) --metadata $(DEV_METADATA) $(if $(filter yes,$(CONFIRM)),--yes,)

destroy-sa: ## Remove the service account and its role grants
	$(SA_ENV) bash scripts/destroy_sa.sh

destroy-all: destroy destroy-sa ## Delete the agent, then the service account
