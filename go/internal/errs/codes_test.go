package errs_test

import (
	"slices"
	"testing"

	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/schema"
)

func TestErrorCodesMatchSchema(t *testing.T) {
	want, err := schema.EnumAt("errors/v1/error.schema.json", "$defs", "ErrorCode", "enum")
	if err != nil {
		t.Fatal(err)
	}
	got := []string{
		string(errs.CodeInvalidConfig), string(errs.CodeUnsupportedCapability), string(errs.CodeResidencyViolation),
		string(errs.CodeConsentRequired), string(errs.CodePrivacyModeForbids), string(errs.CodeQuotaExceeded),
		string(errs.CodeBudgetExceeded), string(errs.CodeRateLimited), string(errs.CodeAuthenticationFailed),
		string(errs.CodeProviderUnavailable), string(errs.CodeProviderTimeout), string(errs.CodeStreamClosed),
		string(errs.CodeCancelled), string(errs.CodeInternal),
	}
	slices.Sort(got)
	if !slices.Equal(got, want) {
		t.Errorf("ErrorCode drift\n go: %v\n schema: %v", got, want)
	}
}
