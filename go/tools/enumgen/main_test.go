package main

import (
	"bytes"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"testing"
)

// The real schemas, relative to this package: enumgen runs from the go module
// root with ../schemas, and the test runs from go/tools/enumgen.
const realSchemas = "../../../schemas"

func TestGoNameCamelCasesAndKeepsInitialisms(t *testing.T) {
	t.Parallel()
	cases := []struct{ prefix, value, want string }{
		{"Channel", "webrtc", "ChannelWebRTC"},
		{"Channel", "long_form", "ChannelLongForm"},
		{"Event", "agent.state_changed", "EventAgentStateChanged"},
		{"Layout", "room-composite", "LayoutRoomComposite"},
		{"Stage", "STT", "StageSTT"},
		{"Code", "vad_id_url_json", "CodeVADIDURLJSON"},
		{"Turn", "auto", "TurnAuto"},
	}
	for _, tc := range cases {
		if got := goName(tc.prefix, tc.value); got != tc.want {
			t.Errorf("goName(%q, %q) = %q, want %q", tc.prefix, tc.value, got, tc.want)
		}
	}
}

func TestPyNameIsUpperSnake(t *testing.T) {
	t.Parallel()
	cases := []struct{ value, want string }{
		{"webrtc", "WEBRTC"},
		{"long_form", "LONG_FORM"},
		{"agent.state_changed", "AGENT_STATE_CHANGED"},
		{"room-composite", "ROOM_COMPOSITE"},
		{"mp4v2", "MP4V2"},
	}
	for _, tc := range cases {
		if got := pyName(tc.value); got != tc.want {
			t.Errorf("pyName(%q) = %q, want %q", tc.value, got, tc.want)
		}
	}
}

func writeSchema(t *testing.T, root, name, body string) {
	t.Helper()
	p := filepath.Join(root, name)
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestEnumAtReadsMembersInSchemaOrder(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	writeSchema(t, root, "x/v1/x.schema.json", `{"$defs":{"Colour":{"enum":["red","green","blue"]}}}`)
	got, err := enumAt(root, target{Schema: "x/v1/x.schema.json", Pointer: []string{"$defs", "Colour", "enum"}})
	if err != nil {
		t.Fatal(err)
	}
	if want := []string{"red", "green", "blue"}; !slices.Equal(got, want) {
		t.Fatalf("enumAt = %v, want %v (schema order)", got, want)
	}
}

func TestEnumAtRejectsWhatIsNotAStringEnum(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	writeSchema(t, root, "x.schema.json", `{
		"$defs": {
			"Ok":       {"enum": ["a"]},
			"Object":   {"enum": {"a": 1}},
			"Numbers":  {"enum": [1, 2]},
			"Empty":    {"enum": []},
			"Scalar":   "leaf"
		}
	}`)
	writeSchema(t, root, "broken.schema.json", `{"$defs": [`)
	cases := []struct {
		name    string
		schema  string
		pointer []string
		want    string
	}{
		{"missing file", "nope.schema.json", []string{"$defs"}, "no such file"},
		{"malformed json", "broken.schema.json", []string{"$defs"}, "unexpected end of JSON"},
		{"missing key", "x.schema.json", []string{"$defs", "Nope", "enum"}, `no "Nope"`},
		{"path through a scalar", "x.schema.json", []string{"$defs", "Scalar", "enum"}, "is not an object"},
		{"enum that is an object", "x.schema.json", []string{"$defs", "Object", "enum"}, "is not an enum"},
		{"non-string member", "x.schema.json", []string{"$defs", "Numbers", "enum"}, "non-string member"},
		{"empty enum", "x.schema.json", []string{"$defs", "Empty", "enum"}, "is empty"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			_, err := enumAt(root, target{Schema: tc.schema, Pointer: tc.pointer})
			if err == nil {
				t.Fatalf("enumAt(%s, %v) accepted", tc.schema, tc.pointer)
			}
			if !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("enumAt(%s, %v) = %q, want it to mention %q", tc.schema, tc.pointer, err, tc.want)
			}
		})
	}
}

// parseGo returns the file and the names of every top-level declaration.
func parseGo(t *testing.T, src []byte) (*ast.File, map[string]bool) {
	t.Helper()
	f, err := parser.ParseFile(token.NewFileSet(), "gen.go", src, parser.ParseComments)
	if err != nil {
		t.Fatalf("generated Go does not parse: %v\n%s", err, src)
	}
	names := map[string]bool{}
	for _, d := range f.Decls {
		switch d := d.(type) {
		case *ast.GenDecl:
			for _, s := range d.Specs {
				switch s := s.(type) {
				case *ast.TypeSpec:
					names[s.Name.Name] = true
				case *ast.ValueSpec:
					for _, n := range s.Names {
						names[n.Name] = true
					}
				}
			}
		case *ast.FuncDecl:
			names[d.Name.Name] = true
		}
	}
	return f, names
}

