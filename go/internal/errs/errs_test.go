package errs_test

import (
	"testing"

	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/schema"
)

func TestGeneratedEnumsMatchSchema(t *testing.T) {
	t.Parallel()
	const file = "errors/v1/error.schema.json"
	cases := []struct {
		name    string
		pointer []string
		got     []string
	}{
		{"ErrorCode", []string{"$defs", "ErrorCode", "enum"}, schema.Names(errs.AllErrorCodes)},
		{"Stage", []string{"properties", "stage", "enum"}, schema.Names(errs.AllStages)},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			if err := schema.CheckEnum(file, tc.pointer, tc.got); err != nil {
				t.Error(err)
			}
		})
	}
}
