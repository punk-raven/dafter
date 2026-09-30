package control_test

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/punk-raven/dafter/go/internal/carrier/vobiz"
	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/ids"
	"github.com/punk-raven/dafter/go/internal/transport"
)

const (
	publicURL   = "https://calls.example.com"
	signingKey  = "not-a-real-token"
	bridgeApp   = "12345678901234567"
	callerTail  = "2025550143"
	bridgeUUID  = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
	vobizForms  = "../carrier/vobiz/testdata/"
	answerPath  = "/telephony/carrier-out/answer"
	bridgePath  = "/telephony/carrier-out/bridge"
	wrongSigner = "someone-elses-token"
)

var (
	errUnavailable = errs.Errorf(errs.CodeProviderUnavailable, "media server unreachable for CreateSIPParticipant")
	nonces         atomic.Int64
	roomOfHold     = regexp.MustCompile(`>([0-9a-f]{32})</Conference>`)
)

func serveInbound(t *testing.T) (*harness, *lockedBuffer) {
	t.Helper()
	h, logs := servePhone(t)
	trunk := carrierTrunk
	trunk.Inbound = &transport.Inbound{
		PublicURL: publicURL, SigningKey: signingKey, BridgeHost: "app.vobiz.ai", BridgeUser: bridgeApp,
		Session: transport.InboundSession{TenantID: tenantID, Language: "hi", Profile: "phone"},
	}
	h.svc.Trunks = transport.Trunks{"carrier-out": trunk, "outbound-only": carrierTrunk}
	return h, logs
}

func carrierForm(t *testing.T, name string, change func(url.Values)) string {
	t.Helper()
	raw, err := os.ReadFile(vobizForms + name)
	if err != nil {
		t.Fatal(err)
	}
	form, err := url.ParseQuery(strings.TrimSpace(string(raw)))
	if err != nil {
		t.Fatal(err)
	}
	if change != nil {
		change(form)
	}
	return form.Encode()
}

