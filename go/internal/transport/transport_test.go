package transport_test

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/transport"
)

const (
	apiKey    = "devkey"
	apiSecret = "secret-long-enough-for-a-signing-key"
	room      = "s_7f3a9c21"
	identity  = "p_4b81e0d7"
)

func livekit(t *testing.T) transport.Transport {
	t.Helper()
	lk, err := transport.NewLiveKit("ws://127.0.0.1:7880", apiKey, apiSecret)
	if err != nil {
		t.Fatalf("construct adapter: %v", err)
	}
	return lk
}

func verified(t *testing.T, raw string) (header, payload map[string]any) {
	t.Helper()
	parts := strings.Split(raw, ".")
	if len(parts) != 3 {
		t.Fatalf("token has %d segments, want 3", len(parts))
	}
	mac := hmac.New(sha256.New, []byte(apiSecret))
	mac.Write([]byte(parts[0] + "." + parts[1]))
	want := base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
	if !hmac.Equal([]byte(parts[2]), []byte(want)) {
		t.Fatal("the token is not signed with the api secret")
	}
	return segment(t, parts[0]), segment(t, parts[1])
}

func segment(t *testing.T, s string) map[string]any {
	t.Helper()
	raw, err := base64.RawURLEncoding.DecodeString(s)
	if err != nil {
		t.Fatalf("decode segment: %v", err)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatalf("decode segment json: %v", err)
	}
	return out
}

func mint(t *testing.T, role config.Role) (map[string]any, transport.Token) {
	t.Helper()
	tok, err := livekit(t).MintToken(transport.Grant{Room: room, Identity: identity, Role: role})
	if err != nil {
		t.Fatalf("mint %s token: %v", role, err)
	}
	header, payload := verified(t, tok.JWT)
	if header["alg"] != "HS256" || header["typ"] != "JWT" {
		t.Errorf("header is %v", header)
	}
	return payload, tok
}

func video(t *testing.T, role config.Role) map[string]any {
	t.Helper()
	payload, _ := mint(t, role)
	grant, ok := payload["video"].(map[string]any)
	if !ok {
		t.Fatalf("%s token carries no video grant: %v", role, payload)
	}
	return grant
}

func TestAMintedTokenNamesItsIssuerRoomAndIdentity(t *testing.T) {
	t.Parallel()
	payload, tok := mint(t, config.RoleParticipant)

	if payload["iss"] != apiKey {
		t.Errorf("issuer is %v, want %q", payload["iss"], apiKey)
	}
	if payload["sub"] != identity || payload["identity"] != identity {
		t.Errorf("subject/identity are %v/%v, want %q", payload["sub"], payload["identity"], identity)
	}
	if _, ok := payload["name"]; ok {
		t.Error("the token carries a display name; a real name would reach the media server's dashboards")
	}
	grant := payload["video"].(map[string]any)
	if grant["room"] != room || grant["roomJoin"] != true {
		t.Errorf("token does not grant a join to %q: %v", room, grant)
	}
	if tok.URL != "ws://127.0.0.1:7880" {
		t.Errorf("token carries url %q", tok.URL)
	}
	if tok.ExpiresAt.Before(time.Now()) {
		t.Errorf("token expired at mint time: %s", tok.ExpiresAt)
	}
}

func TestATokenIsValidFromIssueUntilItsStatedExpiry(t *testing.T) {
	t.Parallel()
	payload, tok := mint(t, config.RoleParticipant)

	iat, nbf, exp := payload["iat"].(float64), payload["nbf"].(float64), payload["exp"].(float64)
	if nbf != iat {
		t.Errorf("token is not valid from the moment it was issued: nbf %v, iat %v", nbf, iat)
	}
	if exp-iat != transport.DefaultTTL.Seconds() {
		t.Errorf("token lives %vs, want %vs", exp-iat, transport.DefaultTTL.Seconds())
	}
	if got := int64(exp); got != tok.ExpiresAt.Unix() {
		t.Errorf("the claim expires at %d, the caller was told %d", got, tok.ExpiresAt.Unix())
	}
}

func TestGrantsDeriveFromTheRole(t *testing.T) {
	t.Parallel()
	cases := []struct {
		role      config.Role
		publish   bool
		subscribe bool
		hidden    bool
		recorder  bool
		agent     bool
	}{
		{config.RoleParticipant, true, true, false, false, false},
		{config.RolePresenter, true, true, false, false, false},
		{config.RoleObserver, false, true, false, false, false},
		{config.RoleAgent, true, true, false, false, true},
		{config.RoleRecorder, false, true, true, true, false},
	}
	for _, tc := range cases {
		t.Run(string(tc.role), func(t *testing.T) {
			t.Parallel()
			grant := video(t, tc.role)
			if grant["canPublish"] != tc.publish {
				t.Errorf("canPublish = %v, want %v", grant["canPublish"], tc.publish)
			}
			if grant["canSubscribe"] != tc.subscribe {
				t.Errorf("canSubscribe = %v, want %v", grant["canSubscribe"], tc.subscribe)
			}
			if grant["canPublishData"] != tc.publish {
				t.Errorf("canPublishData = %v, want %v", grant["canPublishData"], tc.publish)
			}
			if truth(grant["hidden"]) != tc.hidden || truth(grant["recorder"]) != tc.recorder ||
				truth(grant["agent"]) != tc.agent {
				t.Errorf("hidden/recorder/agent = %v/%v/%v, want %v/%v/%v",
					grant["hidden"], grant["recorder"], grant["agent"], tc.hidden, tc.recorder, tc.agent)
			}
		})
	}
}

