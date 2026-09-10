SHELL := /bin/bash
.DEFAULT_GOAL := help

GO_DIR     := go
SCHEMA_DIR := schemas
PY_DIR     := python
PY_CORE    := $(PY_DIR)/dafter_core/src/dafter_core
GO_SCHEMAS := $(GO_DIR)/internal/schema/schemas
PY_SCHEMAS := $(PY_CORE)/_schemas
LINT       := $(abspath $(GO_DIR)/bin/golangci-lint)
GENERATED  := $(GO_SCHEMAS) $(PY_SCHEMAS) $(PY_CORE)/enums.py ':(glob)$(GO_DIR)/internal/**/*_gen.go'

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z0-9_.-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Codegen
#
# There is one definition of an event, ever, and it lives in schemas/. Go cannot
# //go:embed across a module boundary, so the schemas are copied into the module
# and checked in. That keeps `go build` and `go test` working without make, at
# the cost of a duplicate that CI has to police - which generate-check does.
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
generate-check: generate ## Fail if any generated output is stale or hand-edited
	@dirty=$$(git status --porcelain -- $(GENERATED)); \
	if [ -n "$$dirty" ]; then \
		echo "generated output does not match schemas/ - run 'make generate' and commit the result"; \
		echo "$$dirty"; \
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

.PHONY: py-test
py-test: generate ## Run the Python tests
	cd $(PY_DIR) && uv run --frozen pytest -q

.PHONY: py-lint
py-lint: generate ## Lint and type-check the Python packages
	cd $(PY_DIR) && uv run --frozen ruff check . && uv run --frozen ruff format --check . && uv run --frozen mypy

.PHONY: check
check: generate-check vet lint test py-lint py-test ## What CI runs
