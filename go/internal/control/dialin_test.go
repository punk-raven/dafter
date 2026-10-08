package control_test

import (
	"encoding/json"
	"errors"
	"net/http"
	"regexp"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/state"
)

const (
	meetingNumber = "+12025550101"
	allowedCaller = "+12025550143"
)

var pinShape = regexp.MustCompile(`^[0-9]{8}$`)

type dialInResponse struct {
	SessionID string `json:"sessionId"`
	Room      string `json:"room"`
	Config    json.RawMessage
	DialIn    *struct {
		Numbers []string `json:"numbers"`
		PIN     string   `json:"pin"`
	} `json:"dialIn"`
}

func serveMeetings(t *testing.T) (*harness, *lockedBuffer) {
	t.Helper()
	h, logs := serveInbound(t)
	trunk := h.svc.Trunks["carrier-out"]
	trunk.Numbers = []string{"+12025550100", meetingNumber}
	in := *trunk.Inbound
	in.MeetingNumbers = []string{meetingNumber}
	trunk.Inbound = &in
	h.svc.Trunks["carrier-out"] = trunk
	return h, logs
}

func dialInRequest(guests, callerCheck string) string {
	telephony := `{"phoneGuests":"` + guests + `"`
	if callerCheck != "" {
		telephony += `,"dialIn":{"callerCheck":"` + callerCheck + `"}`
	}
	return `{"tenantId":"` + tenantID + `","language":"hi","channel":"webrtc","overrides":{"telephony":` + telephony + `}}}`
}

