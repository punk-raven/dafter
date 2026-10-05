package control_test

import (
	"errors"
	"net/http"
	"net/url"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/carrier/vobiz"
	"github.com/punk-raven/dafter/go/internal/ids"
	"github.com/punk-raven/dafter/go/internal/state"
)

const (
	pinPath     = "/telephony/carrier-out/pin"
	firstCall   = "550e8400-e29b-41d4-a716-446655440000"
	secondCall  = "11111111-2222-3333-4444-555555555555"
	asksForPIN  = "<Gather action=\"" + publicURL + pinPath + "\""
	asksAgain   = "That PIN did not open a meeting"
	hangsUp     = "<Hangup>"
	strangerTel = "+12025550177"
)

func toMeeting(change func(url.Values)) func(url.Values) {
	return func(f url.Values) {
		f.Set("To", strings.TrimPrefix(meetingNumber, "+"))
		if change != nil {
			change(f)
		}
	}
}

func (h *harness) ringMeeting(t *testing.T, change func(url.Values)) (int, string) {
	t.Helper()
	return h.ring(t, toMeeting(change))
}

func (h *harness) keyIn(t *testing.T, pin string, change func(url.Values)) (int, string) {
	t.Helper()
	form := carrierForm(t, "pin.form", toMeeting(func(f url.Values) {
		f.Set("Digits", pin)
		if change != nil {
			change(f)
		}
	}))
	return h.webhook(t, pinPath, form, signingKey, nonce())
}

func admittedTo(t *testing.T, h *harness, xml, room, callUUID string) {
	t.Helper()
	m := roomOfHold.FindStringSubmatch(xml)
	if m == nil || !strings.Contains(xml, "Joining the meeting") || !strings.Contains(xml, `callbackUrl="`+publicURL+`/telephony/carrier-out/held/`+m[1]+`"`) {
		t.Fatalf("not admitted: %s", xml)
	}
	before := len(h.transport.calls)
	if status, _ := h.webhook(t, "/telephony/carrier-out/held/"+m[1], carrierForm(t, "conference-enter.form", func(f url.Values) { f.Set("CallUUID", callUUID) }), "", ""); status != http.StatusOK {
		t.Fatalf("held callback: %d", status)
	}
	if len(h.transport.calls) != before+1 {
		t.Fatal("the admitted caller was not dialed back")
	}
	c := h.transport.calls[before]
	if c.Room != room || c.To != "" || c.Headers[vobiz.BridgeHeader] != m[1] || c.Trunk.Address != bridgeHost ||
		c.Trunk.AuthUsername != endpoint || c.Trunk.AuthPassword != endpointKey || ids.ValidateID(ids.PrefixParticipant, c.Identity) != nil {
		t.Errorf("dial-back %+v, want the meeting's own room %s", c, room)
	}
}

func TestAMeetingCallerKeysInThePINAndJoinsTheRunningSession(t *testing.T) {
	t.Parallel()
	h, logs := serveMeetings(t)
	meeting := h.openMeeting(t, "")
	dispatched := len(h.transport.dispatched)

	status, ask := h.ringMeeting(t, nil)
	if status != http.StatusOK || !strings.Contains(ask, asksForPIN) || !strings.Contains(ask, `numDigits="8"`) {
		t.Fatalf("answer %d: %s", status, ask)
	}
	_, admit := h.keyIn(t, meeting.DialIn.PIN, nil)
	admittedTo(t, h, admit, meeting.Room, firstCall)
	if _, retried := h.keyIn(t, meeting.DialIn.PIN, nil); retried != admit {
		t.Errorf("a retried digits callback held the caller twice: %s", retried)
	}
	if len(h.transport.dispatched) != dispatched {
		t.Error("a dial-in caller opened a session or dispatched an agent")
	}
	token := roomOfHold.FindStringSubmatch(admit)[1]
	bridge := carrierForm(t, "bridge.form", func(f url.Values) { f.Set(vobiz.BridgeHeader, token) })
	if status, join := h.webhook(t, bridgePath, bridge, signingKey, nonce()); status != http.StatusOK || !strings.Contains(join, ">"+token+"</Conference>") {
		t.Errorf("bridge %d: %s", status, join)
	}
	for _, secret := range []string{meeting.DialIn.PIN, callerTail} {
		if strings.Contains(logs.String(), secret) {
			t.Errorf("the logs carry %q", secret)
		}
	}
}

