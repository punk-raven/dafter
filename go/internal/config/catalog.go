package config

import (
	"encoding/json"
	"io/fs"
	"path"
	"strings"

	"github.com/punk-raven/dafter/go/internal/errs"
)

func LoadCatalog(fsys fs.FS) (*Catalog, error) {
	defaults, err := fs.ReadFile(fsys, "defaults.json")
	if err != nil {
		return nil, errs.Wrap(errs.CodeInvalidConfig, err, "read catalog defaults")
	}
	c := &Catalog{Defaults: defaults}

	tenants, err := loadDir(fsys, "tenants")
	if err != nil {
		return nil, err
	}
	c.Tenants = tenants

	profiles, err := loadDir(fsys, "profiles")
	if err != nil {
		return nil, err
	}
	c.Profiles = profiles

	languages, err := loadDir(fsys, "languages")
	if err != nil {
		return nil, err
	}
	c.Languages = languages

	channels, err := loadDir(fsys, "channels")
	if err != nil {
		return nil, err
	}
	c.Channels = make(map[Channel]json.RawMessage, len(channels))
	for name, raw := range channels {
		ch := Channel(name)
		if !ch.Valid() {
			return nil, errs.Errorf(errs.CodeInvalidConfig, "channels/%s.json names no known channel", name)
		}
		c.Channels[ch] = raw
	}
	return c, nil
}

func loadDir(fsys fs.FS, dir string) (map[string]json.RawMessage, error) {
	entries, err := fs.ReadDir(fsys, dir)
	if err != nil {
		return nil, errs.Wrap(errs.CodeInvalidConfig, err, "read catalog directory %s", dir)
	}
	out := make(map[string]json.RawMessage, len(entries))
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".json") {
			continue
		}
		raw, err := fs.ReadFile(fsys, path.Join(dir, e.Name()))
		if err != nil {
			return nil, errs.Wrap(errs.CodeInvalidConfig, err, "read %s/%s", dir, e.Name())
		}
		out[strings.TrimSuffix(e.Name(), ".json")] = raw
	}
	return out, nil
}
