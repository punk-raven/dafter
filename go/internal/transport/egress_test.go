package transport_test

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/transport"
)

const (
	sessionID    = "s_7f3a9c21"
	audioTrackID = "TR_AMabc123"
	videoTrackID = "TR_VCdef456"
	egressID     = "EG_abc123"
)

var devStorage = transport.EgressStorage{
	Bucket: "dafter-recordings", Endpoint: "http://127.0.0.1:9000", Region: "us-east-1",
	AccessKey: "minioadmin", Secret: "minioadmin", ForcePathStyle: true,
}

func compositeEncoding() *config.EgressProfile {
	return &config.EgressProfile{
		Width: 1280, Height: 720, Framerate: 30, VideoBitrate: 3000, AudioBitrate: 128,
		VideoCodec: config.EgressCodecH264Main,
	}
}

type twirpCall struct {
	method string
	token  string
	body   []byte
}

func egressServer(t *testing.T, reply string, status int) (*httptest.Server, *[]twirpCall) {
	t.Helper()
	calls := &[]twirpCall{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Errorf("read request: %v", err)
		}
		if r.Method != http.MethodPost || !strings.HasPrefix(r.URL.Path, "/twirp/livekit.") {
			t.Errorf("%s %s is not a twirp call", r.Method, r.URL.Path)
		}
		if ct := r.Header.Get("Content-Type"); ct != "application/json" {
			t.Errorf("content type %q", ct)
		}
		*calls = append(*calls, twirpCall{
			method: strings.TrimPrefix(r.URL.Path, "/twirp/livekit."),
			token:  strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer "),
			body:   body,
		})
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/twirp/livekit.RoomService/CreateRoom" {
			_, _ = w.Write([]byte(`{"sid":"RM_x","name":"s_7f3a9c21","empty_timeout":300}`))
			return
		}
		w.WriteHeader(status)
		_, _ = w.Write([]byte(reply))
	}))
	t.Cleanup(srv.Close)
	return srv, calls
}

const startedReply = `{"egress_id":"EG_abc123","room_id":"RM_x","room_name":"s_7f3a9c21","source_type":"EGRESS_SOURCE_TYPE_WEB","status":"EGRESS_STARTING","started_at":"1758535200000000000","ended_at":"0","updated_at":"0","room_composite":{"room_name":"s_7f3a9c21"},"error":"","error_code":0}`

func recorder(t *testing.T, srv *httptest.Server, opts ...transport.Option) transport.Transport {
	t.Helper()
	wsURL := "ws" + strings.TrimPrefix(srv.URL, "http")
	opts = append([]transport.Option{transport.WithEgressStorage(devStorage)}, opts...)
	lk, err := transport.NewLiveKit(wsURL, apiKey, apiSecret, opts...)
	if err != nil {
		t.Fatalf("construct adapter: %v", err)
	}
	return lk
}

func fixture(t *testing.T, name string) []byte {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("testdata", "egress", name))
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func compact(t *testing.T, raw []byte) string {
	t.Helper()
	var buf bytes.Buffer
	if err := json.Compact(&buf, raw); err != nil {
		t.Fatalf("not json: %v\n%s", err, raw)
	}
	return buf.String()
}

func TestEachLayoutSendsExactlyItsPinnedRequest(t *testing.T) {
	t.Parallel()
	off := &config.EgressProfile{AudioBitrate: 64}
	cases := []struct {
		fixture string
		method  string
		req     transport.EgressRequest
	}{
		{"room-composite.json", "Egress/StartRoomCompositeEgress", transport.EgressRequest{
			Room: room, SessionID: sessionID, Layout: config.LayoutRoomComposite, Encoding: compositeEncoding(),
		}},
		{"room-composite-preset.json", "Egress/StartRoomCompositeEgress", transport.EgressRequest{
			Room: room, SessionID: sessionID, Layout: config.LayoutRoomComposite,
			Encoding: &config.EgressProfile{Preset: config.PresetH2641080p30},
		}},
		{"room-composite-audio-only.json", "Egress/StartRoomCompositeEgress", transport.EgressRequest{
			Room: room, SessionID: sessionID, Layout: config.LayoutRoomComposite, AudioOnly: true, Encoding: off,
		}},
		{"track-composite.json", "Egress/StartTrackCompositeEgress", transport.EgressRequest{
			Room: room, SessionID: sessionID, Layout: config.LayoutTrackComposite, Encoding: compositeEncoding(),
			AudioTrackID: audioTrackID, VideoTrackID: videoTrackID,
		}},
		{"track.json", "Egress/StartTrackEgress", transport.EgressRequest{
			Room: room, SessionID: sessionID, Layout: config.LayoutTrack, Encoding: compositeEncoding(),
			TrackID: audioTrackID,
		}},
	}
	for _, tc := range cases {
		t.Run(tc.fixture, func(t *testing.T) {
			t.Parallel()
			srv, calls := egressServer(t, startedReply, http.StatusOK)
			info, err := recorder(t, srv).StartEgress(t.Context(), tc.req)
			if err != nil {
				t.Fatalf("start egress: %v", err)
			}
			if len(*calls) != 1 {
				t.Fatalf("%d calls were made, want 1", len(*calls))
			}
			call := (*calls)[0]
			if call.method != tc.method {
				t.Errorf("called %s, want %s", call.method, tc.method)
			}
			if got, want := compact(t, call.body), compact(t, fixture(t, tc.fixture)); got != want {
				t.Errorf("request differs from the pinned fixture\n got: %s\nwant: %s", got, want)
			}
			if info.EgressID != egressID || info.Status != "EGRESS_STARTING" || info.Room != room {
				t.Errorf("info = %+v", info)
			}
			if info.StartedAt.Unix() != 1758535200 {
				t.Errorf("started at %s; the service reports unix nanoseconds as a string", info.StartedAt)
			}
		})
	}
}

