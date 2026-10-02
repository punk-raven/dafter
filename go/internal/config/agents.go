package config

import (
	"bytes"
	"encoding/json"
	"errors"
	"maps"
	"regexp"
	"slices"
	"strings"

	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/schema"
)

var AgentSlug = regexp.MustCompile(`^[a-z][a-z0-9-]{1,31}$`)

const nearMissesPointer = "/agent/addressing/nearMisses"

type AgentDefinition struct {
	Name       string   `json:"name"`
	Aliases    []string `json:"aliases,omitempty"`
	NearMisses []string `json:"nearMisses,omitempty"`
	Profile    string   `json:"profile,omitempty"`
}

func ParseAgentDefinition(raw []byte) (*AgentDefinition, error) {
	if err := schema.ValidateDocument(schema.AgentDefinition, raw, errs.CodeInvalidConfig); err != nil {
		return nil, err
	}
	var a AgentDefinition
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if err := d.Decode(&a); err != nil {
		return nil, errs.Wrap(errs.CodeInvalidConfig, err, "decode agent definition")
	}
	return &a, nil
}

func (a *AgentDefinition) layer() (json.RawMessage, error) {
	addressing := map[string]any{"aliases": nonNil(a.Aliases)}
	if len(a.NearMisses) > 0 {
		addressing["nearMisses"] = a.NearMisses
	}
	raw, err := json.Marshal(map[string]any{"agent": map[string]any{"name": a.Name, "addressing": addressing}})
	if err != nil {
		return nil, errs.Wrap(errs.CodeInternal, err, "marshal agent layer")
	}
	return raw, nil
}

func nonNil(list []string) []string {
	if list == nil {
		return []string{}
	}
	return list
}

func (c *Catalog) checkAgents() error {
	var problems []string
	for _, slug := range slices.Sorted(maps.Keys(c.Agents)) {
		at := "/agents/" + pointerEscaper.Replace(slug)
		if !AgentSlug.MatchString(slug) {
			problems = append(problems, located(at, "an agent is stored under a lowercase slug of 2 to 32 letters, digits and hyphens"))
			continue
		}
		if _, err := ParseAgentDefinition(c.Agents[slug]); err != nil {
			problems = append(problems, prefixed(at, err)...)
		}
	}
	if len(problems) > 0 {
		return detailed(errs.CodeInvalidConfig, problems, "the catalog carries %d agent problem(s)")
	}
	return nil
}

func prefixed(at string, err error) []string {
	var de *errs.Error
	if !errors.As(err, &de) || len(de.Details) == 0 {
		return []string{located(at, "is not a valid document")}
	}
	out := make([]string, 0, len(de.Details))
	for _, d := range de.Details {
		if rest, ok := strings.CutPrefix(d, "at '"); ok {
			out = append(out, "at '"+at+rest)
			continue
		}
		out = append(out, located(at, d))
	}
	return out
}

func (c *Catalog) agentLayer(slug string) (source, string, []string) {
	raw, ok := c.Agents[slug]
	if !ok {
		return source{}, "", []string{located("/agent", "no agent of that name is registered")}
	}
	def, err := ParseAgentDefinition(raw)
	if err != nil {
		return source{}, "", prefixed("/agents/"+pointerEscaper.Replace(slug), err)
	}
	layer, err := def.layer()
	if err != nil {
		return source{}, "", []string{located("/agent", "could not be composed")}
	}
	return source{name: slug + " agent", raw: layer}, def.Profile, nil
}

func unionNearMisses(base, over any) (any, bool) {
	b, baseIsList := base.([]any)
	o, overIsList := over.([]any)
	if !baseIsList || !overIsList {
		return nil, false
	}
	out := slices.Clone(b)
	for _, v := range o {
		if !slices.Contains(out, v) {
			out = append(out, v)
		}
	}
	return out, true
}

func keptAll(over, doc any) bool {
	o, overIsList := over.([]any)
	d, docIsList := doc.([]any)
	if !overIsList || !docIsList {
		return false
	}
	for _, v := range o {
		if !slices.Contains(d, v) {
			return false
		}
	}
	return true
}
