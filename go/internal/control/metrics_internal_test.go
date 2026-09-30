package control

import "testing"

func TestCarrierAndCallPathsCarryNoTrunkTokenOrParticipantIntoALabel(t *testing.T) {
	t.Parallel()
	for path, want := range map[string]string{
		"/telephony/vobiz/answer":                                "/telephony/{trunk}/answer",
		"/telephony/vobiz/held/0f1e2d3c4b5a69788796a5b4c3d2e1f0": "/telephony/{trunk}/held",
		"/telephony/anything-at-all/bridge":                      "/telephony/{trunk}/bridge",
		"/telephony/answer":                                      "/telephony",
		"/telephony/vobiz/../../etc":                             "/telephony",
		"/sessions/s_7f3a9c21/call/start":                        "/sessions/{id}/call/start",
		"/sessions/s_7f3a9c21/call/p_4b81e0d7/stop":              "/sessions/{id}/call/{participant}/stop",
	} {
		if got := normalizePath(path); got != want {
			t.Errorf("%s labelled %s, want %s", path, got, want)
		}
	}
}
