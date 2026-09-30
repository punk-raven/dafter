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

const (
	listedRoomPeople = `{"participants":[` +
		`{"identity":"p_4b81e0d7","kind":"SIP","tracks":[{"sid":"TR_phone","type":"AUDIO"}]},` +
		`{"identity":"p_9c2e11aa","kind":0,"tracks":[]},` +
		`{"identity":"agent-AJ_x","kind":"AGENT","tracks":[]}]}`
	browserIdentity = "p_9c2e11aa"
)

func hangUpServer(t *testing.T, replies map[string]string) (*httptest.Server, *[]twirpCall) {
	t.Helper()
	calls := &[]twirpCall{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Errorf("read request: %v", err)
		}
		method := strings.TrimPrefix(r.URL.Path, "/twirp/livekit.")
		*calls = append(*calls, twirpCall{method: method, token: strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer "), body: body})
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

func pinned(t *testing.T, dir, name string) []byte {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("testdata", dir, name))
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func TestAHangUpRemovesThePhoneUnderARoomScopedAdminToken(t *testing.T) {
	t.Parallel()
	srv, calls := hangUpServer(t, map[string]string{
		"RoomService/ListRooms":         openRoom,
		"RoomService/ListParticipants":  listedRoomPeople,
		"RoomService/RemoveParticipant": `{}`,
	})
	if err := recorder(t, srv).HangUp(t.Context(), room, phoneIdentity); err != nil {
		t.Fatalf("hang up: %v", err)
	}
	want := []struct{ method, dir, fixture string }{
		{"RoomService/ListRooms", "dispatch", "list-rooms.json"},
		{"RoomService/ListParticipants", "egress", "list-participants.json"},
		{"RoomService/RemoveParticipant", "sip", "remove-participant.json"},
	}
	if len(*calls) != len(want) {
		t.Fatalf("calls were %+v", *calls)
	}
	for i, w := range want {
		c := (*calls)[i]
		if c.method != w.method {
			t.Errorf("call %d went to %s, want %s", i, c.method, w.method)
		}
		if got, want := compact(t, c.body), compact(t, pinned(t, w.dir, w.fixture)); got != want {
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
		if _, payload := verified(t, c.token); payload["sip"] != nil {
			t.Errorf("%s carries a SIP grant; hanging up needs none", c.method)
		}
	}
}

func TestOnlyAPhoneInTheRoomIsHungUp(t *testing.T) {
	t.Parallel()
	for _, tc := range []struct {
		name, identity string
		rooms          string
		want           error
	}{
		{"a person in the browser", browserIdentity, openRoom, transport.ErrNotAPhone},
		{"the agent", "agent-AJ_x", openRoom, transport.ErrNotAPhone},
		{"someone not in the room", "p_00000000", openRoom, transport.ErrNoSuchParticipant},
		{"a room that has closed", phoneIdentity, `{"rooms":[]}`, transport.ErrNoSuchParticipant},
	} {
		srv, calls := hangUpServer(t, map[string]string{
			"RoomService/ListRooms":        tc.rooms,
			"RoomService/ListParticipants": listedRoomPeople,
		})
		if err := recorder(t, srv).HangUp(t.Context(), room, tc.identity); !errors.Is(err, tc.want) {
			t.Errorf("%s: %v, want %v", tc.name, err, tc.want)
		}
		for _, c := range *calls {
			if c.method == "RoomService/RemoveParticipant" {
				t.Errorf("%s was removed", tc.name)
			}
		}
	}
}

func TestAHangUpNamingNoRoomOrNobodyNeverReachesTheServer(t *testing.T) {
	t.Parallel()
	srv, calls := hangUpServer(t, map[string]string{})
	lk := recorder(t, srv)
	var de *errs.Error
	for _, args := range [][2]string{{"", phoneIdentity}, {room, ""}} {
		if err := lk.HangUp(t.Context(), args[0], args[1]); !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
			t.Errorf("hang up %v was not refused: %v", args, err)
		}
	}
	if len(*calls) != 0 {
		t.Errorf("%d refused hang-ups reached the server", len(*calls))
	}
}