func truth(v any) bool {
	b, ok := v.(bool)
	return ok && b
}

func TestPermissionsAreAlwaysStatedRatherThanLeftToTheServerDefault(t *testing.T) {
	t.Parallel()
	for _, role := range config.AllRoles {
		grant := video(t, role)
		for _, key := range []string{"canPublish", "canSubscribe", "canPublishData"} {
			if _, ok := grant[key]; !ok {
				t.Errorf("%s leaves %s unset; an absent permission is granted by default", role, key)
			}
		}
	}
}

func TestNoRoleEverReceivesAnOperatorGrant(t *testing.T) {
	t.Parallel()
	for _, role := range config.AllRoles {
		grant := video(t, role)
		for _, key := range []string{"roomAdmin", "roomCreate", "roomList", "roomRecord", "ingressAdmin"} {
			if _, ok := grant[key]; ok {
				t.Errorf("%s holds the operator grant %s: %v", role, key, grant)
			}
		}
	}
}

func TestAnUnknownRoleMintsNothing(t *testing.T) {
	t.Parallel()
	_, err := livekit(t).MintToken(transport.Grant{Room: room, Identity: identity, Role: "superuser"})
	var de *errs.Error
	if !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
		t.Fatalf("want %s, got %v", errs.CodeInvalidConfig, err)
	}
}

func TestATokenCannotOutliveRevocation(t *testing.T) {
	t.Parallel()
	_, err := livekit(t).MintToken(transport.Grant{
		Room: room, Identity: identity, Role: config.RoleParticipant, TTL: 48 * time.Hour,
	})
	if err == nil {
		t.Fatal("a two-day join token was minted; it cannot be taken back once issued")
	}
}

func TestATokenIsRefusedWithoutARoomOrIdentity(t *testing.T) {
	t.Parallel()
	if _, err := livekit(t).MintToken(transport.Grant{Identity: identity, Role: config.RoleParticipant}); err == nil {
		t.Error("minted a token with no room")
	}
	if _, err := livekit(t).MintToken(transport.Grant{Room: room, Role: config.RoleParticipant}); err == nil {
		t.Error("minted a token with no identity")
	}
}

func TestTheAdapterRefusesAnUnusableConfiguration(t *testing.T) {
	t.Parallel()
	cases := map[string][3]string{
		"no scheme":      {"127.0.0.1:7880", apiKey, apiSecret},
		"http scheme":    {"http://127.0.0.1:7880", apiKey, apiSecret},
		"no credentials": {"ws://127.0.0.1:7880", "", ""},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			if _, err := transport.NewLiveKit(tc[0], tc[1], tc[2]); err == nil {
				t.Error("accepted a configuration that cannot mint a usable token")
			}
		})
	}
}

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

func TestADispatchSendsThePinnedRequestUnderARoomScopedAdminToken(t *testing.T) {
	t.Parallel()
	const reply = `{"id":"AD_abc123","agent_name":"dafter-py","room":"s_7f3a9c21","metadata":"{}"}`
	srv, calls := egressServer(t, reply, http.StatusOK)
	doc := []byte(`{"apiVersion":"dafter.dev/v1","sessionId":"s_7f3a9c21"}`)
	info, err := recorder(t, srv).DispatchAgent(t.Context(), transport.AgentDispatch{Room: room, Pool: "dafter-py", Metadata: doc})
	if err != nil {
		t.Fatalf("dispatch: %v", err)
	}
	if info.DispatchID != "AD_abc123" || info.Pool != "dafter-py" || info.Room != room {
		t.Errorf("info = %+v", info)
	}
	if len(*calls) != 1 || (*calls)[0].method != "AgentDispatchService/CreateDispatch" {
		t.Fatalf("calls were %+v", *calls)
	}
	raw, err := os.ReadFile(filepath.Join("testdata", "dispatch", "create-dispatch.json"))
	if err != nil {
		t.Fatal(err)
	}
	if got, want := compact(t, (*calls)[0].body), compact(t, raw); got != want {
		t.Errorf("request differs from the pinned fixture\n got: %s\nwant: %s", got, want)
	}
	_, payload := verified(t, (*calls)[0].token)
	grant, _ := payload["video"].(map[string]any)
	if grant["roomAdmin"] != true || grant["room"] != room || len(grant) != 2 {
		t.Errorf("grant = %v, want roomAdmin on %s and nothing else", grant, room)
	}
}

