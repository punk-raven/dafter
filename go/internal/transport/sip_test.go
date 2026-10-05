package transport_test

import (
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/transport"
)

const (
	callee        = "+12025550143"
	phoneIdentity = "p_4b81e0d7"
)

var carrier = transport.Trunk{
	Provider: "carrier", Address: "sip.carrier.example", Transport: "udp",
	Numbers: []string{"+12025550100", "+12025550101"}, DestinationCountry: "US",
	AuthUsername: "dafter-user", AuthPassword: "dafter-password",
}

func phoneCall() transport.PhoneCall {
	return transport.PhoneCall{
		Room: sessionID, Identity: phoneIdentity, To: callee, Trunk: carrier,
		RingingTimeout: 30 * time.Second, MaxCallDuration: 30 * time.Minute,
	}
}

const placedReply = `{"participant_id":"PA_abc123","participant_identity":"p_4b81e0d7","room_name":"s_7f3a9c21","sip_call_id":"SCL_abc123"}`

func TestACallSendsThePinnedRequestUnderASIPCallOnlyToken(t *testing.T) {
	t.Parallel()
	srv, calls := egressServer(t, placedReply, http.StatusOK)
	info, err := recorder(t, srv).PlaceCall(t.Context(), phoneCall())
	if err != nil {
		t.Fatalf("place call: %v", err)
	}
	if info != (transport.CallInfo{ParticipantID: "PA_abc123", Identity: phoneIdentity, Room: sessionID, CallID: "SCL_abc123"}) {
		t.Errorf("info = %+v", info)
	}
	if len(*calls) != 1 || (*calls)[0].method != "SIP/CreateSIPParticipant" {
		t.Fatalf("calls were %+v", *calls)
	}
	raw, err := os.ReadFile(filepath.Join("testdata", "sip", "create-sip-participant.json"))
	if err != nil {
		t.Fatal(err)
	}
	if got, want := compact(t, (*calls)[0].body), compact(t, raw); got != want {
		t.Errorf("request differs from the pinned fixture\n got: %s\nwant: %s", got, want)
	}
	_, payload := verified(t, (*calls)[0].token)
	sip, _ := payload["sip"].(map[string]any)
	if sip["call"] != true || sip["admin"] != false || len(sip) != 2 {
		t.Errorf("sip grant = %v, want call stated true and admin stated false", sip)
	}
	if v, _ := payload["video"].(map[string]any); len(v) != 0 {
		t.Errorf("video grant = %v, want none: placing a call needs no room permission", v)
	}
	if exp, iat := payload["exp"].(float64), payload["iat"].(float64); exp-iat > 60 {
		t.Errorf("the SIP token lives %.0fs; it is minted per call", exp-iat)
	}
}

func TestACallIsRefusedBeforeItReachesTheServer(t *testing.T) {
	t.Parallel()
	srv, calls := egressServer(t, placedReply, http.StatusOK)
	lk := recorder(t, srv)
	broken := map[string]func(*transport.PhoneCall){
		"no room":           func(c *transport.PhoneCall) { c.Room = "" },
		"no identity":       func(c *transport.PhoneCall) { c.Identity = "" },
		"a local number":    func(c *transport.PhoneCall) { c.To = "02025550143" },
		"a SIP URI":         func(c *transport.PhoneCall) { c.To = "sip:+12025550143@carrier.example" },
		"no trunk address":  func(c *transport.PhoneCall) { c.Trunk.Address = "" },
		"no number to call": func(c *transport.PhoneCall) { c.Trunk.Numbers = nil },
		"rings forever":     func(c *transport.PhoneCall) { c.RingingTimeout = 0 },
		"lasts without end": func(c *transport.PhoneCall) { c.MaxCallDuration = 0 },
	}
	for name, change := range broken {
		c := phoneCall()
		change(&c)
		var de *errs.Error
		_, err := lk.PlaceCall(t.Context(), c)
		if !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
			t.Errorf("%s was not refused: %v", name, err)
			continue
		}
		if strings.Contains(de.Error(), "2025550143") {
			t.Errorf("%s: the refusal repeats the number: %v", name, de)
		}
	}
	if len(*calls) != 0 {
		t.Errorf("%d refused calls reached the server", len(*calls))
	}
}

