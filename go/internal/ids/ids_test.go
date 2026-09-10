package ids

import "testing"

func TestNewIDIsOpaqueAndWellFormed(t *testing.T) {
	for _, p := range []IDPrefix{PrefixTenant, PrefixSession, PrefixParticipant, PrefixRecording, PrefixEvent} {
		id, err := NewID(p)
		if err != nil {
			t.Fatalf("NewID(%q): %v", p, err)
		}
		if err := ValidateID(p, id); err != nil {
			t.Errorf("NewID(%q) produced %q which fails its own validator: %v", p, id, err)
		}
	}
}

func TestValidateIDRejectsTheWrongKind(t *testing.T) {
	id := MustNewID(PrefixSession)
	if err := ValidateID(PrefixParticipant, id); err == nil {
		t.Fatalf("a session id %q was accepted as a participant id", id)
	}
}

func TestValidateIDRejectsIdentifyingValues(t *testing.T) {
	for _, bad := range []string{"jane@example.com", "+919876543210", "Jane Doe", "s_", "s_nothex!", "S_7F3A9C21"} {
		if err := ValidateID(PrefixSession, bad); err == nil {
			t.Errorf("ValidateID accepted %q", bad)
		}
	}
}

func TestNewIDIsUnique(t *testing.T) {
	seen := make(map[string]struct{}, 1000)
	for range 1000 {
		id := MustNewID(PrefixEvent)
		if _, dup := seen[id]; dup {
			t.Fatalf("duplicate identifier %q; event ids double as webhook idempotency keys", id)
		}
		seen[id] = struct{}{}
	}
}
