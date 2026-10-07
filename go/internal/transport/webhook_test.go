package transport_test

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"

	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/transport"
)

func signedWebhook(t *testing.T, body []byte, key, secret string, claims jwt.MapClaims) *http.Request {
	t.Helper()
	sum := sha256.Sum256(body)
	all := jwt.MapClaims{"iss": key, "exp": time.Now().Add(5 * time.Minute).Unix(), "sha256": base64.StdEncoding.EncodeToString(sum[:])}
	for k, v := range claims {
		if v == nil {
			delete(all, k)
			continue
		}
		all[k] = v
	}
	token, err := jwt.NewWithClaims(jwt.SigningMethodHS256, all).SignedString([]byte(secret))
	if err != nil {
		t.Fatalf("sign: %v", err)
	}
	r := httptest.NewRequest(http.MethodPost, "/livekit/webhook", bytes.NewReader(body))
	r.Header.Set("Authorization", token)
	return r
}

func webhookReader(t *testing.T) transport.Transport {
	t.Helper()
	srv := httptest.NewServer(http.NotFoundHandler())
	t.Cleanup(srv.Close)
	return recorder(t, srv)
}

func TestAWebhookTheMediaServerSignedIsRead(t *testing.T) {
	t.Parallel()
	lk := webhookReader(t)
	body := fixture(t, "../webhook/track-published.json")
	hook, err := lk.ReadWebhook(signedWebhook(t, body, apiKey, apiSecret, nil))
	if err != nil {
		t.Fatalf("ReadWebhook: %v", err)
	}
	if hook.Event != transport.EventTrackPublished || hook.Room != sessionID || hook.TrackID != audioTrackID || hook.Participant != "p_4b81e0d7" {
		t.Errorf("read %+v", hook)
	}
}

func TestEveryEventTheControlPlaneActsOnIsRead(t *testing.T) {
	t.Parallel()
	lk := webhookReader(t)
	read := func(name string) transport.Webhook {
		t.Helper()
		hook, err := lk.ReadWebhook(signedWebhook(t, fixture(t, "../webhook/"+name), apiKey, apiSecret, nil))
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		return hook
	}
	if h := read("participant-joined.json"); h.Event != transport.EventParticipantJoined || h.Room != sessionID || h.Participant != "p_4b81e0d7" || !h.At.Equal(time.Unix(1791316752, 0)) {
		t.Errorf("participant joined read as %+v", h)
	}
	if h := read("participant-joined-agent.json"); h.Participant != "agent-AJ_4w6LsXJWudCx" {
		t.Errorf("agent joined read as %+v", h)
	}
	if h := read("room-finished.json"); h.Event != transport.EventRoomFinished || h.Room != sessionID {
		t.Errorf("room finished read as %+v", h)
	}
	h := read("egress-ended.json")
	if h.Event != transport.EventEgressEnded || h.Room != sessionID || h.Egress.EgressID != egressID || h.Egress.Status != "EGRESS_COMPLETE" ||
		!h.Egress.EndedAt.Equal(time.Unix(0, 1791316767140796103)) {
		t.Errorf("egress ended read as %+v", h)
	}
}

func TestAWebhookAnyoneElseCouldHaveSentIsRefused(t *testing.T) {
	t.Parallel()
	lk := webhookReader(t)
	body := fixture(t, "../webhook/track-published.json")
	tampered := append(bytes.Clone(body[:len(body)-2]), ' ', '}')
	for name, r := range map[string]*http.Request{
		"another secret":   signedWebhook(t, body, apiKey, "not-the-secret", nil),
		"another key":      signedWebhook(t, body, "another-key", apiSecret, nil),
		"a changed body":   signedWebhook(t, body, apiKey, apiSecret, jwt.MapClaims{"sha256": base64.StdEncoding.EncodeToString(tampered)}),
		"no expiry":        signedWebhook(t, body, apiKey, apiSecret, jwt.MapClaims{"exp": nil}),
		"an expired token": signedWebhook(t, body, apiKey, apiSecret, jwt.MapClaims{"exp": time.Now().Add(-time.Minute).Unix()}),
	} {
		var de *errs.Error
		if _, err := lk.ReadWebhook(r); !errors.As(err, &de) || de.Code != errs.CodeAuthenticationFailed {
			t.Errorf("%s: %v, want %s", name, err, errs.CodeAuthenticationFailed)
		}
	}
}
