package config

import (
	"bytes"
	"encoding/json"
	"maps"
	"regexp"
	"slices"
	"strings"

	"github.com/punk-raven/dafter/go/internal/errs"
)

const llmPointer = "/agent/pipeline/llm"

var llmRouteName = regexp.MustCompile(`^[a-z][a-z0-9_]{1,31}/[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$`)

func (c *Catalog) checkLLMRoutes() error {
	var problems []string
	for _, name := range slices.Sorted(maps.Keys(c.LLMs)) {
		var ref ProviderRef
		d := json.NewDecoder(bytes.NewReader(c.LLMs[name]))
		d.DisallowUnknownFields()
		if err := d.Decode(&ref); err != nil {
			problems = append(problems, located("/llms/"+pointerEscaper.Replace(name), "is not a provider reference"))
			continue
		}
		if !llmRouteName.MatchString(name) || name != ref.Provider+"/"+ref.Model {
			problems = append(problems, located("/llms/"+pointerEscaper.Replace(name),
				"a route is named provider/model after the reference it pins"))
		}
	}
	if len(problems) > 0 {
		return detailed(errs.CodeInvalidConfig, problems, "the catalog carries %d LLM route(s) no session could run")
	}
	return nil
}

func (c *Catalog) llmRoute(name string) (source, []string) {
	raw, ok := c.LLMs[name]
	if !ok {
		return source{}, []string{located("/llm", "no LLM route of that name is registered")}
	}
	return source{name: name + " LLM route", raw: raw, replaces: llmPointer}, nil
}

func routeProblems(overrides json.RawMessage) []string {
	var m map[string]json.RawMessage
	if json.Unmarshal(overrides, &m) != nil {
		return nil
	}
	if _, ok := m["llm"]; ok {
		return []string{located("/llm", "is chosen by the session request's llm field, which also lands the route, never by an override")}
	}
	return nil
}

func (s source) pin() (map[string]any, error) {
	if s.replaces == "" {
		return decode(s)
	}
	var value any
	if err := json.Unmarshal(s.raw, &value); err != nil {
		return nil, errs.Wrap(errs.CodeInvalidConfig, err, "the %s is not JSON", s.name)
	}
	tokens := strings.Split(strings.TrimPrefix(s.replaces, "/"), "/")
	for i := len(tokens) - 1; i >= 0; i-- {
		value = map[string]any{tokens[i]: value}
	}
	return value.(map[string]any), nil
}

func (s source) pins(pointer string) bool {
	return s.replaces != "" && (pointer == s.replaces || strings.HasPrefix(pointer, s.replaces+"/"))
}

func cut(doc map[string]any, pointer string) map[string]any {
	if pointer == "" {
		return doc
	}
	return cutTokens(doc, strings.Split(strings.TrimPrefix(pointer, "/"), "/"))
}

func cutTokens(m map[string]any, tokens []string) map[string]any {
	out := maps.Clone(m)
	if len(tokens) == 1 {
		delete(out, tokens[0])
		return out
	}
	if child, ok := m[tokens[0]].(map[string]any); ok {
		out[tokens[0]] = cutTokens(child, tokens[1:])
	}
	return out
}
