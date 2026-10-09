SHELL := /bin/bash
.DEFAULT_GOAL := help

GO_DIR     := go
SCHEMA_DIR := schemas
PY_DIR     := python
PY_CORE    := $(PY_DIR)/dafter_core/src/dafter_core
GO_SCHEMAS := $(GO_DIR)/internal/schema/schemas
PY_SCHEMAS := $(PY_CORE)/_schemas
LINT       := $(abspath $(GO_DIR)/bin/golangci-lint)
VULN       := $(abspath $(GO_DIR)/bin/govulncheck)
LK         := $(abspath $(GO_DIR)/bin/lk)
LK_VERSION := 2.18.7
GENERATED  := $(GO_SCHEMAS) $(PY_SCHEMAS) $(PY_CORE)/enums.py ':(glob)$(GO_DIR)/internal/**/*_gen.go'

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z0-9_.-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: generate
generate: ## Refresh everything derived from schemas/
	@cd $(GO_DIR) && go run ./tools/enumgen ../$(SCHEMA_DIR) . ../$(PY_CORE)/enums.py
	@for dest in $(GO_SCHEMAS) $(PY_SCHEMAS); do \
		rm -rf "$$dest"; mkdir -p "$$dest"; \
		cp -R $(SCHEMA_DIR)/. "$$dest"/; \
		find "$$dest" -type f ! -name '*.schema.json' -delete; \
		echo "  generated $$dest"; \
	done
	@find $(PY_SCHEMAS) -type d -exec touch {}/__init__.py \;

.PHONY: generate-check
generate-check: ## Fail if any generated output was committed
	@tracked=$$(git ls-files -- $(GENERATED)); \
	if [ -n "$$tracked" ]; then \
		echo "generated output must not be committed - 'make generate' produces it:"; \
		echo "$$tracked"; \
		exit 1; \
	fi

.PHONY: build
build: generate ## Build
	cd $(GO_DIR) && go build ./...

.PHONY: test
test: generate ## Test with the race detector
	cd $(GO_DIR) && go test -race ./...

.PHONY: vet
vet: generate ## go vet
	cd $(GO_DIR) && go vet ./...

.PHONY: tidy
tidy: ## Tidy the Go module
	cd $(GO_DIR) && go mod tidy

$(LINT):
	cd $(GO_DIR)/tools/golangci && GOBIN=$(dir $(LINT)) go install github.com/golangci/golangci-lint/v2/cmd/golangci-lint

.PHONY: lint
lint: generate $(LINT) ## Lint, including the package dependency graph
	cd $(GO_DIR) && $(LINT) run

$(VULN):
	cd $(GO_DIR)/tools/govulncheck && GOBIN=$(dir $(VULN)) go install golang.org/x/vuln/cmd/govulncheck

.PHONY: vulncheck
vulncheck: generate $(VULN) ## Report vulnerabilities on a reachable call path
	cd $(GO_DIR) && $(VULN) ./...

.PHONY: py-test
py-test: generate ## Run the Python tests
	cd $(PY_DIR) && uv run --frozen pytest -q

.PHONY: py-lint
py-lint: generate ## Lint and type-check the Python packages
	cd $(PY_DIR) && uv run --frozen ruff check . && uv run --frozen ruff format --check . && uv run --frozen mypy

