package errs

import "testing"

func TestRetryabilityIsNotSetPerCallSite(t *testing.T) {
	if Errorf(CodeAuthenticationFailed, "bad key").Retryable {
		t.Error("auth failures are retryable; retrying burns budget and delays the page")
	}
	if !Errorf(CodeProviderTimeout, "slow").Retryable {
		t.Error("provider timeouts are not retryable")
	}
}

func TestDetailsAppearInTheMessage(t *testing.T) {
	e := Errorf(CodeInvalidConfig, "2 problem(s)")
	e.Details = []string{"at '/a': bad", "at '/b': worse"}
	got := e.Error()
	for _, want := range []string{"at '/a': bad", "at '/b': worse"} {
		if !contains(got, want) {
			t.Errorf("%q missing from:\n%s", want, got)
		}
	}
}

func contains(s, sub string) bool {
	return len(s) >= len(sub) && (func() bool {
		for i := 0; i+len(sub) <= len(s); i++ {
			if s[i:i+len(sub)] == sub {
				return true
			}
		}
		return false
	})()
}
