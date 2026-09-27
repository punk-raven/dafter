package control_test

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
)

func versionEvent(t *testing.T, call recordedCall, createdAt string, mutate func(map[string]any)) string {
	t.Helper()
	var human, agent sourceView
	for _, s := range call.sources.Sources {
		if s.Speaker.Kind == "human" {
			human = s
		} else {
			agent = s
		}
	}
	speaker := func(s sourceView) map[string]any {
		out := map[string]any{"kind": s.Speaker.Kind}
		if s.Speaker.ParticipantID != "" {
			out["participantId"] = s.Speaker.ParticipantID
		}
		return out
	}
	line := func(s sourceView, text string) map[string]any {
		return map[string]any{"speaker": speaker(s), "recordingId": s.RecordingID, "startMs": 1210, "endMs": 3480, "text": text}
	}
	payload := map[string]any{
		"pass":     "batch",
		"language": "hi",
		"provenance": map[string]any{
			"provider": "sarvam", "model": "saaras:v3", "configHash": call.session.ConfigHash, "createdAt": createdAt,
			"recordings": []any{
				map[string]any{"recordingId": human.RecordingID, "speaker": speaker(human), "startedAt": human.StartedAt.Format(time.RFC3339Nano)},
				map[string]any{"recordingId": agent.RecordingID, "speaker": speaker(agent), "startedAt": agent.StartedAt.Format(time.RFC3339Nano)},
			},
		},
		"verbatim": []any{line(human, "उम्म मीटिंग कब है"), line(agent, "दस बजे")},
		"clean":    []any{line(human, "मीटिंग कब है?"), line(agent, "10 बजे।")},
	}
	if mutate != nil {
		mutate(payload)
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	hash, err := config.HashWithout(raw, "transcriptHash")
	if err != nil {
		t.Fatal(err)
	}
	if _, stated := payload["transcriptHash"]; !stated {
		payload["transcriptHash"] = hash
	}
	envelope := map[string]any{
		"eventId": "e_5c4b3a2918f7e6d5c4b3a2918f7e6d5c", "type": "transcript.version_created", "version": 1,
		"sessionId": call.session.SessionID, "tenantId": tenantID, "sequence": 0,
		"occurredAt": createdAt, "payload": payload,
	}
	out, err := json.Marshal(envelope)
	if err != nil {
		t.Fatal(err)
	}
	return string(out)
}

type versionReply struct {
	Version        int       `json:"version"`
	TranscriptHash string    `json:"transcriptHash"`
	Provider       string    `json:"provider"`
	Model          string    `json:"model"`
	CreatedAt      time.Time `json:"createdAt"`
	StoredAt       time.Time `json:"storedAt"`
}

func (h *harness) submit(t *testing.T, sessionID, body string) (int, []byte) {
	t.Helper()
	return h.as(t, workerSecret, http.MethodPost, "/sessions/"+sessionID+"/transcripts", body)
}

func TestAReRunIsANewVersionAndEveryVersionExportsWithItsHash(t *testing.T) {
	t.Parallel()
	h := serve(t)
	call := recordCall(t, h)
	path := "/sessions/" + call.session.SessionID + "/transcripts"

	first := versionEvent(t, call, "2026-09-24T10:31:12.004Z", nil)
	for i, want := range []int{http.StatusCreated, http.StatusOK} {
		status, raw := h.submit(t, call.session.SessionID, first)
		var got versionReply
		if err := json.Unmarshal(raw, &got); err != nil {
			t.Fatal(err)
		}
		if status != want || got.Version != 1 || got.Provider != "sarvam" || got.Model != "saaras:v3" {
			t.Fatalf("submission %d: %d %s", i, status, raw)
		}
	}
	status, raw := h.submit(t, call.session.SessionID, versionEvent(t, call, "2026-09-24T11:02:40.500Z", nil))
	if status != http.StatusCreated || !strings.Contains(string(raw), `"version":2`) {
		t.Fatalf("a re-run: %d %s", status, raw)
	}

	status, raw = h.as(t, workerSecret, http.MethodGet, path, "")
	var listed struct {
		SessionID string         `json:"sessionId"`
		Versions  []versionReply `json:"versions"`
	}
	if err := json.Unmarshal(raw, &listed); err != nil || status != http.StatusOK {
		t.Fatalf("list: %d %s", status, raw)
	}
	if len(listed.Versions) != 2 || listed.Versions[0].Version != 1 || listed.Versions[1].Version != 2 ||
		!listed.Versions[1].CreatedAt.Equal(time.Date(2026, 9, 24, 11, 2, 40, 500000000, time.UTC)) {
		t.Errorf("versions %+v", listed.Versions)
	}

	status, raw = h.as(t, workerSecret, http.MethodGet, path+"/1", "")
	var export struct {
		SessionID  string          `json:"sessionId"`
		Version    int             `json:"version"`
		Transcript json.RawMessage `json:"transcript"`
	}
	if err := json.Unmarshal(raw, &export); err != nil || status != http.StatusOK || export.Version != 1 {
		t.Fatalf("export: %d %s", status, raw)
	}
	rehashed, err := config.HashWithout(export.Transcript, "transcriptHash")
	if err != nil {
		t.Fatal(err)
	}
	if rehashed != listed.Versions[0].TranscriptHash || !strings.Contains(string(export.Transcript), rehashed) {
		t.Errorf("the export re-hashes to %s, stored %s", rehashed, listed.Versions[0].TranscriptHash)
	}
	for _, bad := range []string{"/3", "/0", "/one"} {
		if status, _ := h.as(t, workerSecret, http.MethodGet, path+bad, ""); status != http.StatusBadRequest {
			t.Errorf("GET %s: %d", bad, status)
		}
	}
}

func TestATranscriptVersionMustProveHowItWasMade(t *testing.T) {
	t.Parallel()
	h := serve(t)
	call := recordCall(t, h)
	provenance := func(p map[string]any) map[string]any { return p["provenance"].(map[string]any) }
	recording := func(p map[string]any, i int) map[string]any {
		return provenance(p)["recordings"].([]any)[i].(map[string]any)
	}
	cases := map[string]func(map[string]any){
		"a hash of something else":    func(p map[string]any) { p["transcriptHash"] = strings.Repeat("0", 64) },
		"another session's config":    func(p map[string]any) { provenance(p)["configHash"] = strings.Repeat("a", 64) },
		"a recording it never made":   func(p map[string]any) { recording(p, 0)["recordingId"] = "EG_elsewhere" },
		"a recording still finishing": func(p map[string]any) { recording(p, 0)["recordingId"] = "EG_stub6" },
		"someone else's track": func(p map[string]any) {
			recording(p, 0)["speaker"] = map[string]any{"kind": "human", "participantId": ravi}
		},
		"a start the recording never had": func(p map[string]any) {
			recording(p, 0)["startedAt"] = "2020-01-01T00:00:00Z"
		},
		"a line from outside its provenance": func(p map[string]any) {
			p["clean"].([]any)[0].(map[string]any)["recordingId"] = "EG_stub2"
		},
		"a line given to the wrong speaker": func(p map[string]any) {
			p["verbatim"].([]any)[1].(map[string]any)["speaker"] = map[string]any{"kind": "human", "participantId": asha}
		},
		"not the batch pass": func(p map[string]any) { p["pass"] = "realtime" },
	}
	for name, mutate := range cases {
		status, raw := h.submit(t, call.session.SessionID, versionEvent(t, call, "2026-09-24T10:31:12.004Z", mutate))
		var de errs.Error
		if err := json.Unmarshal(raw, &de); err != nil || status != http.StatusBadRequest || de.Code != errs.CodeInvalidConfig || len(de.Details) == 0 {
			t.Errorf("%s: %d %s", name, status, raw)
		}
	}
	good := versionEvent(t, call, "2026-09-24T10:31:12.004Z", nil)
	if status, _ := h.as(t, "", http.MethodPost, "/sessions/"+call.session.SessionID+"/transcripts", good); status != http.StatusUnauthorized {
		t.Errorf("a transcript was stored without the worker credential: %d", status)
	}
	other := h.create(t, afterCallRequest("after_call"))
	if status, _ := h.submit(t, other.SessionID, good); status != http.StatusBadRequest {
		t.Errorf("one session's transcript was stored under another: %d", status)
	}
	if status, raw := h.as(t, workerSecret, http.MethodGet, "/sessions/"+call.session.SessionID+"/transcripts", ""); status != http.StatusOK || !strings.Contains(string(raw), `"versions":[]`) {
		t.Errorf("a refused version was stored: %s", raw)
	}
}

func TestATranscriptIsReadOnlyWithTheWorkerCredential(t *testing.T) {
	t.Parallel()
	h := serve(t)
	call := recordCall(t, h)
	if status, raw := h.submit(t, call.session.SessionID, versionEvent(t, call, "2026-09-24T10:31:12.004Z", nil)); status != http.StatusCreated {
		t.Fatalf("store: %d %s", status, raw)
	}
	path := "/sessions/" + call.session.SessionID + "/transcripts"
	for _, read := range []string{path, path + "/1"} {
		for name, credential := range map[string]string{
			"no credential": "", "a wrong secret": "not-the-secret", "a participant's token": call.session.Token,
		} {
			status, raw := h.as(t, credential, http.MethodGet, read, "")
			var de errs.Error
			if err := json.Unmarshal(raw, &de); err != nil || status != http.StatusUnauthorized ||
				de.Code != errs.CodeAuthenticationFailed || strings.Contains(string(raw), "transcriptHash") {
				t.Errorf("GET %s with %s: %d %s", read, name, status, raw)
			}
		}
		if status, raw := h.as(t, workerSecret, http.MethodGet, read, ""); status != http.StatusOK {
			t.Errorf("GET %s with the worker credential: %d %s", read, status, raw)
		}
	}
}