func (h *harness) webhook(t *testing.T, path, body, key, nonce string) (int, string) {
	t.Helper()
	req, err := http.NewRequestWithContext(t.Context(), http.MethodPost, h.server.URL+path, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	if key != "" {
		mac := hmac.New(sha256.New, []byte(key))
		mac.Write([]byte(publicURL + path + "." + nonce))
		req.Header.Set(vobiz.SignatureHeader, base64.StdEncoding.EncodeToString(mac.Sum(nil)))
		req.Header.Set(vobiz.NonceHeader, nonce)
	}
	resp, err := h.server.Client().Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer closeBody(t, resp)
	raw, err := readAll(resp)
	if err != nil {
		t.Fatal(err)
	}
	return resp.StatusCode, string(raw)
}

func nonce() string {
	return strconv.FormatInt(1000000000000000000+nonces.Add(1), 10)
}

func (h *harness) ring(t *testing.T, change func(url.Values)) (int, string) {
	t.Helper()
	return h.webhook(t, answerPath, carrierForm(t, "answer.form", change), signingKey, nonce())
}

func TestAnInboundCallIsStoredHeldDialedBackAndBridged(t *testing.T) {
	t.Parallel()
	h, logs := serveInbound(t)

	status, hold := h.ring(t, nil)
	m := roomOfHold.FindStringSubmatch(hold)
	if status != http.StatusOK || m == nil || !strings.Contains(hold, `callbackUrl="`+publicURL+`/telephony/carrier-out/held/`+m[1]+`"`) {
		t.Fatalf("answer %d: %s", status, hold)
	}
	token := m[1]
	if len(h.transport.dispatched) != 1 || len(h.transport.calls) != 0 {
		t.Fatalf("dispatched %d, calls %d: the session is stored and the agent dispatched before anyone dials", len(h.transport.dispatched), len(h.transport.calls))
	}
	room := h.transport.dispatched[0].Room
	stored, err := h.store.Session(t.Context(), room)
	if err != nil {
		t.Fatalf("the held caller's session was not stored: %v", err)
	}
	cfg, err := config.Parse(stored.Config)
	if err != nil || cfg.Channel != config.ChannelTelephony || cfg.TrunkName() != "carrier-out" {
		t.Fatalf("stored config: %v, channel %s trunk %q", err, cfg.Channel, cfg.TrunkName())
	}

	status, retried := h.ring(t, nil)
	if status != http.StatusOK || retried != hold || len(h.transport.dispatched) != 1 {
		t.Errorf("a retried answer made another session: %d %s", status, retried)
	}

	held := "/telephony/carrier-out/held/" + token
	for range 2 {
		if status, _ := h.webhook(t, held, carrierForm(t, "conference-enter.form", nil), "", ""); status != http.StatusOK {
			t.Fatalf("held callback: %d", status)
		}
	}
	if len(h.transport.calls) != 1 {
		t.Fatalf("calls %+v; the caller is dialed back once", h.transport.calls)
	}
	c := h.transport.calls[0]
	if c.Room != room || c.SIPUser != bridgeApp || c.To != "" || c.Headers[vobiz.BridgeHeader] != token ||
		c.Trunk.Address != "app.vobiz.ai" || c.Trunk.AuthPassword != "" || ids.ValidateID(ids.PrefixParticipant, c.Identity) != nil {
		t.Errorf("dial-back %+v", c)
	}

	bridge := func() (int, string) {
		return h.webhook(t, bridgePath, carrierForm(t, "bridge.form", func(f url.Values) { f.Set(vobiz.BridgeHeader, token) }), signingKey, nonce())
	}
	if status, join := bridge(); status != http.StatusOK || !strings.Contains(join, ">"+token+"</Conference>") || strings.Contains(join, "callbackUrl") {
		t.Errorf("bridge %d: %s", status, join)
	}
	if status, again := bridge(); status != http.StatusOK || !strings.Contains(again, "<Hangup>") {
		t.Errorf("a second bridge on one token was admitted: %d %s", status, again)
	}

	for where, text := range map[string]string{"answer": hold, "stored config": string(stored.Config), "logs": logs.String()} {
		if strings.Contains(text, callerTail) {
			t.Errorf("the caller's number reached the %s", where)
		}
	}
}

func TestAnUnsignedOrReplayedAnswerStoresNothing(t *testing.T) {
	t.Parallel()
	h, _ := serveInbound(t)
	body := carrierForm(t, "answer.form", nil)
	used := nonce()
	for name, c := range map[string]struct{ path, key, nonce string }{
		"unsigned":         {answerPath, "", ""},
		"signed by others": {answerPath, wrongSigner, nonce()},
		"unknown trunk":    {"/telephony/nobody/answer", signingKey, nonce()},
		"no inbound":       {"/telephony/outbound-only/answer", signingKey, nonce()},
	} {
		if status, _ := h.webhook(t, c.path, body, c.key, c.nonce); status != http.StatusForbidden && status != http.StatusNotFound {
			t.Errorf("%s was answered: %d", name, status)
		}
	}
	if len(h.transport.dispatched) != 0 {
		t.Fatalf("a refused webhook dispatched an agent: %+v", h.transport.dispatched)
	}
	if status, _ := h.webhook(t, answerPath, body, signingKey, used); status != http.StatusOK {
		t.Fatalf("a signed answer was refused: %d", status)
	}
	other := carrierForm(t, "answer.form", func(f url.Values) { f.Set("CallUUID", "11111111-2222-3333-4444-555555555555") })
	if status, _ := h.webhook(t, answerPath, other, signingKey, used); status != http.StatusForbidden {
		t.Errorf("a captured signature opened a second call: %d", status)
	}
	if len(h.transport.dispatched) != 1 {
		t.Errorf("dispatched %d, want the one signed call", len(h.transport.dispatched))
	}
}

func TestAnAnswerForAnotherNumberOrAnOutboundLegIsRefused(t *testing.T) {
	t.Parallel()
	h, logs := serveInbound(t)
	for name, change := range map[string]func(url.Values){
		"another number": func(f url.Values) { f.Set("To", "12025550199") },
		"outbound":       func(f url.Values) { f.Set("Direction", "outbound") },
		"no call id":     func(f url.Values) { f.Del("CallUUID") },
	} {
		if status, body := h.ring(t, change); status != http.StatusBadRequest || strings.Contains(body, callerTail) {
			t.Errorf("%s: %d %s", name, status, body)
		}
	}
	if len(h.transport.dispatched) != 0 || strings.Contains(logs.String(), callerTail) {
		t.Errorf("dispatched %d; logs carry the number: %v", len(h.transport.dispatched), strings.Contains(logs.String(), callerTail))
	}
}

func TestOnlyTheHeldCallersEntryDialsBack(t *testing.T) {
	t.Parallel()
	h, _ := serveInbound(t)
	_, hold := h.ring(t, nil)
	token := roomOfHold.FindStringSubmatch(hold)[1]
	held := "/telephony/carrier-out/held/"
	for name, c := range map[string]struct{ path, body string }{
		"an unknown token": {held + "00000000000000000000000000000000", carrierForm(t, "conference-enter.form", nil)},
		"a bad token":      {held + "nope", carrierForm(t, "conference-enter.form", nil)},
		"an exit":          {held + token, carrierForm(t, "conference-enter.form", func(f url.Values) { f.Set("ConferenceAction", "exit") })},
		"another leg":      {held + token, carrierForm(t, "conference-enter.form", func(f url.Values) { f.Set("CallUUID", bridgeUUID) })},
	} {
		if h.webhook(t, c.path, c.body, "", ""); len(h.transport.calls) != 0 {
			t.Fatalf("%s dialed the caller back", name)
		}
	}
	unknown := carrierForm(t, "bridge.form", func(f url.Values) { f.Set(vobiz.BridgeHeader, token) })
	if status, body := h.webhook(t, bridgePath, unknown, signingKey, nonce()); status != http.StatusOK || !strings.Contains(body, "<Hangup>") {
		t.Errorf("a bridge before the dial-back was admitted: %d %s", status, body)
	}
}

func TestAFailedDialBackIsRetriedOnTheCarriersNextCallback(t *testing.T) {
	t.Parallel()
	h, _ := serveInbound(t)
	_, hold := h.ring(t, nil)
	held := "/telephony/carrier-out/held/" + roomOfHold.FindStringSubmatch(hold)[1]
	h.transport.callErr = errUnavailable
	if status, _ := h.webhook(t, held, carrierForm(t, "conference-enter.form", nil), "", ""); status != http.StatusServiceUnavailable {
		t.Errorf("a failed dial-back answered %d; the carrier retries only a failed callback", status)
	}
	h.transport.callErr = nil
	if status, _ := h.webhook(t, held, carrierForm(t, "conference-enter.form", nil), "", ""); status != http.StatusOK || len(h.transport.calls) != 1 {
		t.Errorf("the retry did not dial: %d, %d calls", status, len(h.transport.calls))
	}
}

func TestAnInboundSessionMustNameItsOwnTrunk(t *testing.T) {
	t.Parallel()
	h, _ := serveInbound(t)
	trunk := h.svc.Trunks["carrier-out"]
	in := *trunk.Inbound
	in.Session.Profile = "support"
	trunk.Inbound = &in
	h.svc.Trunks["carrier-out"] = trunk
	if status, body := h.ring(t, nil); status != http.StatusOK || !strings.Contains(body, "<Hangup>") {
		t.Errorf("%d %s", status, body)
	}
	if len(h.transport.dispatched) != 0 {
		t.Error("a session that does not name the trunk it arrived on was stored and dispatched")
	}
}
