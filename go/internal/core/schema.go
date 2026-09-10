package core

import (
	"encoding/json"
	"fmt"
	"io/fs"
	"path"
	"strings"
	"sync"

	"github.com/santhosh-tekuri/jsonschema/v6"

	"github.com/punk-raven/dafter/go/internal/core/schemagen"
)

const (
	SchemaResolvedSessionConfig = "https://schemas.dafter.dev/config/v1/resolved-session-config.schema.json"
	SchemaEventEnvelope         = "https://schemas.dafter.dev/events/v1/envelope.schema.json"
	SchemaError                 = "https://schemas.dafter.dev/errors/v1/error.schema.json"
)

var (
	compileOnce sync.Once
	compiled    map[string]*jsonschema.Schema
	compileErr  error
)

func compileAll() {
	c := jsonschema.NewCompiler()
	c.AssertFormat()

	ids, err := walkSchemas(func(id string, doc any) error {
		return c.AddResource(id, doc)
	})
	if err != nil {
		compileErr = err
		return
	}

	compiled = make(map[string]*jsonschema.Schema, len(ids))
	for _, id := range ids {
		s, err := c.Compile(id)
		if err != nil {
			compileErr = fmt.Errorf("core: compile schema %s: %w", id, err)
			return
		}
		compiled[id] = s
	}
}

func walkSchemas(visit func(id string, doc any) error) ([]string, error) {
	var ids []string
	err := fs.WalkDir(schemagen.FS, ".", func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() || !strings.HasSuffix(path.Base(p), ".schema.json") {
			return nil
		}
		b, err := schemagen.FS.ReadFile(p)
		if err != nil {
			return fmt.Errorf("core: read %s: %w", p, err)
		}
		doc, err := jsonschema.UnmarshalJSON(strings.NewReader(string(b)))
		if err != nil {
			return fmt.Errorf("core: parse %s: %w", p, err)
		}
		obj, ok := doc.(map[string]any)
		if !ok {
			return fmt.Errorf("core: %s is not a JSON object", p)
		}
		id, ok := obj["$id"].(string)
		if !ok || id == "" {
			return fmt.Errorf("core: %s has no $id", p)
		}
		if err := visit(id, doc); err != nil {
			return fmt.Errorf("core: register %s: %w", p, err)
		}
		ids = append(ids, id)
		return nil
	})
	if err != nil {
		return nil, err
	}
	if len(ids) == 0 {
		return nil, fmt.Errorf("core: no schemas embedded - run `make generate`")
	}
	return ids, nil
}

func SchemaFor(id string) (*jsonschema.Schema, error) {
	compileOnce.Do(compileAll)
	if compileErr != nil {
		return nil, compileErr
	}
	s, ok := compiled[id]
	if !ok {
		return nil, fmt.Errorf("core: no schema registered for %s", id)
	}
	return s, nil
}

func ValidateAgainst(id string, v any) error {
	s, err := SchemaFor(id)
	if err != nil {
		return err
	}
	b, err := json.Marshal(v)
	if err != nil {
		return Wrap(CodeInternal, err, "marshal value for schema validation")
	}
	doc, err := jsonschema.UnmarshalJSON(strings.NewReader(string(b)))
	if err != nil {
		return Wrap(CodeInternal, err, "reparse value for schema validation")
	}
	if err := s.Validate(doc); err != nil {
		return Wrap(CodeInvalidConfig, err, "document does not satisfy %s", id)
	}
	return nil
}

func (e *EventEnvelope) Validate() error {
	return ValidateAgainst(SchemaEventEnvelope, e)
}

func (c *ResolvedSessionConfig) Validate() error {
	if err := ValidateAgainst(SchemaResolvedSessionConfig, c); err != nil {
		return err
	}
	if c.PrivacyMode == PrivacySealed && c.Agent.Enabled {
		return Errorf(CodePrivacyModeForbids,
			"session %s is sealed, so an agent cannot be dispatched into it", c.SessionID)
	}
	if c.Recording.Enabled && c.Recording.ConsentArtifactID == "" {
		return Errorf(CodeConsentRequired,
			"session %s enables recording without a consent artifact", c.SessionID)
	}
	if c.Recording.Enabled &&
		c.Recording.StartAt == StartAtSessionCreate &&
		c.Recording.Layout != LayoutRoomComposite {
		return Errorf(CodeInvalidConfig,
			"session %s asks for capture at session creation with layout %q, but a track egress "+
				"attaches to a published track and cannot start before one exists", c.SessionID, c.Recording.Layout)
	}
	return nil
}