func TestAWrongPINIsRetriedUntilTheAttemptsRunOut(t *testing.T) {
	t.Parallel()
	h, logs := serveMeetings(t)
	meeting := h.openMeeting(t, "")
	h.ringMeeting(t, nil)
	for i, entered := range []string{"00000000", ""} {
		if status, again := h.keyIn(t, entered, nil); status != http.StatusOK || !strings.Contains(again, asksAgain) || !strings.Contains(again, "<Gather") {
			t.Fatalf("attempt %d: %d %s", i+1, status, again)
		}
	}
	if _, last := h.keyIn(t, meeting.DialIn.PIN[:7], nil); !strings.Contains(last, "could not join you") || !strings.Contains(last, hangsUp) || strings.Contains(last, "<Gather") {
		t.Errorf("the third wrong attempt did not end the call: %s", last)
	}
	if strings.Contains(logs.String(), "00000000") || strings.Contains(logs.String(), meeting.DialIn.PIN[:7]) {
		t.Error("a keyed PIN reached the logs")
	}
	if len(h.transport.calls) != 0 {
		t.Error("a caller with no right PIN was dialed into a room")
	}
}

func TestAPINOpensNothingOnceTheSessionHasEnded(t *testing.T) {
	t.Parallel()
	h, _ := serveMeetings(t)
	meeting := h.openMeeting(t, "")
	if err := h.svc.SweepDialIns(t.Context()); err != nil {
		t.Fatal(err)
	}
	h.transport.closeRoom(meeting.Room)
	h.ringMeeting(t, nil)
	if _, again := h.keyIn(t, meeting.DialIn.PIN, nil); !strings.Contains(again, asksAgain) {
		t.Errorf("an ended meeting's PIN was taken: %s", again)
	}
	if _, err := h.store.DialInPIN(t.Context(), meeting.SessionID); !errors.Is(err, state.ErrNotFound) {
		t.Errorf("the ended meeting kept its PIN: %v", err)
	}
	h.transport.roomErr = errUnavailable
	waiting := h.openMeeting(t, "")
	if _, refused := h.keyIn(t, waiting.DialIn.PIN, func(f url.Values) { f.Set("CallUUID", secondCall) }); !strings.Contains(refused, hangsUp) {
		t.Errorf("a media server that cannot say the room is open let the caller in: %s", refused)
	}
}

func TestAPINAndNumberCheckAdmitsOnlyAnAllowedCaller(t *testing.T) {
	t.Parallel()
	h, _ := serveMeetings(t)
	meeting := h.openMeeting(t, "pin_and_number")
	if status, raw := h.allow(t, meeting.SessionID, `{"numbers":["`+allowedCaller+`"]}`); status != http.StatusOK {
		t.Fatalf("allow: %d %s", status, raw)
	}
	if _, ask := h.ringMeeting(t, nil); !strings.Contains(ask, asksForPIN) {
		t.Fatalf("a PIN and number meeting did not ask the allowed caller for the PIN: %s", ask)
	}
	for name, caller := range map[string]string{"another number": strangerTel, "a withheld number": "anonymous", "no number": ""} {
		if _, again := h.keyIn(t, meeting.DialIn.PIN, func(f url.Values) { f.Set("CallUUID", secondCall); f.Set("From", caller) }); !strings.Contains(again, asksAgain) && !strings.Contains(again, "could not join you") {
			t.Errorf("%s with the right PIN was admitted: %s", name, again)
		}
	}
	_, admit := h.keyIn(t, meeting.DialIn.PIN, nil)
	admittedTo(t, h, admit, meeting.Room, firstCall)
}

