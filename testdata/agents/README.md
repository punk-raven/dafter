# Agent definition vectors

`definitions.json` pins what the agent definition schema
(`schemas/config/v1/agent-definition.schema.json`) accepts and rejects, fed to
both halves unchanged: `go/internal/config/agents_test.go` and
`python/dafter_core/tests/test_agent_definition.py`.

Each case is one document an operator stores in the config store's `agents`
kind. `rejected` is null for a document both halves accept, or the error code
and the pointer of every problem, sorted, which both halves must report alike.
A field the schema does not declare is left out on purpose: the two
validators locate an unevaluated property differently (Go at the property,
Python at the object that holds it), so each half tests that on its own.
