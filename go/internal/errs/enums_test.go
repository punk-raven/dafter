package errs_test

import (
	"testing"

	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/schema"
)

func TestGeneratedEnumsMatchSchema(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name    string
		file    string
		pointer []string
		got     []string
	}{
		{"ErrorCode", "errors/v1/error.schema.json", []string{"$defs", "ErrorCode", "enum"}, schema.Names(errs.AllErrorCodes)},
		{"Stage", "errors/v1/error.schema.json", []string{"properties", "stage", "enum"}, schema.Names(errs.AllStages)},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			if err := schema.CheckEnum(tc.file, tc.pointer, tc.got); err != nil {
				t.Error(err)
			}
		})
	}
}
