package schema

import (
	"slices"
	"strings"
	"testing"
)

func TestNewCompilesEveryEmbeddedSchema(t *testing.T) {
	t.Parallel()
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
	t.Parallel()
	if Default == nil || len(Default.compiled) == 0 {
		t.Fatal("Default was not compiled at initialisation")
	}
}

func TestUnknownSchemaIDIsAnError(t *testing.T) {
	t.Parallel()
	if _, err := Default.SchemaFor("https://schemas.dafter.dev/nope/v1/nope.schema.json"); err == nil {
		t.Fatal("an unregistered schema id was accepted")
	}
}

func TestEnumAtReturnsTheMembersSorted(t *testing.T) {
	t.Parallel()
	got, err := EnumAt("common/v1/ids.schema.json", "$defs", "Channel", "enum")
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"long_form", "telephony", "webrtc"}
	if !slices.Equal(got, want) {
		t.Fatalf("EnumAt = %v, want %v", got, want)
	}
}

func TestEnumAtRejectsWhatIsNotAnEnum(t *testing.T) {
	t.Parallel()
	const ids = "common/v1/ids.schema.json"
	cases := []struct {
		name string
		file string
		keys []string
		want string
	}{
		{"missing file", "common/v1/nope.schema.json", []string{"$defs"}, "run `make generate`"},
		{"path through a non-object", ids, []string{"$defs", "Channel", "enum", "x"}, "is not an object"},
		{"leaf that is an object", ids, []string{"$defs", "Channel"}, "is not an enum"},
		{"missing key", ids, []string{"$defs", "Nope", "enum"}, "is not an object"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			_, err := EnumAt(tc.file, tc.keys...)
			if err == nil {
				t.Fatalf("EnumAt(%s, %v) accepted", tc.file, tc.keys)
			}
			if !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("EnumAt(%s, %v) = %q, want it to mention %q", tc.file, tc.keys, err, tc.want)
			}
		})
	}
}

func TestCheckEnumIgnoresOrder(t *testing.T) {
	t.Parallel()
	pointer := []string{"$defs", "Channel", "enum"}
	err := CheckEnum("common/v1/ids.schema.json", pointer, []string{"webrtc", "long_form", "telephony"})
	if err != nil {
		t.Fatalf("a complete set in a different order was rejected: %v", err)
	}
}

func TestCheckEnumNamesEveryMissingAndExtraMember(t *testing.T) {
	t.Parallel()
	pointer := []string{"$defs", "Channel", "enum"}
	err := CheckEnum("common/v1/ids.schema.json", pointer, []string{"webrtc", "carrier_pigeon", "telephony"})
	if err == nil {
		t.Fatal("a drifted set was accepted")
	}
	for _, want := range []string{"[long_form]", "[carrier_pigeon]", "ids.schema.json"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("error %q does not mention %s", err, want)
		}
	}
}

func TestCheckEnumFailsWhenTheSchemaCannotBeRead(t *testing.T) {
	t.Parallel()
	if err := CheckEnum("common/v1/nope.schema.json", []string{"$defs"}, nil); err == nil {
		t.Fatal("a missing schema was reported as matching")
	}
}

func TestNamesConvertsATypedSlice(t *testing.T) {
	t.Parallel()
	type colour string
	got := Names([]colour{"red", "green"})
	if !slices.Equal(got, []string{"red", "green"}) {
		t.Fatalf("Names = %v", got)
	}
	if got := Names[colour](nil); len(got) != 0 {
		t.Fatalf("Names(nil) = %v, want empty", got)
	}
}