func TestANumberCheckAdmitsAnAllowedCallerWithoutAPIN(t *testing.T) {
	t.Parallel()
	h, _ := serveMeetings(t)
	meeting := h.openMeeting(t, "number")
	h.allow(t, meeting.SessionID, `{"numbers":["`+allowedCaller+`"]}`)
	_, admit := h.ringMeeting(t, nil)
	admittedTo(t, h, admit, meeting.Room, firstCall)

	other := func(f url.Values) { f.Set("CallUUID", secondCall); f.Set("From", strangerTel) }
	if _, ask := h.ringMeeting(t, other); !strings.Contains(ask, asksForPIN) {
		t.Errorf("a number not on the list was not asked for a PIN: %s", ask)
	}
	if _, again := h.keyIn(t, meeting.DialIn.PIN, other); !strings.Contains(again, asksAgain) {
		t.Errorf("the PIN admitted a number the meeting does not allow: %s", again)
	}
	withheld := func(f url.Values) { f.Set("CallUUID", "22222222-3333-4444-5555-666666666666"); f.Del("From") }
	if _, ask := h.ringMeeting(t, withheld); !strings.Contains(ask, asksForPIN) {
		t.Errorf("a withheld number was admitted by number: %s", ask)
	}
}

func TestANumberAllowedInTwoRunningMeetingsIsAskedWhichNeverGuessed(t *testing.T) {
	t.Parallel()
	h, _ := serveMeetings(t)
	first, second := h.openMeeting(t, "number"), h.openMeeting(t, "number")
	for _, m := range []dialInResponse{first, second} {
		h.allow(t, m.SessionID, `{"numbers":["`+allowedCaller+`"]}`)
	}
	if _, ask := h.ringMeeting(t, nil); !strings.Contains(ask, asksForPIN) || len(h.transport.calls) != 0 {
		t.Fatalf("an ambiguous number was put in a meeting: %s", ask)
	}
	_, admit := h.keyIn(t, second.DialIn.PIN, nil)
	admittedTo(t, h, admit, second.Room, firstCall)

	h.transport.closeRoom(first.Room)
	_, direct := h.ringMeeting(t, func(f url.Values) { f.Set("CallUUID", secondCall) })
	admittedTo(t, h, direct, second.Room, secondCall)
}

func TestAnUnsignedOrForeignDigitsCallbackIsRefused(t *testing.T) {
	t.Parallel()
	h, _ := serveMeetings(t)
	meeting := h.openMeeting(t, "")
	body := carrierForm(t, "pin.form", toMeeting(func(f url.Values) { f.Set("Digits", meeting.DialIn.PIN) }))
	for name, c := range map[string]struct{ key, nonce string }{
		"unsigned":         {"", ""},
		"signed by others": {wrongSigner, nonce()},
	} {
		if status, _ := h.webhook(t, pinPath, body, c.key, c.nonce); status != http.StatusForbidden {
			t.Errorf("%s digits were answered: %d", name, status)
		}
	}
	agentNumber := carrierForm(t, "pin.form", func(f url.Values) { f.Set("Digits", meeting.DialIn.PIN) })
	if status, _ := h.webhook(t, pinPath, agentNumber, signingKey, nonce()); status != http.StatusBadRequest {
		t.Errorf("digits for the agent's number were taken: %d", status)
	}
	if status, _ := h.webhook(t, "/telephony/outbound-only/pin", body, signingKey, nonce()); status != http.StatusNotFound {
		t.Errorf("a trunk with no inbound took digits: %d", status)
	}
	if len(h.transport.calls) != 0 {
		t.Error("a refused callback dialed someone in")
	}
}

func TestTheAgentsNumberStillOpensItsOwnSession(t *testing.T) {
	t.Parallel()
	h, _ := serveMeetings(t)
	if _, hold := h.ring(t, nil); roomOfHold.FindStringSubmatch(hold) == nil || strings.Contains(hold, "<Gather") || len(h.transport.dispatched) != 1 {
		t.Errorf("the agent's number did not open a session: %s", hold)
	}
}
