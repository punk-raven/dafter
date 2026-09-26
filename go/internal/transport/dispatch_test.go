package transport_test

import (
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/transport"
)

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
