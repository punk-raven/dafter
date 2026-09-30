package control_test

import (
	"context"

	"github.com/punk-raven/dafter/go/internal/transport"
)

func (s *stubTransport) PlaceCall(_ context.Context, c transport.PhoneCall) (transport.CallInfo, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.callErr != nil {
		return transport.CallInfo{}, s.callErr
	}
	s.calls = append(s.calls, c)
	return transport.CallInfo{ParticipantID: "PA_stub", Identity: c.Identity, Room: c.Room, CallID: "SCL_stub"}, nil
}
