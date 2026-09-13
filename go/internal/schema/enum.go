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

func CheckEnum(file string, pointer []string, got []string) error {
	want, err := EnumAt(file, pointer...)
	if err != nil {
		return err
	}
	sorted := append([]string(nil), got...)
	slices.Sort(sorted)
	if slices.Equal(sorted, want) {
		return nil
	}
	var missing, extra []string
	for _, w := range want {
		if !slices.Contains(sorted, w) {
			missing = append(missing, w)
		}
	}
	for _, g := range sorted {
		if !slices.Contains(want, g) {
			extra = append(extra, g)
		}
	}
	return fmt.Errorf("%s %v: schema has %v that Go lacks; Go has %v that the schema lacks",
		file, pointer, missing, extra)
}

func Names[T ~string](vs []T) []string {
	out := make([]string, 0, len(vs))
	for _, v := range vs {
		out = append(out, string(v))
	}
	return out
}
