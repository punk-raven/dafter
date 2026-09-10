package config

import (
	"errors"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/errs"
)

const minimal = `{
  "apiVersion":"dafter.dev/v1","sessionId":"s_7f3a9c21","tenantId":"t_9c21a4be",
  "privacyMode":"open","language":"en-IN","channel":"webrtc",
  "agent":{"enabled":true,"pool":"dafter-py"},"turn":{"strategy":"auto"},
  "recording":{"enabled":false},"budgets":{"turnGapP50Ms":800,"turnGapP95Ms":1500}
}`

func withField(extra string) []byte {
	return []byte(strings.TrimSuffix(strings.TrimSpace(minimal), "}") + "," + extra + "}")
}

func TestParseAcceptsAMinimalDocument(t *testing.T) {
	c, err := Parse([]byte(minimal))
	if err != nil {
		t.Fatalf("minimal document rejected: %v", err)
	}
	if c.Agent.Pool != "dafter-py" {
		t.Errorf("pool = %q", c.Agent.Pool)
	}
}

func TestParseRejectsAMisspelledField(t *testing.T) {
	err := mustFail(t, withField(`"privacymode":"open"`))
	if !strings.Contains(strings.Join(err.Details, "\n"), "privacymode") {
		t.Errorf("the typo is not named in the error:\n%v", err)
	}
}

func TestParseRejectsAnUnknownField(t *testing.T) {
	_ = mustFail(t, withField(`"totallyUnknownField":"typo"`))
}

func TestParseRejectsAValueTheStructWouldAccept(t *testing.T) {
	raw := strings.Replace(minimal, `"pool":"dafter-py"`, `"pool":"NOT A VALID POOL NAME"`, 1)
	err := mustFail(t, []byte(raw))
	if !strings.Contains(strings.Join(err.Details, "\n"), "/agent/pool") {
		t.Errorf("want a located /agent/pool problem, got:\n%v", err)
	}
}

func TestParseRejectsMalformedJSON(t *testing.T) {
	_ = mustFail(t, []byte(`{"apiVersion":`))
}

func TestParseAppliesCrossFieldRules(t *testing.T) {
	raw := strings.Replace(minimal, `"privacyMode":"open"`, `"privacyMode":"sealed"`, 1)
	err := mustFail(t, []byte(raw))
	if err.Code != errs.CodePrivacyModeForbids {
		t.Errorf("want %s, got %s", errs.CodePrivacyModeForbids, err.Code)
	}
}

func mustFail(t *testing.T, raw []byte) *errs.Error {
	t.Helper()
	c, err := Parse(raw)
	if err == nil {
		t.Fatalf("accepted a document it should have refused: %+v", c)
	}
	var de *errs.Error
	if !errors.As(err, &de) {
		t.Fatalf("want *errs.Error, got %T: %v", err, err)
	}
	return de
}
