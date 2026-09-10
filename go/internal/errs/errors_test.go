package errs

import (
	"strings"
	"testing"
)

func TestRetryabilityIsNotSetPerCallSite(t *testing.T) {
	t.Parallel()
	cases := []struct {
		code ErrorCode
		want bool
	}{
		{CodeAuthenticationFailed, false},
		{CodeQuotaExceeded, false},
		{CodeInvalidConfig, false},
		{CodeProviderTimeout, true},
		{CodeProviderUnavailable, true},
		{CodeRateLimited, true},
		{CodeStreamClosed, true},
	}
	for _, tc := range cases {
		t.Run(string(tc.code), func(t *testing.T) {
			t.Parallel()
			if got := Errorf(tc.code, "x").Retryable; got != tc.want {
				t.Errorf("%s retryable = %v, want %v", tc.code, got, tc.want)
			}
		})
	}
}

func TestDetailsAppearInTheMessage(t *testing.T) {
	t.Parallel()
	e := Errorf(CodeInvalidConfig, "2 problem(s)")
	e.Details = []string{"at '/a': bad", "at '/b': worse"}
	got := e.Error()
	for _, want := range e.Details {
		if !strings.Contains(got, want) {
			t.Errorf("%q missing from:\n%s", want, got)
		}
	}
}