func TestARefusedCallCarriesNothingTheServerSaidAboutIt(t *testing.T) {
	t.Parallel()
	srv, _ := egressServer(t, `{"code":"invalid_argument","msg":"cannot dial +12025550143"}`, http.StatusBadRequest)
	_, err := recorder(t, srv).PlaceCall(t.Context(), phoneCall())
	var de *errs.Error
	if !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
		t.Fatalf("want %s, got %v", errs.CodeInvalidConfig, err)
	}
	if strings.Contains(de.Error(), "2025550143") || len(de.Details) != 0 {
		t.Errorf("the error is logged and returned, and it carries the server's words about the number: %v", de)
	}
}

func TestACallAnsweredForSomeoneElseIsNotTakenForOurs(t *testing.T) {
	t.Parallel()
	srv, _ := egressServer(t, `{"participant_id":"PA_x","participant_identity":"sip_+12025550143","room_name":"s_7f3a9c21"}`, http.StatusOK)
	var de *errs.Error
	if _, err := recorder(t, srv).PlaceCall(t.Context(), phoneCall()); !errors.As(err, &de) || de.Code != errs.CodeInternal {
		t.Errorf("want %s, got %v", errs.CodeInternal, err)
	}
}

func TestAHeldCallerIsReachedThroughTheCarriersSIPEndpointWithItsBridgeToken(t *testing.T) {
	t.Parallel()
	srv, calls := egressServer(t, placedReply, http.StatusOK)
	bridge := transport.PhoneCall{
		Room: sessionID, Identity: phoneIdentity, SIPUser: "12345678901234567",
		Headers: map[string]string{"X-VH-Bridge": "0f1e2d3c4b5a69788796a5b4c3d2e1f0"},
		Trunk: transport.Trunk{
			Provider: "vobiz", Address: "registrar.vobiz.ai:5060", Transport: "tcp", Numbers: []string{"+12025550100"},
			AuthUsername: "dafter_bridge", AuthPassword: "not-a-real-endpoint-password",
		},
		RingingTimeout: 30 * time.Second, MaxCallDuration: 30 * time.Minute,
	}
	if _, err := recorder(t, srv).PlaceCall(t.Context(), bridge); err != nil {
		t.Fatalf("place call: %v", err)
	}
	raw, err := os.ReadFile(filepath.Join("testdata", "sip", "create-sip-participant-bridge.json"))
	if err != nil {
		t.Fatal(err)
	}
	if got, want := compact(t, (*calls)[0].body), compact(t, raw); got != want {
		t.Errorf("request differs from the pinned fixture\n got: %s\nwant: %s", got, want)
	}
}

func TestACallNamesExactlyOneDestinationAndOnlyXHeaders(t *testing.T) {
	t.Parallel()
	srv, calls := egressServer(t, placedReply, http.StatusOK)
	lk := recorder(t, srv)
	for name, change := range map[string]func(*transport.PhoneCall){
		"a number and a user": func(c *transport.PhoneCall) { c.SIPUser = "12345678901234567" },
		"neither":             func(c *transport.PhoneCall) { c.To = "" },
		"a user with a host":  func(c *transport.PhoneCall) { c.To, c.SIPUser = "", "app@evil.example" },
		"a routing header":    func(c *transport.PhoneCall) { c.Headers = map[string]string{"Route": "sip:evil.example"} },
	} {
		c := phoneCall()
		change(&c)
		var de *errs.Error
		if _, err := lk.PlaceCall(t.Context(), c); !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
			t.Errorf("%s was not refused: %v", name, err)
		}
	}
	if len(*calls) != 0 {
		t.Errorf("%d refused calls reached the server", len(*calls))
	}
}
