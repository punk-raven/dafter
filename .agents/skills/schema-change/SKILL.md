---
name: schema-change
description: >-
  Checklist for changing anything under schemas/ in the Dafter repo: config
  document, event envelope, error taxonomy or shared identifier patterns. Use
  whenever a field, enum member, pattern or payload shape changes, so the Go and
  Python halves stay generated from one definition and CI stays green.
---

# Changing a schema

`schemas/` is the only source of truth. Both halves consume it through
generated output, so a schema change is never finished until the generated
files, the parsers and the drift tests on both sides agree with it. The
Makefile and `make generate-check` police the generated output; the enum drift
tests police the generator. Both must pass.

## Checklist

1. **Edit under `schemas/` only.** Never touch the copies in
   `go/internal/schema/schemas/` or
   `python/dafter_core/src/dafter_core/_schemas/`, nor any `*_gen.go`, nor
   `python/dafter_core/src/dafter_core/enums.py`. They are outputs.
   Keep the conventions the existing schemas set: objects close with
   `unevaluatedProperties: false` (not `additionalProperties`, see the commit
   body of `fix(schemas): correct region, hash, closure and event id width`),
   identifiers reference the patterns in `schemas/common/v1/ids.schema.json`,
   and an event type must have a payload schema before any code emits it
   (stated in `schemas/events/v1/envelope.schema.json`).

2. **Run `make generate`.** It runs `go/tools/enumgen` and refreshes both
   schema copies. Requires the Go toolchain (`go/go.mod` pins the version).

3. **If a field changed, update both parsers by hand.** Generated output covers
   enums and schema copies only; struct and dataclass fields are hand-written:
   - Go: the structs in `go/internal/config/config.go` or
     `go/internal/events/events.go`. Decoding uses `DisallowUnknownFields`, so a
     schema field the struct lacks is rejected at decode, which is a test
     failure, not silent drift.
   - Python: the frozen dataclasses and their `from_dict` in
     `python/dafter_core/src/dafter_core/config.py` or `events.py`. Assign
     fields by keyword, never positionally (see the commit body of
     `fix(python): assign pipeline fields by name`).
   - A rule the schema cannot express goes in `check` on both sides, with the
     same error code and an equivalent message.

4. **If an enum member was added or removed**, nothing else is needed for an
   enum enumgen already knows: the constants and the `All*` slice regenerate
   in Go, the `StrEnum` regenerates in Python, and the drift tests compare
   against the schema.

5. **If a NEW enum was added**, register it in the `targets` slice in
   `go/tools/enumgen/main.go` (schema path, JSON pointer to the `enum` array,
   Go package, type, constant prefix, output file, `All*` name), then re-run
   `make generate`. Then add a drift case in both halves:
   - Go: the `cases` table in the matching `go/internal/<pkg>/enums_test.go`
     (`schema.CheckEnum` against `schema.Names(<pkg>.All<Type>)`).
   - Python: `CASES` in `python/dafter_core/tests/test_enums.py`, and export
     the class from `python/dafter_core/src/dafter_core/__init__.py`.
     `test_every_generated_enum_has_a_drift_test` fails until the case exists.

6. **Add or update the behaviour tests** in the package you touched
   (`*_test.go` next to the code; `python/dafter_core/tests/`). A change that
   rejects or accepts a new document shape needs a test on both sides that
   feeds the same document and expects the same error code.

7. **Run `make check`** (or, without Go, at least `make py-lint py-test` from a
   tree where `make generate` has already run). CI runs `make check`.

8. **Commit the generated output in the same commit** as the schema edit.
   A commit that changes `schemas/` without the matching generated files fails
   `make generate-check`.

## What not to do

- Do not hand-edit generated files to make a test pass; fix the schema or the
  generator.
- Do not add a Go field without the schema change, or the platform's own type
  fails its own contract (see the `details` field in the commit body of
  `fix(core): report every validation failure, and locate each one`).
- Do not invent a payload shape for an event no code produces yet; the
  envelope leaves those open on purpose.
