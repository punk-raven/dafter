package transport_test

import (
	"errors"
	"net/http"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/transport"
)

const participantsReply = `{"participants":[
 {"sid":"PA_a","identity":"p_4b81e0d7","state":"ACTIVE","kind":"STANDARD","tracks":[
  {"sid":"TR_VCdef456","type":"VIDEO","source":"CAMERA"},
  {"sid":"TR_AMabc123","type":"AUDIO","source":"MICROPHONE"}]},
 {"sid":"PA_b","identity":"agent-AJ_x","state":"ACTIVE","kind":"AGENT","tracks":[
  {"sid":"TR_AMagent1","type":"AUDIO","source":"MICROPHONE"}]},
 {"sid":"PA_c","identity":"p_9d02c3aa","tracks":[{"sid":"TR_AMpeer1"}]},
 {"sid":"PA_d","identity":"p_1a2b3c4d","kind":4,"tracks":[{"sid":"TR_VCnum1","type":1}]}
]}`

func TestAPublisherIsFoundUnderARoomScopedAdminToken(t *testing.T) {
	t.Parallel()
	srv, calls := dispatchServer(t, map[string]string{"RoomService/ListParticipants": participantsReply})
	lk := recorder(t, srv)

	cases := map[string]transport.TrackPublisher{
		audioTrackID:  {Identity: identity, Audio: true},
		videoTrackID:  {Identity: identity},
		"TR_AMagent1": {Identity: "agent-AJ_x", Agent: true, Audio: true},
		"TR_AMpeer1":  {Identity: "p_9d02c3aa", Audio: true},
		"TR_VCnum1":   {Identity: "p_1a2b3c4d", Agent: true},
	}
	for track, want := range cases {
		got, err := lk.TrackOwner(t.Context(), room, track)
		if err != nil {
			t.Fatalf("%s: %v", track, err)
		}
		if got != want {
			t.Errorf("%s: %+v, want %+v", track, got, want)
		}
	}
	for _, c := range *calls {
		if c.method != "RoomService/ListParticipants" {
			t.Errorf("called %s", c.method)
		}
		if got, want := compact(t, c.body), compact(t, fixture(t, "list-participants.json")); got != want {
			t.Errorf("request\n %s\nwant\n %s", got, want)
		}
		grant := grantOf(t, c)
		if grant["roomAdmin"] != true || grant["room"] != room || len(grant) != 2 {
			t.Errorf("grant %v, want roomAdmin on %s and nothing else", grant, room)
		}
	}
}

func TestATrackNobodyPublishesHasNoOwner(t *testing.T) {
	t.Parallel()
	srv, _ := dispatchServer(t, map[string]string{"RoomService/ListParticipants": participantsReply})
	_, err := recorder(t, srv).TrackOwner(t.Context(), room, "TR_AMgone")
	var de *errs.Error
	if !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
		t.Fatalf("want %s, got %v", errs.CodeInvalidConfig, err)
	}
}

const completeReply = `{"items":[{"egress_id":"EG_abc123","room_name":"s_7f3a9c21","status":"EGRESS_COMPLETE",
 "track":{"room_name":"s_7f3a9c21","track_id":"TR_AMabc123"},
 "file_results":[{"filename":"s_7f3a9c21/track-TR_AMabc123-20260924100001123.ogg","size":"48213",
  "location":"http://127.0.0.1:9000/dafter-recordings/s_7f3a9c21/track-TR_AMabc123-20260924100001123.ogg"}]}],
 "next_page_token":null}`

