package vobiz_test

import (
	"bytes"
	"errors"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/punk-raven/dafter/go/internal/carrier/vobiz"
	"github.com/punk-raven/dafter/go/internal/errs"
)

const (
	base      = "https://calls.example.com/telephony/vobiz/answer"
	key       = "not-a-real-token"
	nonce     = "12345678901234567890"
	reference = "rh9LWW9+XhcnRljNtFdsPmu8CDf6n4+ja1e+kOOhgIA="
	token     = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
)

func fixture(t *testing.T, name string) []byte {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("testdata", name))
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func form(t *testing.T, name string) url.Values {
	t.Helper()
	values, err := url.ParseQuery(strings.TrimSpace(string(fixture(t, name))))
	if err != nil {
		t.Fatal(err)
	}
	return values
}

func signed(signature, n string) http.Header {
	h := http.Header{}
	h.Set(vobiz.SignatureHeader, signature)
	h.Set(vobiz.NonceHeader, n)
	return h
}

func TestTheSignatureMatchesTheDocumentedFormula(t *testing.T) {
	t.Parallel()
	got, ok := vobiz.Verify(base, key, signed(reference, nonce))
	if !ok || got != nonce {
		t.Fatalf("the reference signature did not verify: %q %v", got, ok)
	}
	for name, c := range map[string]struct{ base, key, sig, nonce string }{
		"another url":      {base + "x", key, reference, nonce},
		"another key":      {base, key + "x", reference, nonce},
		"another nonce":    {base, key, reference, "12345678901234567891"},
		"no signature":     {base, key, "", nonce},
		"no nonce":         {base, key, reference, ""},
		"no key":           {base, "", reference, nonce},
		"a V2 signature":   {base, key, "hmOK2yUy3M8K2pF8yQY5mDDkq8Y0x0Qj0l3E4x0qZp8=", nonce},
		"a lettered nonce": {base, key, reference, "abc"},
	} {
		if _, ok := vobiz.Verify(c.base, c.key, signed(c.sig, c.nonce)); ok {
			t.Errorf("%s verified", name)
		}
	}
}

func TestAnAnswerNamesTheCallAndTheNumberItRangNeverTheCaller(t *testing.T) {
	t.Parallel()
	a, err := vobiz.ParseAnswer(form(t, "answer.form"))
	if err != nil {
		t.Fatal(err)
	}
	if a != (vobiz.Answer{CallUUID: "550e8400-e29b-41d4-a716-446655440000", To: "+12025550100"}) {
		t.Errorf("answer = %+v", a)
	}
	bad := form(t, "answer.form")
	bad.Set("Direction", "outbound")
	bad.Set("CallUUID", "x")
	bad.Set("To", "sip:someone@example.com")
	_, err = vobiz.ParseAnswer(bad)
	var de *errs.Error
	if !errors.As(err, &de) || len(de.Details) != 3 || strings.Contains(de.Error(), "2025550143") {
		t.Errorf("want every problem located and no number repeated: %v", err)
	}
}

func TestABridgeCarriesItsTokenAndAConferenceEntryItsCall(t *testing.T) {
	t.Parallel()
	b, err := vobiz.ParseBridge(form(t, "bridge.form"))
	if err != nil || b.Token != token || b.CallUUID != "7c9e6679-7425-40de-944b-e07fc1f90ae7" {
		t.Errorf("bridge = %+v, %v", b, err)
	}
	unsigned := form(t, "bridge.form")
	unsigned.Set(vobiz.BridgeHeader, "not-hex")
	if _, err := vobiz.ParseBridge(unsigned); err == nil {
		t.Error("a malformed token was taken")
	}
	uuid, ok := vobiz.Entered(form(t, "conference-enter.form"))
	if !ok || uuid != "550e8400-e29b-41d4-a716-446655440000" {
		t.Errorf("entered = %q %v", uuid, ok)
	}
	exit := form(t, "conference-enter.form")
	exit.Set("ConferenceAction", "exit")
	if _, ok := vobiz.Entered(exit); ok {
		t.Error("an exit was taken for an entry")
	}
}

func TestTheAnswersArePinned(t *testing.T) {
	t.Parallel()
	limit := 30 * time.Minute
	for name, got := range map[string][]byte{
		"hold.xml":   vobiz.Hold(token, "https://calls.example.com/telephony/vobiz/held/"+token, limit),
		"join.xml":   vobiz.Join(token, limit),
		"hangup.xml": vobiz.Hangup(),
	} {
		if want := bytes.TrimSpace(fixture(t, name)); !bytes.Equal(got, want) {
			t.Errorf("%s differs\n got: %s\nwant: %s", name, got, want)
		}
	}
	if escaped := vobiz.Join("a<b", limit); !bytes.Contains(escaped, []byte("a&lt;b")) {
		t.Errorf("a room name was not escaped: %s", escaped)
	}
}
