package core

import (
	"errors"
	"strings"
	"testing"
)

func TestValidationNamesEveryProblem(t *testing.T) {
	c := validConfig()
	c.APIVersion = "wrong"
	c.SessionID = "not-opaque"
	c.Channel = "carrier-pigeon"
	c.Budgets = Budgets{}

	err := c.Validate()
	if err == nil {
		t.Fatal("four broken fields were accepted")
	}
	var de *Error
	if !errors.As(err, &de) {
		t.Fatalf("want *Error, got %T", err)
	}

	for _, field := range []string{"/apiVersion", "/sessionId", "/channel", "/budgets/turnGapP50Ms"} {
		if !strings.Contains(strings.Join(de.Details, "\n"), field) {
			t.Errorf("no detail mentions %s; an operator cannot act on this error\n%v", field, err)
		}
	}
}

func TestMalformedEventIsAPlatformFault(t *testing.T) {
	e := event(EventAgentStateChanged, map[string]any{"state": "THINKING"})
	var de *Error
	if !errors.As(e.Validate(), &de) {
		t.Fatal("want *Error")
	}
	if de.Code != CodeInternal {
		t.Errorf("a malformed event reported %q; the consumer cannot fix a platform-generated event", de.Code)
	}
}

func TestErrorSerializesWithinItsOwnSchema(t *testing.T) {
	c := validConfig()
	c.Channel = "carrier-pigeon"
	var de *Error
	errors.As(c.Validate(), &de)

	if err := ValidateAgainst(SchemaError, de, CodeInternal); err != nil {
		t.Fatalf("the platform error type does not satisfy the error schema: %v", err)
	}
}

func TestRetryabilityIsNotSetPerCallSite(t *testing.T) {
	if Errorf(CodeAuthenticationFailed, "bad key").Retryable {
		t.Error("auth failures are retryable; retrying burns budget and delays the page")
	}
	if !Errorf(CodeProviderTimeout, "slow").Retryable {
		t.Error("provider timeouts are not retryable")
	}
}
