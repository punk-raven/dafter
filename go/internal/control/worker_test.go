package control_test

import (
	"bytes"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/errs"
)

func (h *harness) worker(t *testing.T, credential, sessionID, action, body string) (int, []byte) {
	t.Helper()
	req, err := http.NewRequestWithContext(t.Context(), http.MethodPost,
		h.server.URL+"/sessions/"+sessionID+"/agent/"+action, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	if credential != "" {
		req.Header.Set("Authorization", "Bearer "+credential)
	}
	resp, err := h.server.Client().Do(req)
	if err != nil {
		t.Fatalf("POST agent/%s: %v", action, err)
	}
	defer closeBody(t, resp)
	raw, err := readAll(resp)
	if err != nil {
		t.Fatal(err)
	}
	return resp.StatusCode, raw
}

func keyRequest(hash string) string {
	return `{"configHash":"` + hash + `"}`
}

func TestTheWorkerFetchesATrustedAgentSessionKeyOverItsOwnCredential(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, trustedAgentRequest("hi"))
	status, raw := h.worker(t, workerSecret, created.SessionID, "key", keyRequest(created.ConfigHash))
	if status != http.StatusOK {
		t.Fatalf("agent key returned %d: %s", status, raw)
	}
	var got struct {
		SessionID     string `json:"sessionId"`
		EncryptionKey string `json:"encryptionKey"`
	}
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatal(err)
	}
	if got.SessionID != created.SessionID || got.EncryptionKey != created.EncryptionKey {
		t.Errorf("the worker got %+v, want the one key the humans hold; two keys make one session two calls", got)
	}
	for _, d := range h.transport.dispatched {
		if bytes.Contains(d.Metadata, []byte(created.EncryptionKey)) {
			t.Error("the key rode the dispatch, which the media server reads")
		}
	}
}

func TestTheSessionKeyIsWithheldUnlessTheWorkerAndTheModeAllowIt(t *testing.T) {
	t.Parallel()
	h := serve(t)
	trusted := h.create(t, trustedAgentRequest("hi"))
	sealed := h.create(t, sealedRequest("hi"))
	open := h.create(t, request("hi", "webrtc"))

	cases := []struct {
		name, credential, session, body string
		status                          int
		code                            errs.ErrorCode
		pointer                         string
	}{
		{"no credential", "", trusted.SessionID, keyRequest(trusted.ConfigHash), http.StatusUnauthorized, errs.CodeAuthenticationFailed, ""},
		{"a client token", trusted.Token, trusted.SessionID, keyRequest(trusted.ConfigHash), http.StatusUnauthorized, errs.CodeAuthenticationFailed, ""},
		{"another document", workerSecret, trusted.SessionID, keyRequest(open.ConfigHash), http.StatusBadRequest, errs.CodeInvalidConfig, "/configHash"},
		{"a field the call does not take", workerSecret, trusted.SessionID, `{"configHash":"` + trusted.ConfigHash + `","role":"participant"}`, http.StatusBadRequest, errs.CodeInvalidConfig, ""},
		{"sealed", workerSecret, sealed.SessionID, keyRequest(sealed.ConfigHash), http.StatusBadRequest, errs.CodeInvalidConfig, "/agent/enabled"},
		{"open", workerSecret, open.SessionID, keyRequest(open.ConfigHash), http.StatusBadRequest, errs.CodePrivacyModeForbids, "/privacyMode"},
	}
	for _, c := range cases {
		status, raw := h.worker(t, c.credential, c.session, "key", c.body)
		var de errs.Error
		if err := json.Unmarshal(raw, &de); err != nil {
			t.Fatalf("%s: %v: %s", c.name, err, raw)
		}
		if status != c.status || de.Code != c.code {
			t.Errorf("%s: returned %d %s, want %d %s", c.name, status, de.Code, c.status, c.code)
		}
		if c.pointer != "" && (len(de.Details) != 1 || !strings.Contains(de.Details[0], c.pointer)) {
			t.Errorf("%s: details %v do not locate %s", c.name, de.Details, c.pointer)
		}
		for _, key := range []string{trusted.EncryptionKey, sealed.EncryptionKey} {
			if bytes.Contains(raw, []byte(key)) {
				t.Errorf("%s: a refused call carried a session key", c.name)
			}
		}
	}

	h.svc.WorkerSecret = ""
	if status, _ := h.worker(t, "", trusted.SessionID, "key", keyRequest(trusted.ConfigHash)); status != http.StatusUnauthorized {
		t.Errorf("with no worker credential configured an empty one was accepted: %d", status)
	}
}

func TestAWorkerRefusalIsReadBackUntilTheNextInvite(t *testing.T) {
	t.Parallel()
	h := serve(t)
	got := h.create(t, request("hi", "webrtc"))
	refusal := `{"code":"unsupported_capability","message":"this worker cannot run the session's turn strategy","retryable":false,"details":["at '/turn/strategy': semantic"]}`

	for name, body := range map[string]string{
		"a field the error schema does not have": `{"code":"internal","message":"m","retryable":false,"transcript":"x"}`,
		"a code outside the taxonomy":            `{"code":"worker_sad","message":"m","retryable":false}`,
		"not json":                               `refused`,
	} {
		if status, raw := h.worker(t, workerSecret, got.SessionID, "refusal", body); status != http.StatusBadRequest {
			t.Errorf("%s: returned %d %s", name, status, raw)
		}
	}
	if status, _ := h.worker(t, "", got.SessionID, "refusal", refusal); status != http.StatusUnauthorized {
		t.Errorf("an unauthenticated refusal returned %d", status)
	}
	if h.read(t, got.SessionID).AgentRefusal != nil {
		t.Fatal("a rejected report was stored")
	}

	if status, raw := h.worker(t, workerSecret, got.SessionID, "refusal", refusal); status != http.StatusNoContent {
		t.Fatalf("refusal returned %d %s", status, raw)
	}
	var stored errs.Error
	if err := json.Unmarshal(h.read(t, got.SessionID).AgentRefusal, &stored); err != nil {
		t.Fatal(err)
	}
	if stored.Code != errs.CodeUnsupportedCapability || len(stored.Details) != 1 || !strings.Contains(stored.Details[0], "/turn/strategy") {
		t.Errorf("the session reads back refusal %+v", stored)
	}

	if status, reply := h.agent(t, got.SessionID, "start", ""); status != http.StatusCreated {
		t.Fatalf("invite returned %d %+v", status, reply)
	}
	if raw := h.read(t, got.SessionID).AgentRefusal; raw != nil {
		t.Errorf("a fresh invite still shows the last refusal %s", raw)
	}
}
