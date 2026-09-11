---
name: schema-change
description: >-
  Checklist for changing anything under schemas/ (config, event envelope, errors, ids)
  so Go and Python stay generated from one definition and CI stays green.
---

# Changing a schema

1. Edit under `schemas/` only. Never touch `go/internal/schema/schemas/`,
   `python/dafter_core/src/dafter_core/_schemas/`, `*_gen.go` or `enums.py`; they are outputs.
   Objects close with `unevaluatedProperties: false` (why: commit body
   `fix(schemas): correct region, hash, closure and event id width`). An event type needs a
   payload schema before code emits it (`schemas/events/v1/envelope.schema.json`).

2. Run `make generate` (needs the Go toolchain pinned in `go/go.mod`).

3. Field changed: update both hand-written parsers.
   Go: structs in `go/internal/config/config.go` or `go/internal/events/events.go`.
   Python: dataclasses and `from_dict` in `python/dafter_core/src/dafter_core/config.py` or
   `events.py`; assign by keyword (why: commit body `fix(python): assign pipeline fields by name`).
   A rule the schema cannot express goes in `check` on both sides, same error code.

4. Enum member added or removed on an enum enumgen already knows: nothing more, output regenerates.

5. New enum: add it to `targets` in `go/tools/enumgen/main.go` (schema path, pointer to the
   `enum` array, package, type, prefix, output file, `All*` name), re-run `make generate`, then
   add a drift case in `go/internal/<pkg>/enums_test.go` and in `CASES` of
   `python/dafter_core/tests/test_enums.py`, and export the class from
   `python/dafter_core/src/dafter_core/__init__.py`.

6. Add behaviour tests on both sides that feed the same document and expect the same error code.

7. Run `make check`. Without Go, `make py-lint py-test` fails because both depend on
   `generate` (which runs `go run ./tools/enumgen`), so on a tree where generate already ran
   call uv directly from `python/`:
   `uv run --frozen ruff check . && uv run --frozen ruff format --check . && uv run --frozen mypy`
   then `uv run --frozen pytest -q`.

8. Commit the generated output in the same commit as the schema edit, or `make generate-check` fails.

Never hand-edit generated output to pass a test. Never add a Go field without the schema change
(why: commit body `fix(core): report every validation failure, and locate each one`).
