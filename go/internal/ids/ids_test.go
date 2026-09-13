package ids_test

import (
	"testing"

	"github.com/punk-raven/dafter/go/internal/ids"
)

func TestNewIDIsOpaqueAndWellFormed(t *testing.T) {
	t.Parallel()
	for _, p := range []ids.IDPrefix{
		ids.PrefixTenant, ids.PrefixSession, ids.PrefixParticipant,
		ids.PrefixRecording, ids.PrefixEvent,
	} {
		id, err := ids.NewID(p)
		if err != nil {
			t.Fatalf("NewID(%q): %v", p, err)
		}
		if err := ids.ValidateID(p, id); err != nil {
			t.Errorf("NewID(%q) produced %q which fails its own validator: %v", p, id, err)
		}
	}
}

func TestValidateIDRejectsTheWrongKind(t *testing.T) {
	t.Parallel()
	id := ids.MustNewID(ids.PrefixSession)
	if err := ids.ValidateID(ids.PrefixParticipant, id); err == nil {
		t.Fatalf("a session id %q was accepted as a participant id", id)
	}
}

func TestValidateIDRejectsIdentifyingValues(t *testing.T) {
	t.Parallel()
	for _, bad := range []string{
		"jane@example.com", "+919876543210", "Jane Doe",
		"s_", "s_nothex!", "S_7F3A9C21",
	} {
		if err := ids.ValidateID(ids.PrefixSession, bad); err == nil {
			t.Errorf("ValidateID accepted %q", bad)
		}
	}
}

func TestNewIDIsUnique(t *testing.T) {
	t.Parallel()
	seen := make(map[string]struct{}, 1000)
	for range 1000 {
		id := ids.MustNewID(ids.PrefixEvent)
		if _, dup := seen[id]; dup {
			t.Fatalf("duplicate identifier %q; event ids double as webhook idempotency keys", id)
		}
		seen[id] = struct{}{}
	}
}
