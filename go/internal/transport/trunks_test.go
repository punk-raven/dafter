package transport_test

import (
	"errors"
	"os"
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
	trunks, skipped, err := transport.LoadTrunks([]byte(carrierTable), environment(map[string]string{
		"CARRIER_SIP_USERNAME": "user", "CARRIER_SIP_PASSWORD": "pass",
	}))
	if err != nil || len(skipped) != 0 {
		t.Fatalf("load: %v, skipped %v", err, skipped)
	}
	out := trunks["carrier-out"]
	if out.AuthUsername != "user" || out.AuthPassword != "pass" || out.Transport != "tls" || out.Address != "sip.carrier.example:5060" || out.Inbound != nil {
		t.Errorf("carrier-out = %+v", out)
	}
	if lab := trunks["open-lab"]; lab.AuthUsername != "" || lab.Transport != "" || !slices.Equal(lab.Numbers, []string{"+12025550101"}) {
		t.Errorf("open-lab = %+v", lab)
	}
	if empty, skipped, err := transport.LoadTrunks([]byte(`{}`), environment(nil)); err != nil || len(empty) != 0 || len(skipped) != 0 {
		t.Errorf("an empty table: %v, %v, %v", empty, skipped, err)
	}
}

func TestATrunkWhoseVariablesAreAllUnsetIsLeftOut(t *testing.T) {
	t.Parallel()
	trunks, skipped, err := transport.LoadTrunks([]byte(carrierTable), environment(nil))
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if _, ok := trunks["carrier-out"]; ok || !slices.Equal(skipped, []string{"carrier-out"}) {
		t.Errorf("trunks %v skipped %v; a trunk this environment does not configure is left out, and said so", trunks, skipped)
	}
	if _, ok := trunks["open-lab"]; !ok {
		t.Error("a trunk that reads no variable was left out")
	}
}

func TestATrunkTableThatCouldReachTheWrongPlaceIsRefusedWhole(t *testing.T) {
	t.Parallel()
	table := `{
		"Bad_Name": {"provider": "carrier", "address": "sip.carrier.example", "numbers": ["+12025550100"]},
		"uri": {"provider": "carrier", "address": "sip:carrier.example", "numbers": ["+12025550100"]},
		"local": {"provider": "carrier", "address": "sip.carrier.example", "numbers": ["02025550100"]},
		"none": {"provider": "carrier", "address": "sip.carrier.example", "numbers": []},
		"both": {"provider": "carrier", "address": "sip.carrier.example", "addressRef": "secret://operator/carrier/sip-domain", "numbers": ["+12025550100"]},
		"sctp": {"provider": "carrier", "address": "sip.carrier.example", "transport": "sctp", "numbers": ["+12025550100"]},
		"half": {"provider": "carrier", "address": "sip.carrier.example", "numbers": ["+12025550100"],
			"authUsernameRef": "secret://operator/carrier/sip-username"},
		"value": {"provider": "carrier", "address": "sip.carrier.example", "numbers": ["+12025550100"],
			"authUsernameRef": "hunter2", "authPasswordRef": "secret://operator/carrier/sip-password"},
		"borrowed": {"provider": "carrier", "address": "sip.carrier.example", "numbers": ["+12025550100"],
			"authUsernameRef": "secret://operator/dafter/sip-username", "authPasswordRef": "secret://operator/carrier/api-key"},
		"partial": {"provider": "carrier", "address": "sip.carrier.example", "numbers": ["+12025550100"],
			"authUsernameRef": "secret://operator/carrier/sip-user", "authPasswordRef": "secret://operator/carrier/sip-password"},
		"referred": {"provider": "carrier", "addressRef": "secret://operator/carrier/sip-uri", "numbersRef": "secret://operator/carrier/sip-local"}
	}`
	_, _, err := transport.LoadTrunks([]byte(table), environment(map[string]string{
		"CARRIER_SIP_PASSWORD": "pass", "CARRIER_API_KEY": "key", "DAFTER_SIP_USERNAME": "worker",
		"CARRIER_SIP_URI": "sip:carrier.example", "CARRIER_SIP_LOCAL": "+12025550100, 02025550100",
	}))
	var de *errs.Error
	if !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
		t.Fatalf("want %s, got %v", errs.CodeInvalidConfig, err)
	}
	joined := strings.Join(de.Details, "\n")
	for _, pointer := range []string{
		"/Bad_Name'", "/uri/address", "/local/numbers", "/none/numbers", "/both/address", "/sctp/transport",
		"/half/authPasswordRef", "/value/authUsernameRef", "/borrowed/authUsernameRef",
		"/borrowed/authPasswordRef", "/partial/authUsernameRef", "/referred/address", "/referred/numbers",
	} {
		if !strings.Contains(joined, pointer) {
			t.Errorf("no detail points at %s\n%s", pointer, joined)
		}
	}
	for _, secret := range []string{"pass", "key", "worker", "hunter2", "sip:carrier"} {
		if strings.Contains(joined, ": "+secret) || strings.Contains(joined, secret+" ") {
			t.Errorf("a refusal repeats a value %q\n%s", secret, joined)
		}
	}
}

