package schema

import (
	"encoding/json"
	"fmt"
	"slices"
)

func EnumAt(file string, keys ...string) ([]string, error) {
	b, err := Raw(file)
	if err != nil {
		return nil, fmt.Errorf("schema: read %s: %w (run `make generate`)", file, err)
	}
	var doc map[string]any
	if err := json.Unmarshal(b, &doc); err != nil {
		return nil, fmt.Errorf("schema: parse %s: %w", file, err)
	}
	cur := any(doc)
	for _, k := range keys {
		m, ok := cur.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("schema: %s: %v is not an object", file, keys)
		}
		cur = m[k]
	}
	raw, ok := cur.([]any)
	if !ok {
		return nil, fmt.Errorf("schema: %s: %v is not an enum", file, keys)
	}
	out := make([]string, 0, len(raw))
	for _, v := range raw {
		s, ok := v.(string)
		if !ok {
			return nil, fmt.Errorf("schema: %s: enum has a non-string member", file)
		}
		out = append(out, s)
	}
	slices.Sort(out)
	return out, nil
}
