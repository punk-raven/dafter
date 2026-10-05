package config

import (
	"encoding/json"
	"maps"
	"slices"

	"github.com/punk-raven/dafter/go/internal/errs"
)

type Kind string

const (
	KindDefaults  Kind = "defaults"
	KindLLMs      Kind = "llms"
	KindAgents    Kind = "agents"
	KindTenants   Kind = "tenants"
	KindProfiles  Kind = "profiles"
	KindLanguages Kind = "languages"
	KindChannels  Kind = "channels"
)

const DefaultsName = "defaults"

var (
	GitKinds      = []Kind{KindDefaults, KindLLMs}
	EditableKinds = []Kind{KindAgents, KindTenants, KindProfiles, KindLanguages, KindChannels}
)

func (k Kind) Known() bool {
	return slices.Contains(GitKinds, k) || slices.Contains(EditableKinds, k)
}

func (k Kind) Editable() bool {
	return slices.Contains(EditableKinds, k)
}

type Documents map[Kind]map[string]json.RawMessage

func (d Documents) Set(kind Kind, name string, doc json.RawMessage) {
	if d[kind] == nil {
		d[kind] = map[string]json.RawMessage{}
	}
	d[kind][name] = doc
}

func (d Documents) Clone() Documents {
	out := make(Documents, len(d))
	for kind, byName := range d {
		out[kind] = maps.Clone(byName)
	}
	return out
}

func (d Documents) Names(kind Kind) []string {
	return slices.Sorted(maps.Keys(d[kind]))
}

type Snapshot struct {
	Catalog *Catalog
	Release int64
}

func (c *Catalog) Snapshot() Snapshot {
	return Snapshot{Catalog: c}
}

func (c *Catalog) Documents() (Documents, error) {
	docs := Documents{}
	docs.Set(KindDefaults, DefaultsName, c.Defaults)
	for name, raw := range c.LLMs {
		docs.Set(KindLLMs, name, raw)
	}
	for name, raw := range c.Agents {
		docs.Set(KindAgents, name, raw)
	}
	for name, raw := range c.Tenants {
		docs.Set(KindTenants, name, raw)
	}
	for name, raw := range c.Profiles {
		docs.Set(KindProfiles, name, raw)
	}
	for name, axis := range c.Languages {
		raw, err := json.Marshal(axis)
		if err != nil {
			return nil, errs.Wrap(errs.CodeInternal, err, "marshal language overlay")
		}
		docs.Set(KindLanguages, name, raw)
	}
	for channel, axis := range c.Channels {
		raw, err := json.Marshal(axis)
		if err != nil {
			return nil, errs.Wrap(errs.CodeInternal, err, "marshal channel overlay")
		}
		docs.Set(KindChannels, string(channel), raw)
	}
	return docs, nil
}

func Assemble(docs Documents) (*Catalog, error) {
	for kind := range docs {
		if !kind.Known() {
			return nil, errs.Errorf(errs.CodeInvalidConfig, "the document set carries a kind no catalog has")
		}
	}
	defaults, ok := docs[KindDefaults][DefaultsName]
	if !ok {
		return nil, errs.Errorf(errs.CodeInvalidConfig, "the document set carries no defaults layer")
	}
	whole := map[string]any{"defaults": defaults}
	for _, kind := range []Kind{KindLLMs, KindAgents, KindTenants, KindProfiles, KindLanguages, KindChannels} {
		byName := map[string]json.RawMessage{}
		for name, raw := range docs[kind] {
			byName[name] = raw
		}
		whole[string(kind)] = byName
	}
	raw, err := json.Marshal(whole)
	if err != nil {
		return nil, errs.Wrap(errs.CodeInvalidConfig, err, "a stored document is not JSON")
	}
	return LoadCatalog(raw)
}