func TestATrunkTableRejectsAnUnknownField(t *testing.T) {
	t.Parallel()
	_, _, err := transport.LoadTrunks([]byte(`{"x": {"provider": "carrier", "address": "h.example", "numbers": ["+12025550100"], "authPassword": "plain"}}`), environment(nil))
	var de *errs.Error
	if !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
		t.Errorf("a plain password field was accepted: %v", err)
	}
}

var vobizEnv = map[string]string{
	"VOBIZ_SIP_DOMAIN":      "aabbccdd.sip.vobiz.ai",
	"VOBIZ_SIP_NUMBERS":     "+12025550100, +12025550101",
	"VOBIZ_SIP_USERNAME":    "dafter",
	"VOBIZ_SIP_PASSWORD":    "not-a-real-password",
	"VOBIZ_SIP_WEBHOOK_URL": "https://calls.example.com/",
	"VOBIZ_SIP_AUTH_TOKEN":  "not-a-real-token",
	"VOBIZ_SIP_BRIDGE_APP":  "12345678901234567",
}

func shippedTable(t *testing.T) []byte {
	t.Helper()
	raw, err := os.ReadFile("../../cmd/dafter-control/trunks.json")
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func TestTheShippedVobizTrunkReadsEverythingPerAccountFromTheEnvironment(t *testing.T) {
	t.Parallel()
	trunks, skipped, err := transport.LoadTrunks(shippedTable(t), environment(vobizEnv))
	if err != nil || len(skipped) != 0 {
		t.Fatalf("load: %v, skipped %v", err, skipped)
	}
	v := trunks["vobiz"]
	if v.Address != "aabbccdd.sip.vobiz.ai" || !slices.Equal(v.Numbers, []string{"+12025550100", "+12025550101"}) ||
		v.AuthUsername != "dafter" || v.AuthPassword != "not-a-real-password" || v.Transport != "tcp" {
		t.Errorf("vobiz = %+v", v)
	}
	in := v.Inbound
	if in == nil || in.PublicURL != "https://calls.example.com" || in.SigningKey != "not-a-real-token" ||
		in.BridgeHost != "sip.vobiz.ai" || in.BridgeUser != "12345678901234567" || in.Session.Profile != "" {
		t.Errorf("vobiz inbound = %+v", in)
	}
}

func TestTheShippedTableBootsWithNoCarrierConfigured(t *testing.T) {
	t.Parallel()
	trunks, skipped, err := transport.LoadTrunks(shippedTable(t), environment(nil))
	if err != nil || len(trunks) != 0 || !slices.Equal(skipped, []string{"vobiz"}) {
		t.Errorf("trunks %v skipped %v: %v", trunks, skipped, err)
	}
	outbound := map[string]string{}
	for k, v := range vobizEnv {
		if !strings.Contains(k, "WEBHOOK") && !strings.Contains(k, "AUTH_TOKEN") && !strings.Contains(k, "BRIDGE") {
			outbound[k] = v
		}
	}
	trunks, _, err = transport.LoadTrunks(shippedTable(t), environment(outbound))
	if err != nil || trunks["vobiz"].Inbound != nil {
		t.Errorf("an outbound-only Vobiz account: %+v, %v", trunks["vobiz"], err)
	}
	outbound["VOBIZ_SIP_WEBHOOK_URL"] = "http://calls.example.com/?key=x"
	var de *errs.Error
	if _, _, err := transport.LoadTrunks(shippedTable(t), environment(outbound)); !errors.As(err, &de) ||
		!strings.Contains(strings.Join(de.Details, "\n"), "/vobiz/inbound/publicUrlRef") ||
		!strings.Contains(strings.Join(de.Details, "\n"), "/vobiz/inbound/signingKeyRef") {
		t.Errorf("a half-configured inbound trunk was accepted: %v", err)
	}
}