func decodeDialIn(t *testing.T, raw []byte) dialInResponse {
	t.Helper()
	var out dialInResponse
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func (h *harness) openMeeting(t *testing.T, callerCheck string) dialInResponse {
	t.Helper()
	status, raw := h.post(t, dialInRequest("dial_in", callerCheck))
	if status != http.StatusCreated {
		t.Fatalf("create a dial-in meeting: %d %s", status, raw)
	}
	out := decodeDialIn(t, raw)
	if out.DialIn == nil {
		t.Fatalf("the creator was not told how to dial in: %s", raw)
	}
	return out
}

func (h *harness) allow(t *testing.T, sessionID, body string) (int, []byte) {
	t.Helper()
	return h.call(t, http.MethodPut, "/sessions/"+sessionID+"/dial-in/numbers", body)
}

func TestADialInMeetingTellsItsCreatorTheNumberAndAPINTheDocumentNeverHolds(t *testing.T) {
	t.Parallel()
	h, logs := serveMeetings(t)
	first, second := h.openMeeting(t, ""), h.openMeeting(t, "")
	if !pinShape.MatchString(first.DialIn.PIN) || first.DialIn.PIN == second.DialIn.PIN ||
		len(first.DialIn.Numbers) != 1 || first.DialIn.Numbers[0] != meetingNumber {
		t.Fatalf("dial-in %+v and %+v", first.DialIn, second.DialIn)
	}
	stored, err := h.store.Session(t.Context(), first.SessionID)
	if err != nil {
		t.Fatal(err)
	}
	for where, text := range map[string]string{"resolved document": string(first.Config), "stored document": string(stored.Config), "logs": logs.String()} {
		if strings.Contains(text, first.DialIn.PIN) {
			t.Errorf("the PIN reached the %s", where)
		}
	}

	for role, sees := range map[string]bool{"participant": true, "presenter": true, "observer": false} {
		status, raw := h.call(t, http.MethodPost, "/sessions/"+first.SessionID+"/join", `{"role":"`+role+`"}`)
		joined := decodeDialIn(t, raw)
		if status != http.StatusOK || (joined.DialIn != nil) != sees || (sees && joined.DialIn.PIN != first.DialIn.PIN) {
			t.Errorf("a %s joining saw dial-in %+v (%d)", role, joined.DialIn, status)
		}
	}
	if status, raw := h.post(t, dialInRequest("dial_out", "")); status != http.StatusCreated || decodeDialIn(t, raw).DialIn != nil {
		t.Errorf("a meeting that only calls out was given a PIN: %s", raw)
	}
}

func TestADialInMeetingNeedsAMeetingNumberAndAnOpenSession(t *testing.T) {
	t.Parallel()
	h, _ := serveInbound(t)
	if de := h.reject(t, dialInRequest("both", ""), http.StatusBadRequest); !strings.Contains(strings.Join(de.Details, ""), "/telephony/phoneGuests") {
		t.Errorf("a trunk with no meeting number took a dial-in meeting: %+v", de)
	}
	if len(h.transport.dispatched) != 0 {
		t.Error("a refused dial-in meeting was dispatched")
	}
	m, _ := serveMeetings(t)
	trusted := strings.Replace(dialInRequest("dial_in", ""), `"overrides":{`, `"overrides":{"privacyMode":"trusted_agent",`, 1)
	if de := m.reject(t, trusted, http.StatusBadRequest); de.Code != errs.CodePrivacyModeForbids {
		t.Errorf("an end-to-end encrypted meeting took dial-in: %+v", de)
	}
	sealed := strings.Replace(dialInRequest("dial_in", ""), `"overrides":{`, `"overrides":{"privacyMode":"sealed","agent":{"enabled":false},`, 1)
	if de := m.reject(t, sealed, http.StatusBadRequest); de.Code != errs.CodePrivacyModeForbids {
		t.Errorf("a sealed meeting took dial-in: %+v", de)
	}
}

func TestAMeetingThatOnlyTakesDialInPlacesNoCall(t *testing.T) {
	t.Parallel()
	h, _ := serveMeetings(t)
	meeting := h.openMeeting(t, "")
	status, raw := h.dial(t, meeting.SessionID, `{"to":"`+callee+`"}`)
	if status != http.StatusBadRequest || !strings.Contains(string(raw), "/telephony/phoneGuests") || len(h.transport.calls) != 0 {
		t.Errorf("a dial-in-only meeting called a phone out: %d %s", status, raw)
	}
	both := decodeDialIn(t, func() []byte { _, raw := h.post(t, dialInRequest("both", "")); return raw }())
	if status, raw := h.dial(t, both.SessionID, `{"to":"`+callee+`"}`); status != http.StatusCreated {
		t.Errorf("a meeting taking both did not call out: %d %s", status, raw)
	}
}

func TestAllowedNumbersGoThroughTheirOwnRouteAndAreNeverEchoed(t *testing.T) {
	t.Parallel()
	h, logs := serveMeetings(t)
	byPIN := h.openMeeting(t, "")
	if status, raw := h.allow(t, byPIN.SessionID, `{"numbers":["`+allowedCaller+`"]}`); status != http.StatusBadRequest || !strings.Contains(string(raw), "/telephony/dialIn/callerCheck") {
		t.Errorf("a PIN-only meeting kept numbers: %d %s", status, raw)
	}
	byNumber := h.openMeeting(t, "pin_and_number")
	status, raw := h.allow(t, byNumber.SessionID, `{"numbers":["`+allowedCaller+`","12025550144","`+allowedCaller+`"]}`)
	if status != http.StatusBadRequest || !strings.Contains(string(raw), "/numbers/1") || strings.Contains(string(raw), "2025550144") {
		t.Errorf("a malformed number was taken or echoed: %d %s", status, raw)
	}
	tooMany := `{"numbers":["` + strings.TrimSuffix(strings.Repeat(allowedCaller+`","`, 51), `","`) + `"]}`
	if status, _ := h.allow(t, byNumber.SessionID, tooMany); status != http.StatusBadRequest {
		t.Errorf("51 numbers were taken: %d", status)
	}
	status, raw = h.allow(t, byNumber.SessionID, `{"numbers":["`+allowedCaller+`","`+allowedCaller+`","+919876543210"]}`)
	if status != http.StatusOK || !strings.Contains(string(raw), `"allowedNumbers":2`) || strings.Contains(string(raw), "2025550143") {
		t.Errorf("allowed numbers: %d %s", status, raw)
	}
	if ok, err := h.store.DialInAllows(t.Context(), byNumber.SessionID, allowedCaller); !ok || err != nil {
		t.Errorf("the allowed number was not stored: %v", err)
	}
	if status, _ := h.allow(t, byNumber.SessionID, `{"numbers":[]}`); status != http.StatusOK {
		t.Errorf("clearing the list: %d", status)
	}
	if ok, _ := h.store.DialInAllows(t.Context(), byNumber.SessionID, allowedCaller); ok {
		t.Error("a cleared list still allowed the number")
	}
	if strings.Contains(logs.String(), "2025550143") || strings.Contains(logs.String(), "9876543210") {
		t.Error("an allowed number reached the logs")
	}
	plain := h.create(t, request("hi", "webrtc"))
	if status, _ := h.allow(t, plain.SessionID, `{"numbers":["`+allowedCaller+`"]}`); status != http.StatusBadRequest {
		t.Errorf("a meeting without dial-in kept numbers: %d", status)
	}
}

func TestADialInEndsWhenItsRoomClosesAfterRunning(t *testing.T) {
	t.Parallel()
	h, _ := serveMeetings(t)
	waiting, running := h.openMeeting(t, ""), h.openMeeting(t, "pin_and_number")
	h.transport.closeRoom(waiting.Room)
	if err := h.svc.SweepDialIns(t.Context()); err != nil {
		t.Fatal(err)
	}
	h.transport.closeRoom(running.Room)
	if err := h.svc.SweepDialIns(t.Context()); err != nil {
		t.Fatal(err)
	}
	if _, err := h.store.DialInPIN(t.Context(), running.SessionID); !errors.Is(err, state.ErrNotFound) {
		t.Errorf("a meeting whose room closed kept its PIN: %v", err)
	}
	if pin, err := h.store.DialInPIN(t.Context(), waiting.SessionID); err != nil || pin != waiting.DialIn.PIN {
		t.Errorf("a meeting nobody has opened yet lost its PIN: %v", err)
	}
	if status, raw := h.allow(t, running.SessionID, `{"numbers":[]}`); status != http.StatusBadRequest || !strings.Contains(string(raw), "has ended") {
		t.Errorf("an ended dial-in took numbers: %d %s", status, raw)
	}
}
