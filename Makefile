SHELL := /bin/bash
.DEFAULT_GOAL := help

GO_DIR     := go
SCHEMA_DIR := schemas
GO_SCHEMAS := $(GO_DIR)/internal/schema/schemas

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
	@cd $(GO_DIR) && go run ./tools/enumgen ../$(SCHEMA_DIR) .
	@rm -rf $(GO_SCHEMAS)
	@mkdir -p $(GO_SCHEMAS)
	@cp -R $(SCHEMA_DIR)/. $(GO_SCHEMAS)/
	@find $(GO_SCHEMAS) -type f ! -name '*.schema.json' -delete
	@echo "generated $(GO_SCHEMAS)"

.PHONY: generate-check
generate-check: generate ## Fail if the generated copy is stale
	@if ! git diff --quiet -- $(GO_SCHEMAS); then \
		echo "generated output is stale - run 'make generate' and commit the result"; \
		git --no-pager diff --stat -- $(GO_SCHEMAS); \
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

.PHONY: lint
lint: generate ## Lint, including the package dependency graph
	cd $(GO_DIR) && golangci-lint run

.PHONY: check
check: generate-check vet lint test ## What CI runs
