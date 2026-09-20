package config

import (
	"bytes"
	"encoding/json"

	"github.com/punk-raven/dafter/go/internal/errs"
)

func LoadCatalog(raw []byte) (*Catalog, error) {
	var c Catalog
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if err := d.Decode(&c); err != nil {
		return nil, errs.Wrap(errs.CodeInvalidConfig, err, "decode config catalog")
	}
	if len(c.Defaults) == 0 {
		return nil, errs.Errorf(errs.CodeInvalidConfig, "the catalog carries no defaults layer")
	}
	for channel := range c.Channels {
		if !channel.Valid() {
			return nil, errs.Errorf(errs.CodeInvalidConfig,
				"the catalog carries a channel overlay that no session could ever select")
		}
	}
	return &c, nil
}