func TestRenderProducesTheTypeItsMembersAndValid(t *testing.T) {
	t.Parallel()
	tg := target{
		Schema: "x/v1/x.schema.json", Package: "paint", Type: "Colour", Prefix: "Colour",
		Out: "internal/paint/colours_gen.go", AllName: "AllColours",
	}
	src, err := render(tg, []string{"red", "dark_blue"})
	if err != nil {
		t.Fatal(err)
	}
	f, names := parseGo(t, src)
	if f.Name.Name != "paint" {
		t.Errorf("package %s, want paint", f.Name.Name)
	}
	for _, want := range []string{"Colour", "ColourRed", "ColourDarkBlue", "AllColours", "Valid"} {
		if !names[want] {
			t.Errorf("generated file lacks %s:\n%s", want, src)
		}
	}
	if !bytes.HasPrefix(src, []byte("// Code generated by tools/enumgen from schemas/x/v1/x.schema.json. DO NOT EDIT.")) {
		t.Errorf("missing the generated-code header:\n%s", src)
	}
	// gofmt aligns the const block, so match on collapsed whitespace.
	flat := strings.Join(strings.Fields(string(src)), " ")
	for _, want := range []string{`ColourRed Colour = "red"`, `ColourDarkBlue Colour = "dark_blue"`, "func (v Colour) Valid() bool"} {
		if !strings.Contains(flat, want) {
			t.Errorf("generated file lacks %q:\n%s", want, src)
		}
	}
}

func TestRenderPythonEmitsOneClassPerTypeInTargetOrder(t *testing.T) {
	t.Parallel()
	order := []target{
		{Type: "Colour"}, {Type: "Shape"}, {Type: "Colour"}, // a type listed twice renders once
	}
	all := map[string][]string{
		"Colour": {"red", "dark_blue"},
		"Shape":  {"round"},
	}
	got := string(renderPython(all, order))
	want := "\"\"\"Generated by tools/enumgen from schemas/. DO NOT EDIT.\"\"\"\n\n" +
		"from __future__ import annotations\n\n" +
		"from enum import StrEnum\n\n" +
		"\nclass Colour(StrEnum):\n    RED = \"red\"\n    DARK_BLUE = \"dark_blue\"\n" +
		"\nclass Shape(StrEnum):\n    ROUND = \"round\"\n"
	if got != want {
		t.Fatalf("renderPython =\n%s\nwant\n%s", got, want)
	}
}

func TestEveryTargetResolvesAgainstTheRealSchemas(t *testing.T) {
	t.Parallel()
	if _, err := os.Stat(realSchemas); err != nil {
		t.Skipf("schemas/ not found at %s: %v", realSchemas, err)
	}
	for _, tg := range targets {
		t.Run(tg.Type, func(t *testing.T) {
			t.Parallel()
			values, err := enumAt(realSchemas, tg)
			if err != nil {
				t.Fatal(err)
			}
			if got, want := tg.Package, path.Base(path.Dir(tg.Out)); got != want {
				t.Errorf("package %s does not match the directory of %s", got, tg.Out)
			}
			if !strings.HasSuffix(tg.Out, "_gen.go") {
				t.Errorf("%s is not named *_gen.go, so generate-check and .gitignore miss it", tg.Out)
			}
			// A collision here is a Go compile error, or a Python attribute
			// silently overwriting another; catch it before either.
			goSeen, pySeen := map[string]string{}, map[string]string{}
			for _, v := range values {
				if prev, dup := goSeen[goName(tg.Prefix, v)]; dup {
					t.Errorf("%q and %q both become Go %s", prev, v, goName(tg.Prefix, v))
				}
				if prev, dup := pySeen[pyName(v)]; dup {
					t.Errorf("%q and %q both become Python %s", prev, v, pyName(v))
				}
				goSeen[goName(tg.Prefix, v)], pySeen[pyName(v)] = v, v
			}
		})
	}
}

func TestRunWritesEveryOutputFromTheRealSchemas(t *testing.T) {
	t.Parallel()
	if _, err := os.Stat(realSchemas); err != nil {
		t.Skipf("schemas/ not found at %s: %v", realSchemas, err)
	}
	goRoot := t.TempDir()
	for _, tg := range targets {
		if err := os.MkdirAll(filepath.Join(goRoot, filepath.Dir(tg.Out)), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	pyOut := filepath.Join(t.TempDir(), "enums.py")
	report, err := run(realSchemas, goRoot, pyOut)
	if err != nil {
		t.Fatal(err)
	}
	reported := strings.Join(report, "\n")

	for _, tg := range targets {
		src, err := os.ReadFile(filepath.Join(goRoot, tg.Out))
		if err != nil {
			t.Errorf("%s was not written: %v", tg.Out, err)
			continue
		}
		f, names := parseGo(t, src)
		if f.Name.Name != tg.Package {
			t.Errorf("%s: package %s, want %s", tg.Out, f.Name.Name, tg.Package)
		}
		if !names[tg.Type] || !names[tg.AllName] {
			t.Errorf("%s: lacks %s or %s", tg.Out, tg.Type, tg.AllName)
		}
		if !strings.Contains(reported, tg.Out) {
			t.Errorf("report does not mention %s:\n%s", tg.Out, reported)
		}
	}

	py, err := os.ReadFile(pyOut)
	if err != nil {
		t.Fatal(err)
	}
	classes := regexp.MustCompile(`(?m)^class (\w+)\(StrEnum\):$`).FindAllStringSubmatch(string(py), -1)
	var got []string
	for _, m := range classes {
		got = append(got, m[1])
	}
	var want []string
	for _, tg := range targets {
		if !slices.Contains(want, tg.Type) {
			want = append(want, tg.Type)
		}
	}
	if !slices.Equal(got, want) {
		t.Errorf("Python classes %v, want %v", got, want)
	}
}

func TestRunReportsAnUnreadableSchema(t *testing.T) {
	t.Parallel()
	_, err := run(t.TempDir(), t.TempDir(), filepath.Join(t.TempDir(), "enums.py"))
	if err == nil {
		t.Fatal("run succeeded with no schemas")
	}
	if !strings.Contains(err.Error(), targets[0].Schema) {
		t.Fatalf("error %q does not name the schema it could not read", err)
	}
}
