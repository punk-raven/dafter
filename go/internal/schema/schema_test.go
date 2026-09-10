package schema

import "testing"

func TestNewCompilesEveryEmbeddedSchema(t *testing.T) {
	v, err := New()
	if err != nil {
		t.Fatalf("embedded schemas do not compile: %v", err)
	}
	for _, id := range []string{ResolvedSessionConfig, EventEnvelope, Error} {
		if _, err := v.SchemaFor(id); err != nil {
			t.Errorf("%s: %v", id, err)
		}
	}
}

func TestCompilationHappensBeforeAnyValidation(t *testing.T) {
	if Default == nil || len(Default.compiled) == 0 {
		t.Fatal("Default was not compiled at initialisation")
	}
}

func TestUnknownSchemaIDIsAnError(t *testing.T) {
	if _, err := Default.SchemaFor("https://schemas.dafter.dev/nope/v1/nope.schema.json"); err == nil {
		t.Fatal("an unregistered schema id was accepted")
	}
}