func TestACompleteRecordingIsHandedOutAsAPresignedURL(t *testing.T) {
	t.Parallel()
	srv, calls := dispatchServer(t, map[string]string{"Egress/ListEgress": completeReply})
	got, err := recorder(t, srv).RecordingFile(t.Context(), egressID, 10*time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	const key = "s_7f3a9c21/track-TR_AMabc123-20260924100001123.ogg"
	if !got.Complete || got.Key != key || got.Status != "EGRESS_COMPLETE" {
		t.Fatalf("%+v", got)
	}
	u, err := url.Parse(got.URL)
	if err != nil {
		t.Fatal(err)
	}
	at, err := time.Parse("20060102T150405Z", u.Query().Get("X-Amz-Date"))
	if err != nil {
		t.Fatal(err)
	}
	want, err := transport.PresignGet(devStorage, key, at, 10*time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	if got.URL != want {
		t.Errorf("url\n %s\nwant\n %s", got.URL, want)
	}
	if !strings.HasPrefix(got.URL, "http://127.0.0.1:9000/dafter-recordings/"+key+"?") {
		t.Errorf("url %s is not the object in the recordings bucket", got.URL)
	}
	if d := got.ExpiresAt.Sub(at); d != 10*time.Minute {
		t.Errorf("expires %s after signing", d)
	}
	if len(*calls) != 1 {
		t.Fatalf("%d calls", len(*calls))
	}
	c := (*calls)[0]
	if got, want := compact(t, c.body), compact(t, fixture(t, "list-egress.json")); got != want {
		t.Errorf("request\n %s\nwant\n %s", got, want)
	}
	if grant := grantOf(t, c); grant["roomRecord"] != true || len(grant) != 1 {
		t.Errorf("grant %v, want roomRecord and nothing else", grant)
	}
}

func TestARecordingStillWritingHasNoURL(t *testing.T) {
	t.Parallel()
	replies := map[string]string{
		"EGRESS_ACTIVE":   `{"items":[{"egress_id":"EG_abc123","status":"EGRESS_ACTIVE","file_results":[]}]}`,
		"EGRESS_ENDING":   `{"items":[{"egress_id":"EG_abc123","status":"EGRESS_ENDING","ended_at":"0","file_results":[]}]}`,
		"EGRESS_STARTING": `{"items":[{"egress_id":"EG_abc123"}]}`,
		"a status number": `{"items":[{"egress_id":"EG_abc123","status":1}]}`,
	}
	for name, reply := range replies {
		srv, _ := dispatchServer(t, map[string]string{"Egress/ListEgress": reply})
		got, err := recorder(t, srv).RecordingFile(t.Context(), egressID, time.Minute)
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		if got.Ended || got.Complete || got.URL != "" || !got.EndedAt.IsZero() || got.Status == "" {
			t.Errorf("%s: %+v", name, got)
		}
	}
}

func TestAnEndedRecordingSaysHowItEndedAndWhetherItLeftAFile(t *testing.T) {
	t.Parallel()
	ended := time.Unix(0, 1758535650000000000).UTC()
	cases := []struct {
		name, reply, status string
		file                bool
	}{
		{"cut at its limit", string(fixture(t, "list-egress-limit-reached.json")), "EGRESS_LIMIT_REACHED", true},
		{"failed", string(fixture(t, "list-egress-failed.json")), "EGRESS_FAILED", false},
		{"aborted", `{"items":[{"egress_id":"EG_abc123","status":5,"ended_at":"1758535650000000000"}]}`, "EGRESS_ABORTED", false},
		{"cut with nothing written", `{"items":[{"egress_id":"EG_abc123","status":"EGRESS_LIMIT_REACHED","ended_at":"1758535650000000000","file_results":[]}]}`, "EGRESS_LIMIT_REACHED", false},
	}
	for _, tc := range cases {
		srv, _ := dispatchServer(t, map[string]string{"Egress/ListEgress": tc.reply})
		got, err := recorder(t, srv).RecordingFile(t.Context(), egressID, time.Minute)
		if err != nil {
			t.Fatalf("%s: %v", tc.name, err)
		}
		if !got.Ended || got.Status != tc.status || !got.EndedAt.Equal(ended) || got.Complete != tc.file || (got.URL != "") != tc.file {
			t.Errorf("%s: %+v", tc.name, got)
		}
	}
}

func TestAnEgressThatEndedRefusesAStop(t *testing.T) {
	t.Parallel()
	srv, calls := egressServer(t, string(fixture(t, "stop-ended.json")), http.StatusPreconditionFailed)
	_, err := recorder(t, srv).StopEgress(t.Context(), egressID)
	var de *errs.Error
	if !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig || !strings.Contains(err.Error(), "failed_precondition") {
		t.Fatalf("want %s from failed_precondition, got %v", errs.CodeInvalidConfig, err)
	}
	if len(*calls) != 1 || compact(t, (*calls)[0].body) != compact(t, fixture(t, "stop.json")) {
		t.Errorf("calls %+v", *calls)
	}
}

func TestARecordingIsLocatedOnlyWhenTheServerKnowsIt(t *testing.T) {
	t.Parallel()
	srv, _ := dispatchServer(t, map[string]string{"Egress/ListEgress": `{"items":[]}`})
	_, err := recorder(t, srv).RecordingFile(t.Context(), egressID, time.Minute)
	var de *errs.Error
	if !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig || !errors.Is(err, transport.ErrUnknownRecording) {
		t.Fatalf("want %s naming an unknown recording, got %v", errs.CodeInvalidConfig, err)
	}

	bare, calls := dispatchServer(t, map[string]string{})
	wsURL := "ws" + strings.TrimPrefix(bare.URL, "http")
	lk, err := transport.NewLiveKit(wsURL, apiKey, apiSecret)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := lk.RecordingFile(t.Context(), egressID, time.Minute); !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
		t.Fatalf("without storage: want %s, got %v", errs.CodeInvalidConfig, err)
	}
	if len(*calls) != 0 {
		t.Error("a recording was looked up with nowhere to read it from")
	}
}
