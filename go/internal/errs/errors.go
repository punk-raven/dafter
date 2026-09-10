package errs

import (
	"fmt"
	"strings"
)

type ProviderContext struct {
	Name       string `json:"name"`
	RequestID  string `json:"requestId,omitempty"`
	NativeCode string `json:"nativeCode,omitempty"`
}

// Message must stay safe to log: no name, email, phone or transcript content.
type Error struct {
	Code      ErrorCode        `json:"code"`
	Message   string           `json:"message"`
	Retryable bool             `json:"retryable"`
	Stage     Stage            `json:"stage,omitempty"`
	Provider  *ProviderContext `json:"provider,omitempty"`
	Details   []string         `json:"details,omitempty"`

	wrapped error
}

func (e *Error) Error() string {
	var b strings.Builder
	fmt.Fprintf(&b, "%s: %s", e.Code, e.Message)
	if e.Provider != nil && e.Provider.RequestID != "" {
		fmt.Fprintf(&b, " (provider %s request %s)", e.Provider.Name, e.Provider.RequestID)
	}
	for _, d := range e.Details {
		fmt.Fprintf(&b, "\n  %s", d)
	}
	return b.String()
}

func (e *Error) Unwrap() error { return e.wrapped }

func Errorf(code ErrorCode, format string, args ...any) *Error {
	return &Error{Code: code, Message: fmt.Sprintf(format, args...), Retryable: retryable(code)}
}

func Wrap(code ErrorCode, err error, format string, args ...any) *Error {
	e := Errorf(code, format, args...)
	e.wrapped = err
	return e
}

// Auth and quota failures are excluded on purpose: retrying burns budget and
// delays the page.
func retryable(code ErrorCode) bool {
	switch code {
	case CodeProviderUnavailable, CodeProviderTimeout, CodeRateLimited, CodeStreamClosed:
		return true
	default:
		return false
	}
}