.PHONY: js-test
js-test: ## Test the test client's scripts with Node's built-in runner
	node --test $(GO_DIR)/cmd/dafter-control/jstest/*.test.mjs

.PHONY: check-tools
check-tools: $(LINT) $(VULN) ## Build the pinned linter and vulnerability scanner

.PHONY: tools
tools: check-tools $(LK) ## check-tools, plus the pinned lk used by the media load test

.PHONY: setup
setup: ## Prepare a fresh clone: check the toolchain, generate, resolve the Python environment
	@./scripts/setup.sh

.PHONY: rules-check
rules-check: ## Check the repository rules the agent hooks enforce, and test the checker
	@cd scripts && python3 -m unittest discover -s agent_rules/tests -t . -q
	@cd scripts && python3 -m agent_rules check

.PHONY: check
check: generate-check rules-check vet lint test js-test py-lint py-test ## What CI runs

EVALS_OUT          ?= $(abspath build/evals)
EVALS_LANGUAGES    ?= hi en-IN kn-IN mr-IN te-IN
AGENT_JOBS         := ../testdata/agent
JOB_hi             := hindi-webrtc-job.json
JOB_en-IN          := english-webrtc-job.json
JOB_kn-IN          := kannada-webrtc-job.json
JOB_mr-IN          := marathi-webrtc-job.json
JOB_te-IN          := telugu-webrtc-job.json
ASR_MANIFEST_hi    := kathbath-hi.json
ASR_MANIFEST_en-IN := svarah-en-IN.json
ASR_MANIFEST_kn-IN := kathbath-kn-IN.json
ASR_MANIFEST_mr-IN := kathbath-mr-IN.json
ASR_MANIFEST_te-IN := kathbath-te-IN.json
ASR_SET            ?= public
ASR_MAX_INR        ?= 5
TTS_MAX_INR        ?= 10
TEXT_MAX_INR       ?= 450
SCREEN_RUNS        ?= 1
SCREEN_CANDIDATES  ?= sarvam_105b,gemini_flash_lite
SCENARIOS_SET      ?= all
SCENARIOS_K        ?= 4
SCENARIOS_MIN_PASS_K ?= 0.85
SCENARIOS_JOB      ?=
LIVE_CONTROL       ?= http://127.0.0.1:8080
LIVE_TURNS         ?= 10
LIVE_SWEEPS        ?= turntaking,interruptions,vad
LIVE_CONDITIONS    ?= clean,g711
LIVE_BASELINE      ?=
REDTEAM_PASS_RATE  ?= 99
REDTEAM_SAMPLE     ?=
PROMPTFOO          := npx --yes promptfoo@0.123.1

comma := ,
empty :=
space := $(empty) $(empty)
comma-list = $(subst $(space),$(comma),$(strip $(1)))
base-languages = $(foreach l,$(1),$(firstword $(subst -, ,$(l))))
known-languages = $(foreach l,$(EVALS_LANGUAGES),$(if $(JOB_$(l)),,$(error EVALS_LANGUAGES: unknown language $(l); use hi en-IN kn-IN mr-IN te-IN)))

.PHONY: evals-offline
evals-offline: generate ## Eval package tests; no provider calls
	cd $(PY_DIR) && uv run --frozen pytest -q dafter_evals/tests

.PHONY: evals-text
evals-text: generate ## Text gates: dafter-screen and pass^k scenarios (EVALS_LANGUAGES, TEXT_MAX_INR, SCENARIOS_K); spends LLM credits
	$(known-languages)
	@mkdir -p $(EVALS_OUT)
	@cd $(PY_DIR) && status=0; \
	uv run --frozen dafter-screen --out $(EVALS_OUT)/screen \
		--language $(call comma-list,$(call base-languages,$(EVALS_LANGUAGES))) \
		--runs $(SCREEN_RUNS) --candidates $(SCREEN_CANDIDATES) || status=1; \
	uv run --frozen dafter-scenarios --out $(EVALS_OUT)/scenarios \
		--language $(call comma-list,$(EVALS_LANGUAGES)) --set $(SCENARIOS_SET) \
		--k $(SCENARIOS_K) --min-pass-k $(SCENARIOS_MIN_PASS_K) --max-inr $(TEXT_MAX_INR) \
		$(if $(SCENARIOS_JOB),--job $(SCENARIOS_JOB)) || status=1; \
	exit $$status

.PHONY: evals-asr
evals-asr: generate ## STT accuracy per language, exit 1 on a baseline regression (EVALS_LANGUAGES, ASR_SET, ASR_MAX_INR); spends STT credits
	$(known-languages)
	@mkdir -p $(EVALS_OUT)
	@cd $(PY_DIR) && status=0; \
	$(foreach l,$(EVALS_LANGUAGES),\
	$(if $(filter golden,$(ASR_SET)),\
	uv run --frozen dafter-asr run --set golden --language $(l),\
	uv run --frozen dafter-asr fetch ../testdata/asr/$(ASR_MANIFEST_$(l)) && \
	uv run --frozen dafter-asr run ../testdata/asr/$(ASR_MANIFEST_$(l))) \
		--job $(AGENT_JOBS)/$(JOB_$(l)) --max-inr $(ASR_MAX_INR) \
		--out $(EVALS_OUT)/asr-$(l).json || status=1;) \
	exit $$status

.PHONY: evals-tts
evals-tts: generate ## TTS round trip per language, exit 1 on a baseline regression (EVALS_LANGUAGES, TTS_MAX_INR); spends TTS and STT credits
	$(known-languages)
	@mkdir -p $(EVALS_OUT)
	@cd $(PY_DIR) && status=0; \
	$(foreach l,$(EVALS_LANGUAGES),\
	uv run --frozen dafter-tts run --job $(AGENT_JOBS)/$(JOB_$(l)) \
		--max-inr $(TTS_MAX_INR) --out $(EVALS_OUT)/tts-$(l).json || status=1;) \
	exit $$status

.PHONY: evals-live
evals-live: generate ## Live agent per language plus sweeps against a running stack (EVALS_LANGUAGES, LIVE_TURNS, LIVE_SWEEPS); spends provider credits
	$(known-languages)
	@mkdir -p $(EVALS_OUT)
	@cd $(PY_DIR) && status=0; \
	$(foreach l,$(EVALS_LANGUAGES),\
	uv run --frozen dafter-evals --control $(LIVE_CONTROL) --language $(l) --turns $(LIVE_TURNS) \
		$(if $(LIVE_BASELINE),--baseline $(LIVE_BASELINE)/live-$(l).json) \
		--out $(EVALS_OUT)/live-$(l).json || status=1;) \
	uv run --frozen dafter-evals --control $(LIVE_CONTROL) --sweep $(LIVE_SWEEPS) \
		--conditions $(LIVE_CONDITIONS) --sweep-languages $(call comma-list,$(EVALS_LANGUAGES)) \
		--out $(EVALS_OUT)/sweep.json || status=1; \
	exit $$status

.PHONY: evals-redteam
evals-redteam: generate ## Red-team bank, fails under REDTEAM_PASS_RATE percent (EVALS_LANGUAGES, REDTEAM_SAMPLE); spends LLM credits
	$(known-languages)
	@mkdir -p $(EVALS_OUT)
	cd $(PY_DIR) && PROMPTFOO_PASS_RATE_THRESHOLD=$(REDTEAM_PASS_RATE) uv run --frozen $(PROMPTFOO) eval \
		-c ../testdata/redteam/promptfooconfig.yaml --no-cache \
		--filter-pattern '^($(subst $(space),|,$(strip $(EVALS_LANGUAGES)))) ' \
		$(if $(REDTEAM_SAMPLE),--filter-sample $(REDTEAM_SAMPLE) --filter-sample-seed 7) \
		-o $(EVALS_OUT)/redteam.json $(EVALS_OUT)/redteam.html

.PHONY: evals-scorecard
evals-scorecard: generate ## Fold the asr, live and tts reports in EVALS_OUT into one card per language; no credits
	@mkdir -p $(EVALS_OUT)
	@shopt -s nullglob; cd $(PY_DIR) && uv run --frozen dafter-scorecard \
		--accuracy $(EVALS_OUT)/asr-*.json --live $(EVALS_OUT)/live-*.json \
		--tts $(EVALS_OUT)/tts-*.json --languages $(call comma-list,$(EVALS_LANGUAGES)) \
		--out $(EVALS_OUT)/scorecard.json --markdown $(EVALS_OUT)/scorecard.md

.PHONY: dev
dev: setup ## Build and start the full dev stack
	docker compose up -d --build
	@echo ""
	@echo "  Test client   http://127.0.0.1:8080"
	@echo "  LiveKit SFU   ws://127.0.0.1:7880"
	@echo "  MinIO console http://127.0.0.1:9001  (minioadmin/minioadmin)"
	@echo "  Jaeger UI     http://127.0.0.1:16686"
	@echo "  Prometheus    http://127.0.0.1:9090"
	@echo "  Grafana       http://127.0.0.1:3000  (admin/admin)"
	@echo "  SIP           udp/tcp 5060, RTP udp 10000-10100"
	@echo ""

.PHONY: loadtest
loadtest: ## Run the load test against the dev stack (USERS=100 DURATION=60s)
	cd $(GO_DIR) && go run ./cmd/dafter-loadtest \
		-target http://127.0.0.1:8080 \
		-users $${USERS:-100} \
		-duration $${DURATION:-60s}

$(LK):
	@./scripts/install-lk.sh "$(LK_VERSION)" "$(LK)"

.PHONY: loadtest-media
loadtest-media: $(LK) ## Concurrent video calls through the control plane and the SFU (ROOMS=100 DURATION=60s, or RAMP="10 25 50")
	@LK_BIN=$(LK) ./scripts/loadtest-media.sh

.PHONY: dev-down
dev-down: ## Tear down the dev stack and volumes
	docker compose --profile tunnel down -v

.PHONY: dev-clean
dev-clean: ## Tear down the dev stack, its volumes and its images (the build cache stays: docker builder prune)
	docker compose --profile tunnel down -v --rmi all --remove-orphans