func TestTheServiceTokenCarriesRoomRecordAndNothingElse(t *testing.T) {
	t.Parallel()
	srv, calls := egressServer(t, startedReply, http.StatusOK)
	if _, err := recorder(t, srv).StartEgress(t.Context(), transport.EgressRequest{
		Room: room, SessionID: sessionID, Layout: config.LayoutRoomComposite, Encoding: compositeEncoding(),
	}); err != nil {
		t.Fatal(err)
	}
	_, payload := verified(t, (*calls)[0].token)
	grant, ok := payload["video"].(map[string]any)
	if !ok {
		t.Fatalf("service token carries no video grant: %v", payload)
	}
	if grant["roomRecord"] != true || grant["room"] != room {
		t.Errorf("grant = %v, want roomRecord on %s", grant, room)
	}
	for _, key := range []string{"roomJoin", "roomAdmin", "roomCreate", "roomList", "ingressAdmin", "canPublish"} {
		if _, present := grant[key]; present {
			t.Errorf("service token carries %s; it is for the egress API and nothing else", key)
		}
	}
	if payload["iss"] != apiKey {
		t.Errorf("issuer %v", payload["iss"])
	}
	if _, present := payload["sub"]; present {
		t.Error("the service token names a subject; it is not a participant")
	}
	exp, iat := payload["exp"].(float64), payload["iat"].(float64)
	if exp-iat > 60 {
		t.Errorf("service token lives %vs; it is used once, immediately", exp-iat)
	}
}

func TestARoomCompositeStartedBeforeTheFirstJoinCreatesTheRoomFirst(t *testing.T) {
	t.Parallel()
	srv, calls := egressServer(t, startedReply, http.StatusOK)
	lk := recorder(t, srv)
	if _, err := lk.StartEgress(t.Context(), transport.EgressRequest{
		Room: room, SessionID: sessionID, Layout: config.LayoutRoomComposite, Encoding: compositeEncoding(), CreateRoom: true,
	}); err != nil {
		t.Fatalf("start egress: %v", err)
	}
	if len(*calls) != 2 || (*calls)[0].method != "RoomService/CreateRoom" || (*calls)[1].method != "Egress/StartRoomCompositeEgress" {
		t.Fatalf("calls were %+v; the room must exist before the egress attaches", *calls)
	}
	if got, want := compact(t, (*calls)[0].body), compact(t, fixture(t, "create-room.json")); got != want {
		t.Errorf("create room request differs from the pinned fixture\n got: %s\nwant: %s", got, want)
	}

	_, create := verified(t, (*calls)[0].token)
	_, start := verified(t, (*calls)[1].token)
	if g := create["video"].(map[string]any); g["roomCreate"] != true || g["roomRecord"] != nil {
		t.Errorf("create room token grant = %v", g)
	}
	if g := start["video"].(map[string]any); g["roomRecord"] != true || g["roomCreate"] != nil {
		t.Errorf("start egress token grant = %v", g)
	}

	_, err := lk.StartEgress(t.Context(), transport.EgressRequest{
		Room: room, SessionID: sessionID, Layout: config.LayoutTrack, TrackID: audioTrackID, CreateRoom: true,
	})
	var de *errs.Error
	if !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
		t.Errorf("a track egress before the first join was accepted; there is no track to attach to: %v", err)
	}
	if len(*calls) != 2 {
		t.Errorf("%d calls; the refused start reached the server", len(*calls))
	}
}

