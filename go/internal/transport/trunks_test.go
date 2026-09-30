package transport_test

import (
	"errors"
	"slices"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/transport"
)

func environment(vars map[string]string) func(string) string {
	return func(name string) string { return vars[name] }
}

const carrierTable = `{
	"carrier-out": {
		"provider": "carrier",
		"address": "sip.carrier.example:5060",
		"transport": "tls",
		"numbers": ["+12025550100"],
		"destinationCountry": "US",
		"authUsernameRef": "secret://operator/carrier/sip-username",
		"authPasswordRef": "secret://operator/carrier/sip-password"
	},
	"open-lab": {"provider": "lab", "address": "10.0.0.5", "numbers": ["+12025550101"]}
}`

func TestATrunkTableResolvesItsCredentialsFromTheEnvironment(t *testing.T) {
	t.Parallel()
	trunks, err := transport.LoadTrunks([]byte(carrierTable), environment(map[string]string{
		"CARRIER_SIP_USERNAME": "user", "CARRIER_SIP_PASSWORD": "pass",
	}))
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	out := trunks["carrier-out"]
	if out.AuthUsername != "user" || out.AuthPassword != "pass" || out.Transport != "tls" || out.Address != "sip.carrier.example:5060" {
		t.Errorf("carrier-out = %+v", out)
	}
	if lab := trunks["open-lab"]; lab.AuthUsername != "" || lab.Transport != "" || !slices.Equal(lab.Numbers, []string{"+12025550101"}) {
		t.Errorf("open-lab = %+v", lab)
	}
	if empty, err := transport.LoadTrunks([]byte(`{}`), environment(nil)); err != nil || len(empty) != 0 {
		t.Errorf("an empty table: %v, %v", empty, err)
	}
}

func TestATrunkTableThatCouldReachTheWrongPlaceIsRefusedWhole(t *testing.T) {
	t.Parallel()
	table := `{
		"Bad_Name": {"provider": "carrier", "address": "sip.carrier.example", "numbers": ["+12025550100"]},
		"uri": {"provider": "carrier", "address": "sip:carrier.example", "numbers": ["+12025550100"]},
		"local": {"provider": "carrier", "address": "sip.carrier.example", "numbers": ["02025550100"]},
		"none": {"provider": "carrier", "address": "sip.carrier.example", "numbers": []},
		"sctp": {"provider": "carrier", "address": "sip.carrier.example", "transport": "sctp", "numbers": ["+12025550100"]},
		"half": {"provider": "carrier", "address": "sip.carrier.example", "numbers": ["+12025550100"],
			"authUsernameRef": "secret://operator/carrier/sip-username"},
		"value": {"provider": "carrier", "address": "sip.carrier.example", "numbers": ["+12025550100"],
			"authUsernameRef": "hunter2", "authPasswordRef": "secret://operator/carrier/sip-password"},
		"borrowed": {"provider": "carrier", "address": "sip.carrier.example", "numbers": ["+12025550100"],
			"authUsernameRef": "secret://operator/dafter/sip-username", "authPasswordRef": "secret://operator/carrier/api-key"},
		"missing": {"provider": "carrier", "address": "sip.carrier.example", "numbers": ["+12025550100"],
			"authUsernameRef": "secret://operator/carrier/sip-user", "authPasswordRef": "secret://operator/carrier/sip-pass"}
	}`
	_, err := transport.LoadTrunks([]byte(table), environment(map[string]string{
		"CARRIER_SIP_PASSWORD": "pass", "CARRIER_API_KEY": "key", "DAFTER_SIP_USERNAME": "worker",
	}))
	var de *errs.Error
	if !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
		t.Fatalf("want %s, got %v", errs.CodeInvalidConfig, err)
	}
	joined := strings.Join(de.Details, "\n")
	for _, pointer := range []string{
		"/Bad_Name'", "/uri/address", "/local/numbers", "/none/numbers", "/sctp/transport",
		"/half/authPasswordRef", "/value/authUsernameRef", "/borrowed/authUsernameRef",
		"/borrowed/authPasswordRef", "/missing/authUsernameRef", "/missing/authPasswordRef",
	} {
		if !strings.Contains(joined, pointer) {
			t.Errorf("no detail points at %s\n%s", pointer, joined)
		}
	}
	for _, secret := range []string{"pass", "key", "worker", "hunter2"} {
		if strings.Contains(joined, ": "+secret) || strings.Contains(joined, secret+" ") {
			t.Errorf("a refusal repeats a secret value %q\n%s", secret, joined)
		}
	}
}

func TestATrunkTableRejectsAnUnknownField(t *testing.T) {
	t.Parallel()
	_, err := transport.LoadTrunks([]byte(`{"x": {"provider": "carrier", "address": "h.example", "numbers": ["+12025550100"], "authPassword": "plain"}}`), environment(nil))
	var de *errs.Error
	if !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
		t.Errorf("a plain password field was accepted: %v", err)
	}
}
