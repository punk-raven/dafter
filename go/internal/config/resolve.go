package config

import (
	"encoding/json"
	"fmt"

	"github.com/punk-raven/dafter/go/internal/errs"
)

type Catalog struct {
	Defaults  json.RawMessage
	Tenants   map[string]json.RawMessage
	Profiles  map[string]json.RawMessage
	Languages map[string]json.RawMessage
	Channels  map[Channel]json.RawMessage
}

type Request struct {
	SessionID string
	TenantID  string
	Profile   string
	Language  string
	Channel   Channel
	Overrides json.RawMessage
}

type Resolution struct {
	Config   *ResolvedSessionConfig
	Document []byte
	Hash     string
}

var reservedOverrides = []string{"sessionId", "tenantId", "configHash"}

func (c *Catalog) Resolve(req Request) (*Resolution, error) {
	doc, err := c.compose(req)
	if err != nil {
		return nil, err
	}

	doc["sessionId"] = req.SessionID
	doc["tenantId"] = req.TenantID
	doc["language"] = req.Language
	doc["channel"] = string(req.Channel)

	raw, err := json.Marshal(doc)
	if err != nil {
		return nil, errs.Wrap(errs.CodeInternal, err, "marshal resolved document")
	}
	document, hash, err := Seal(raw)
	if err != nil {
		return nil, err
	}
	cfg, err := Parse(document)
	if err != nil {
		return nil, err
	}
	return &Resolution{Config: cfg, Document: document, Hash: hash}, nil
}

type source struct {
	name string
	raw  json.RawMessage
}

func (c *Catalog) compose(req Request) (map[string]any, error) {
	sources := []source{{"defaults", c.Defaults}}
	var problems []string

	if raw, ok := c.Tenants[req.TenantID]; ok {
		sources = append(sources, source{"tenant", raw})
	} else {
		problems = append(problems, located("/tenantId", "no tenant configuration is registered"))
	}
	if req.Profile != "" {
		if raw, ok := c.Profiles[req.Profile]; ok {
			sources = append(sources, source{"profile", raw})
		} else {
			problems = append(problems, located("/profile", "no profile of that name is registered"))
		}
	}
	if len(req.Overrides) > 0 {
		sources = append(sources, source{"overrides", req.Overrides})
		problems = append(problems, reservedProblems(req.Overrides)...)
	}
	if len(problems) > 0 {
		return nil, detailed(errs.CodeInvalidConfig, problems, "%d layer(s) could not be resolved")
	}

	if raw, ok := c.Languages[req.Language]; ok {
		sources = append(sources, source{"language overlay", raw})
	} else {
		problems = append(problems, located("/language",
			"no language overlay is configured, so a turn strategy cannot be resolved for this language"))
	}
	if raw, ok := c.Channels[req.Channel]; ok {
		sources = append(sources, source{"channel overlay", raw})
	} else {
		problems = append(problems, located("/channel",
			"no channel overlay is configured, so turn constants cannot be resolved for this channel"))
	}
	if len(problems) > 0 {
		return nil, detailed(errs.CodeUnsupportedCapability, problems, "%d composition axis/axes could not be resolved")
	}

	doc := map[string]any{}
	for _, s := range sources {
		var m map[string]any
		if err := json.Unmarshal(s.raw, &m); err != nil {
			return nil, errs.Wrap(errs.CodeInvalidConfig, err, "the %s layer is not a JSON object", s.name)
		}
		doc = merge(doc, m)
	}
	return doc, nil
}

func reservedProblems(overrides json.RawMessage) []string {
	var m map[string]json.RawMessage
	if json.Unmarshal(overrides, &m) != nil {
		return nil
	}
	var problems []string
	for _, k := range reservedOverrides {
		if _, ok := m[k]; ok {
			problems = append(problems, located("/"+k, "is minted by the control plane and cannot be supplied as a session override"))
		}
	}
	return problems
}

func merge(base, over map[string]any) map[string]any {
	out := make(map[string]any, len(base)+len(over))
	for k, v := range base {
		out[k] = v
	}
	for k, v := range over {
		bm, baseIsObject := out[k].(map[string]any)
		om, overIsObject := v.(map[string]any)
		if baseIsObject && overIsObject {
			out[k] = merge(bm, om)
			continue
		}
		out[k] = v
	}
	return out
}

func located(pointer, because string) string {
	return fmt.Sprintf("at '%s': %s", pointer, because)
}

func detailed(code errs.ErrorCode, problems []string, format string) *errs.Error {
	e := errs.Errorf(code, format, len(problems))
	e.Details = problems
	return e
}
