package configstore

import (
	"cmp"
	"slices"

	"github.com/punk-raven/dafter/go/internal/config"
)

type Removal struct {
	Kind     config.Kind `json:"kind"`
	Name     string      `json:"name"`
	Last     Revision    `json:"last"`
	Deletion Revision    `json:"deletion"`
}

func Removed(trail []Revision) []Removal {
	type ends struct{ last, head Revision }
	byName := map[string]*ends{}
	for _, r := range oldestFirst(trail) {
		e, ok := byName[r.Name]
		if !ok {
			e = &ends{}
			byName[r.Name] = e
		}
		e.head = r
		if !r.Deleted {
			e.last = r
		}
	}
	out := []Removal{}
	for name, e := range byName {
		if e.head.Deleted && e.last.ID != 0 {
			out = append(out, Removal{Kind: e.head.Kind, Name: name, Last: e.last, Deletion: e.head})
		}
	}
	slices.SortFunc(out, func(a, b Removal) int { return cmp.Compare(b.Deletion.ID, a.Deletion.ID) })
	return out
}

func Trail(trail []Revision, name string) []Revision {
	out := []Revision{}
	for _, r := range oldestFirst(trail) {
		if r.Name == name {
			out = append(out, r)
		}
	}
	return out
}

func oldestFirst(trail []Revision) []Revision {
	return slices.SortedFunc(slices.Values(trail), func(a, b Revision) int { return cmp.Compare(a.ID, b.ID) })
}