func TestADispatchWithoutADocumentNeverReachesTheServer(t *testing.T) {
	t.Parallel()
	srv, calls := egressServer(t, "{}", http.StatusOK)
	lk := recorder(t, srv)
	for _, d := range []transport.AgentDispatch{
		{Room: room, Pool: "dafter-py"},
		{Room: room, Pool: "dafter-py", Metadata: []byte("not json")},
		{Pool: "dafter-py", Metadata: []byte("{}")},
		{Room: room, Metadata: []byte("{}")},
	} {
		var de *errs.Error
		if _, err := lk.DispatchAgent(t.Context(), d); !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
			t.Errorf("%+v was not refused: %v", d, err)
		}
	}
	if len(*calls) != 0 {
		t.Errorf("%d refused dispatches reached the server", len(*calls))
	}
}

func dispatchServer(t *testing.T, replies map[string]string) (*httptest.Server, *[]twirpCall) {
	t.Helper()
	calls := &[]twirpCall{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Errorf("read request: %v", err)
		}
		method := strings.TrimPrefix(r.URL.Path, "/twirp/livekit.")
		*calls = append(*calls, twirpCall{
			method: method,
			token:  strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer "),
			body:   body,
		})
		reply, ok := replies[method]
		if !ok {
			t.Errorf("unexpected call to %s", method)
			w.WriteHeader(http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(reply))
	}))
	t.Cleanup(srv.Close)
	return srv, calls
}

func grantOf(t *testing.T, c twirpCall) map[string]any {
	t.Helper()
	_, payload := verified(t, c.token)
	grant, _ := payload["video"].(map[string]any)
	return grant
}

const openRoom = `{"rooms":[{"sid":"RM_x","name":"s_7f3a9c21"}]}`

func TestARecallDeletesOnlyThePoolsDispatchesUnderNarrowServiceTokens(t *testing.T) {
	t.Parallel()
	srv, calls := dispatchServer(t, map[string]string{
		"RoomService/ListRooms":               openRoom,
		"AgentDispatchService/ListDispatch":   `{"agent_dispatches":[{"id":"AD_default","agent_name":"","room":"s_7f3a9c21"},{"id":"AD_abc123","agent_name":"dafter-py","room":"s_7f3a9c21"}]}`,
		"AgentDispatchService/DeleteDispatch": `{"id":"AD_abc123","agent_name":"dafter-py","room":"s_7f3a9c21"}`,
	})
	recalled, err := recorder(t, srv).RecallAgents(t.Context(), room, "dafter-py")
	if err != nil {
		t.Fatalf("recall: %v", err)
	}
	if len(recalled) != 1 || recalled[0].DispatchID != "AD_abc123" || recalled[0].Pool != "dafter-py" {
		t.Errorf("recalled %+v; the room's default dispatch belongs to no pool of ours", recalled)
	}
	want := []struct{ method, fixture string }{
		{"RoomService/ListRooms", "list-rooms.json"},
		{"AgentDispatchService/ListDispatch", "list-dispatch.json"},
		{"AgentDispatchService/DeleteDispatch", "delete-dispatch.json"},
	}
	if len(*calls) != len(want) {
		t.Fatalf("calls were %+v", *calls)
	}
	for i, w := range want {
		c := (*calls)[i]
		if c.method != w.method {
			t.Errorf("call %d went to %s, want %s", i, c.method, w.method)
		}
		raw, err := os.ReadFile(filepath.Join("testdata", "dispatch", w.fixture))
		if err != nil {
			t.Fatal(err)
		}
		if got, want := compact(t, c.body), compact(t, raw); got != want {
			t.Errorf("%s differs from the pinned fixture\n got: %s\nwant: %s", w.method, got, want)
		}
	}
	if g := grantOf(t, (*calls)[0]); g["roomList"] != true || len(g) != 1 {
		t.Errorf("ListRooms grant = %v, want roomList and nothing else", g)
	}
	for _, c := range (*calls)[1:] {
		if g := grantOf(t, c); g["roomAdmin"] != true || g["room"] != room || len(g) != 2 {
			t.Errorf("%s grant = %v, want roomAdmin on %s and nothing else", c.method, g, room)
		}
	}
}

func TestARecallOfAClosedRoomTouchesNoDispatch(t *testing.T) {
	t.Parallel()
	srv, calls := dispatchServer(t, map[string]string{"RoomService/ListRooms": `{"rooms":[]}`})
	lk := recorder(t, srv)
	recalled, err := lk.RecallAgents(t.Context(), room, "dafter-py")
	if err != nil || len(recalled) != 0 || len(*calls) != 1 {
		t.Errorf("recalled %+v err %v after %d calls", recalled, err, len(*calls))
	}
	var de *errs.Error
	for _, args := range [][2]string{{"", "dafter-py"}, {room, ""}} {
		if _, err := lk.RecallAgents(t.Context(), args[0], args[1]); !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
			t.Errorf("recall %v was not refused: %v", args, err)
		}
	}
	if len(*calls) != 1 {
		t.Error("a refused recall reached the server")
	}
}
