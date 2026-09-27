package config_test

import (
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
)

func TestLoadCatalogReadsOneDocument(t *testing.T) {
	t.Parallel()
	raw := []byte(`{
		"defaults": {"apiVersion": "dafter.dev/v1"},
		"tenants": {"t_9c21a4be": {}},
		"profiles": {"support": {}},
		"languages": {"hi": {}, "en-IN": {}},
		"channels": {"webrtc": {}, "telephony": {}}
	}`)
	c, err := config.LoadCatalog(raw)
	if err != nil {
		t.Fatalf("load catalog: %v", err)
	}
	if len(c.Tenants) != 1 || len(c.Profiles) != 1 || len(c.Languages) != 2 || len(c.Channels) != 2 {
		t.Fatalf("catalog loaded %d tenants, %d profiles, %d languages, %d channels",
			len(c.Tenants), len(c.Profiles), len(c.Languages), len(c.Channels))
	}
	if _, ok := c.Languages["en-IN"]; !ok {
		t.Error("a language tag with a region subtag did not survive the key")
	}
	if _, ok := c.Channels[config.ChannelTelephony]; !ok {
		t.Error("telephony did not load")
	}
}

func TestLoadCatalogRejectsWhatCouldNeverResolve(t *testing.T) {
	t.Parallel()
	cases := map[string]string{
		"no defaults layer": `{"tenants": {"t_9c21a4be": {}}, "languages": {"hi": {}}}`,
		"unknown channel":   `{"defaults": {}, "channels": {"carrier_pigeon": {}}}`,
		"unknown section":   `{"defaults": {}, "roles": {"participant": {}}}`,
		"an axis unsplit":   `{"defaults": {}, "languages": {"hi": {"turn": {"strategy": "semantic"}}}}`,
		"not a JSON object": `["defaults"]`,
	}
	for name, raw := range cases {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			if _, err := config.LoadCatalog([]byte(raw)); err == nil {
				t.Error("the catalog loaded")
			}
		})
	}
}