func TestStopSendsTheEgressID(t *testing.T) {
	t.Parallel()
	const reply = `{"egressId":"EG_abc123","roomName":"s_7f3a9c21","status":"EGRESS_ENDING","startedAt":"1758535200000000000","endedAt":"1758535260000000000"}`
	srv, calls := egressServer(t, reply, http.StatusOK)
	info, err := recorder(t, srv).StopEgress(t.Context(), egressID)
	if err != nil {
		t.Fatalf("stop egress: %v", err)
	}
	if (*calls)[0].method != "Egress/StopEgress" {
		t.Errorf("called %s", (*calls)[0].method)
	}
	if got, want := compact(t, (*calls)[0].body), compact(t, fixture(t, "stop.json")); got != want {
		t.Errorf("request differs from the pinned fixture\n got: %s\nwant: %s", got, want)
	}
	if info.Status != "EGRESS_ENDING" || info.EndedAt.Unix() != 1758535260 {
		t.Errorf("info = %+v", info)
	}
}

func TestATwirpRefusalBecomesAPlatformError(t *testing.T) {
	t.Parallel()
	srv, _ := egressServer(t, `{"code":"not_found","msg":"egress does not exist"}`, http.StatusNotFound)
	_, err := recorder(t, srv).StopEgress(t.Context(), egressID)
	var de *errs.Error
	if !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
		t.Fatalf("want %s, got %v", errs.CodeInvalidConfig, err)
	}
	if len(de.Details) != 1 || de.Details[0] != "egress does not exist" {
		t.Errorf("the service's reason was lost: %v", de)
	}

	srv, _ = egressServer(t, `{"code":"unavailable","msg":"no egress available"}`, http.StatusServiceUnavailable)
	_, err = recorder(t, srv).StartEgress(t.Context(), transport.EgressRequest{
		Room: room, SessionID: sessionID, Layout: config.LayoutRoomComposite,
	})
	if !errors.As(err, &de) || de.Code != errs.CodeProviderUnavailable || !de.Retryable {
		t.Fatalf("an unavailable service should be retryable: %v", err)
	}
}

func TestAnEgressIsRefusedBeforeItReachesTheService(t *testing.T) {
	t.Parallel()
	srv, calls := egressServer(t, startedReply, http.StatusOK)
	lk := recorder(t, srv)
	cases := map[string]transport.EgressRequest{
		"no room":                        {SessionID: sessionID, Layout: config.LayoutRoomComposite},
		"no session":                     {Room: room, Layout: config.LayoutRoomComposite},
		"unknown layout":                 {Room: room, SessionID: sessionID, Layout: "hologram"},
		"track composite with no tracks": {Room: room, SessionID: sessionID, Layout: config.LayoutTrackComposite},
		"track with no track":            {Room: room, SessionID: sessionID, Layout: config.LayoutTrack},
		"preset beside explicit fields": {Room: room, SessionID: sessionID, Layout: config.LayoutRoomComposite,
			Encoding: &config.EgressProfile{Preset: config.PresetH264720p30, Width: 1280}},
	}
	for name, req := range cases {
		t.Run(name, func(t *testing.T) {
			_, err := lk.StartEgress(t.Context(), req)
			var de *errs.Error
			if !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
				t.Errorf("want %s, got %v", errs.CodeInvalidConfig, err)
			}
		})
	}
	if len(*calls) != 0 {
		t.Errorf("%d request(s) reached the service", len(*calls))
	}

	if _, err := lk.StopEgress(t.Context(), ""); err == nil {
		t.Error("a stop with no egress id reached the service")
	}
}

func TestWithoutStorageNoRecordingStarts(t *testing.T) {
	t.Parallel()
	srv, calls := egressServer(t, startedReply, http.StatusOK)
	lk, err := transport.NewLiveKit("ws"+strings.TrimPrefix(srv.URL, "http"), apiKey, apiSecret)
	if err != nil {
		t.Fatal(err)
	}
	_, err = lk.StartEgress(t.Context(), transport.EgressRequest{
		Room: room, SessionID: sessionID, Layout: config.LayoutRoomComposite, Encoding: compositeEncoding(),
	})
	var de *errs.Error
	if !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
		t.Fatalf("want %s, got %v", errs.CodeInvalidConfig, err)
	}
	if len(*calls) != 0 {
		t.Error("a recording with nowhere to land was started")
	}

	bad := map[string]transport.EgressStorage{
		"no bucket":      {AccessKey: "k", Secret: "s"},
		"no credentials": {Bucket: "b"},
		"bad endpoint":   {Bucket: "b", AccessKey: "k", Secret: "s", Endpoint: "127.0.0.1:9000"},
	}
	for name, storage := range bad {
		t.Run(name, func(t *testing.T) {
			if _, err := transport.NewLiveKit("ws://127.0.0.1:7880", apiKey, apiSecret, transport.WithEgressStorage(storage)); err == nil {
				t.Error("accepted storage no recording could land in")
			}
		})
	}
}
