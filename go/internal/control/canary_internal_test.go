package control

import (
	"encoding/json"
	"fmt"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
)

func TestTheCanaryBucketIsDeterministic(t *testing.T) {
	t.Parallel()
	first := canaryBucket("support-v4", "caller")
	for range 3 {
		if again := canaryBucket("support-v4", "caller"); again != first {
			t.Fatalf("one caller hashed to buckets %d and %d", first, again)
		}
	}
	if first >= canaryBuckets {
		t.Fatalf("bucket %d fell outside the range", first)
	}
}

func canaryCatalog(percent int) *config.Catalog {
	return &config.Catalog{Profiles: map[string]json.RawMessage{
		"support":    json.RawMessage(fmt.Sprintf(`{"canary": {"profile": "support-v4", "percent": %d}}`, percent)),
		"support-v4": json.RawMessage(`{"version": {"id": "support-v4"}}`),
	}}
}

func candidates(percent, callers int) int {
	catalog := canaryCatalog(percent)
	n := 0
	for i := range callers {
		if routeCanary(catalog, config.Request{Profile: "support"}, fmt.Sprintf("caller-%d", i)).Candidate {
			n++
		}
	}
	return n
}

func TestTheCanarySendsItsShareOfCallers(t *testing.T) {
	t.Parallel()
	for percent, want := range map[int][2]int{0: {0, 0}, 5: {400, 600}, 10: {850, 1150}, 100: {10000, 10000}} {
		if got := candidates(percent, 10000); got < want[0] || got > want[1] {
			t.Errorf("%d%% sent %d of 10000 callers to the candidate, want %d to %d", percent, got, want[0], want[1])
		}
	}
}

func TestANewCandidateDrawsItsOwnCallers(t *testing.T) {
	t.Parallel()
	catalog := canaryCatalog(10)
	next := canaryCatalog(10)
	next.Profiles["support-v4"] = json.RawMessage(`{"version": {"id": "support-v5"}}`)
	moved := 0
	for i := range 1000 {
		caller := fmt.Sprintf("caller-%d", i)
		req := config.Request{Profile: "support"}
		if routeCanary(catalog, req, caller).Candidate != routeCanary(next, req, caller).Candidate {
			moved++
		}
	}
	if moved == 0 {
		t.Error("every caller kept its arm across candidates, so the same callers carry every rollout")
	}
}

func TestARequestWithoutACanaryIsNotRouted(t *testing.T) {
	t.Parallel()
	if routeCanary(canaryCatalog(100), config.Request{Profile: "support-v4"}, "caller").Candidate {
		t.Error("a profile without a canary was routed to a candidate")
	}
}
