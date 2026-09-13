package schema

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"math/big"
	"path"
	"sort"
	"strings"

	"github.com/santhosh-tekuri/jsonschema/v6"
	"github.com/santhosh-tekuri/jsonschema/v6/kind"
	"golang.org/x/text/language"
	"golang.org/x/text/message"

	"github.com/punk-raven/dafter/go/internal/errs"
)

const (
	ResolvedSessionConfig = "https://schemas.dafter.dev/config/v1/resolved-session-config.schema.json"
	EventEnvelope         = "https://schemas.dafter.dev/events/v1/envelope.schema.json"
	Error                 = "https://schemas.dafter.dev/errors/v1/error.schema.json"
)

type Validator struct {
	compiled map[string]*jsonschema.Schema
}

func New() (*Validator, error) {
	c := jsonschema.NewCompiler()
	c.AssertFormat()

	ids, err := walkSchemas(func(id string, doc any) error {
		return c.AddResource(id, doc)
	})
	if err != nil {
		return nil, err
	}

	v := &Validator{compiled: make(map[string]*jsonschema.Schema, len(ids))}
	for _, id := range ids {
		s, err := c.Compile(id)
		if err != nil {
			return nil, fmt.Errorf("schema: compile %s: %w", id, err)
		}
		v.compiled[id] = s
	}
	return v, nil
}

var Default = func() *Validator {
	v, err := New()
	if err != nil {
		panic(err)
	}
	return v
}()

func walkSchemas(visit func(id string, doc any) error) ([]string, error) {
	var ids []string
	err := fs.WalkDir(schemasFS, ".", func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() || !strings.HasSuffix(path.Base(p), ".schema.json") {
			return nil
		}
		b, err := schemasFS.ReadFile(p)
		if err != nil {
			return fmt.Errorf("schema: read %s: %w", p, err)
		}
		doc, err := jsonschema.UnmarshalJSON(strings.NewReader(string(b)))
		if err != nil {
			return fmt.Errorf("schema: parse %s: %w", p, err)
		}
		obj, ok := doc.(map[string]any)
		if !ok {
			return fmt.Errorf("schema: %s is not a JSON object", p)
		}
		id, ok := obj["$id"].(string)
		if !ok || id == "" {
			return fmt.Errorf("schema: %s has no $id", p)
		}
		if err := visit(id, doc); err != nil {
			return fmt.Errorf("schema: register %s: %w", p, err)
		}
		ids = append(ids, id)
		return nil
	})
	if err != nil {
		return nil, err
	}
	if len(ids) == 0 {
		return nil, fmt.Errorf("schema: no schemas embedded - run `make generate`")
	}
	return ids, nil
}

func (v *Validator) SchemaFor(id string) (*jsonschema.Schema, error) {
	s, ok := v.compiled[id]
	if !ok {
		return nil, fmt.Errorf("schema: no schema registered for %s", id)
	}
	return s, nil
}

func (v *Validator) ValidateAgainst(id string, doc any, code errs.ErrorCode) error {
	s, err := v.SchemaFor(id)
	if err != nil {
		return err
	}
	b, err := json.Marshal(doc)
	if err != nil {
		return errs.Wrap(errs.CodeInternal, err, "marshal value for schema validation")
	}
	parsed, err := jsonschema.UnmarshalJSON(bytes.NewReader(b))
	if err != nil {
		return errs.Wrap(errs.CodeInternal, err, "reparse value for schema validation")
	}
	if err := s.Validate(parsed); err != nil {
		problems := leafProblems(err)
		e := errs.Wrap(code, err, "%d problem(s) validating against %s", len(problems), id)
		e.Details = problems
		return e
	}
	return nil
}

var printer = message.NewPrinter(language.English)

func leafProblems(err error) []string {
	var ve *jsonschema.ValidationError
	if !errors.As(err, &ve) {
		return nil
	}
	var out []string
	seen := map[string]bool{}
	var walk func(*jsonschema.ValidationError)
	walk = func(e *jsonschema.ValidationError) {
		if len(e.Causes) == 0 {
			p := fmt.Sprintf("at '%s': %s", pointer(e.InstanceLocation), rule(e.ErrorKind))
			if !seen[p] {
				seen[p] = true
				out = append(out, p)
			}
			return
		}
		for _, c := range e.Causes {
			walk(c)
		}
	}
	walk(ve)
	sort.Strings(out)
	return out
}

var pointerEscaper = strings.NewReplacer("~", "~0", "/", "~1")

func pointer(tokens []string) string {
	var sb strings.Builder
	for _, tok := range tokens {
		sb.WriteByte('/')
		sb.WriteString(pointerEscaper.Replace(tok))
	}
	return sb.String()
}

func rule(k jsonschema.ErrorKind) string {
	switch k := k.(type) {
	case *kind.Pattern:
		return fmt.Sprintf("does not match pattern '%s'", k.Want)
	case *kind.Format:
		return fmt.Sprintf("is not a valid %s", k.Want)
	case *kind.Minimum:
		return "minimum: want " + bound(k.Want)
	case *kind.Maximum:
		return "maximum: want " + bound(k.Want)
	case *kind.ExclusiveMinimum:
		return "exclusiveMinimum: want " + bound(k.Want)
	case *kind.ExclusiveMaximum:
		return "exclusiveMaximum: want " + bound(k.Want)
	case *kind.MultipleOf:
		return "multipleOf: want " + bound(k.Want)
	default:
		return k.LocalizedString(printer)
	}
}

func bound(r *big.Rat) string {
	f, _ := r.Float64()
	return fmt.Sprintf("%v", f)
}

func Raw(p string) ([]byte, error) {
	return schemasFS.ReadFile(path.Join("schemas", p))
}

func ValidateAgainst(id string, doc any, code errs.ErrorCode) error {
	return Default.ValidateAgainst(id, doc, code)
}

// Validate the document before decoding it: decoding first drops fields the
// struct does not declare, so the schema never sees them.
func (v *Validator) ValidateDocument(id string, raw []byte, code errs.ErrorCode) error {
	s, err := v.SchemaFor(id)
	if err != nil {
		return err
	}
	parsed, err := jsonschema.UnmarshalJSON(bytes.NewReader(raw))
	if err != nil {
		return errs.Wrap(code, err, "input is not valid JSON")
	}
	if err := s.Validate(parsed); err != nil {
		problems := leafProblems(err)
		e := errs.Wrap(code, err, "%d problem(s) validating against %s", len(problems), id)
		e.Details = problems
		return e
	}
	return nil
}

func ValidateDocument(id string, raw []byte, code errs.ErrorCode) error {
	return Default.ValidateDocument(id, raw, code)
}
