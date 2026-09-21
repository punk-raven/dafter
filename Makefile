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

# ---------------------------------------------------------------------------
# Codegen
#
# There is one definition of an event, ever, and it lives in schemas/. Go cannot
# //go:embed across a module boundary and Python cannot read a resource outside
# its package, so the schemas are copied into each one - here, at build time.
# Nothing this target writes is committed, which is what generate-check proves.
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------

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

.PHONY: check-tools
check-tools: $(LINT) $(VULN) ## Build the pinned linter and vulnerability scanner

.PHONY: tools
tools: check-tools $(LK) ## check-tools, plus the pinned lk used by the media load test

.PHONY: setup
setup: ## Prepare a fresh clone: check the toolchain, generate, resolve the Python environment
	@./scripts/setup.sh

.PHONY: check
check: generate-check vet lint test py-lint py-test ## What CI runs

# ---------------------------------------------------------------------------
# Dev stack
# ---------------------------------------------------------------------------

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
	@echo ""

.PHONY: loadtest
loadtest: ## Run the load test against the dev stack (USERS=100 DURATION=60s)
	cd $(GO_DIR) && go run ./cmd/dafter-loadtest \
		-target http://127.0.0.1:8080 \
		-users $${USERS:-100} \
		-duration $${DURATION:-60s}

# lk is fetched as a release binary rather than go-installed: the video
# clips its publishers loop are Git LFS objects, and a module-proxy build
# embeds the LFS pointer files, so its publishers connect but send no frames.
$(LK):
	@./scripts/install-lk.sh "$(LK_VERSION)" "$(LK)"

.PHONY: loadtest-media
loadtest-media: $(LK) ## Concurrent video calls through the control plane and the SFU (ROOMS=100 DURATION=60s, or RAMP="10 25 50")
	@LK_BIN=$(LK) ./scripts/loadtest-media.sh

.PHONY: dev-down
dev-down: ## Tear down the dev stack and volumes
	docker compose down -v
