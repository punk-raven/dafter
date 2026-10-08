package transport_test

import (
	"testing"
)

func TestOpenRoomsAsksOnceUnderAListOnlyTokenAndNamesOnlyTheOpenOnes(t *testing.T) {
	t.Parallel()
	srv, calls := dispatchServer(t, map[string]string{"RoomService/ListRooms": openRoom})
	lk := recorder(t, srv)
	open, err := lk.OpenRooms(t.Context(), []string{room, "s_00000000"})
	if err != nil || !open[room] || open["s_00000000"] || len(open) != 1 {
		t.Fatalf("open %v err %v", open, err)
	}
	if len(*calls) != 1 {
		t.Fatalf("calls %+v; every room is asked about in one call", *calls)
	}
	if got := compact(t, (*calls)[0].body); got != `{"names":["`+room+`","s_00000000"]}` {
		t.Errorf("ListRooms request %s", got)
	}
	if g := grantOf(t, (*calls)[0]); g["roomList"] != true || len(g) != 1 {
		t.Errorf("ListRooms grant = %v, want roomList and nothing else", g)
	}
	if none, err := lk.OpenRooms(t.Context(), nil); err != nil || len(none) != 0 || len(*calls) != 1 {
		t.Errorf("no rooms asked the server: %v %v", none, err)
	}
}
