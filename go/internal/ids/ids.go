// Package ids mints and validates the platform's opaque identifiers.
package ids

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"regexp"
)

type IDPrefix string

const (
	PrefixTenant      IDPrefix = "t"
	PrefixSession     IDPrefix = "s"
	PrefixParticipant IDPrefix = "p"
	PrefixRecording   IDPrefix = "r"
	PrefixEvent       IDPrefix = "e"
)

// Event ids double as webhook idempotency keys that consumers store: 16 is not
// widenable later.
var idBytes = map[IDPrefix]int{
	PrefixTenant:      4,
	PrefixSession:     4,
	PrefixParticipant: 4,
	PrefixRecording:   4,
	PrefixEvent:       16,
}

var idPattern = regexp.MustCompile(`^([tspre])_([0-9a-f]+)$`)

func NewID(p IDPrefix) (string, error) {
	n, ok := idBytes[p]
	if !ok {
		return "", fmt.Errorf("ids: unknown id prefix %q", p)
	}
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return "", fmt.Errorf("ids: read entropy: %w", err)
	}
	return string(p) + "_" + hex.EncodeToString(b), nil
}

func MustNewID(p IDPrefix) string {
	id, err := NewID(p)
	if err != nil {
		panic(err)
	}
	return id
}

func ValidateID(p IDPrefix, id string) error {
	n, ok := idBytes[p]
	if !ok {
		return fmt.Errorf("ids: unknown id prefix %q", p)
	}
	m := idPattern.FindStringSubmatch(id)
	if m == nil {
		return fmt.Errorf("ids: %q is not an opaque identifier", id)
	}
	if m[1] != string(p) {
		return fmt.Errorf("ids: %q is a %q identifier, want %q", id, m[1], p)
	}
	if len(m[2]) != n*2 {
		return fmt.Errorf("ids: %q has %d hex digits, want %d", id, len(m[2]), n*2)
	}
	return nil
}
