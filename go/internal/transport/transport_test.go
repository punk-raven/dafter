package transport_test

import (
	"errors"
	"testing"
	"time"

	"github.com/livekit/protocol/auth"

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

func mint(t *testing.T, role config.Role) (*auth.ClaimGrants, transport.Token) {
	t.Helper()
	tok, err := livekit(t).MintToken(transport.Grant{Room: room, Identity: identity, Role: role})
	if err != nil {
		t.Fatalf("mint %s token: %v", role, err)
	}
	verifier, err := auth.ParseAPIToken(tok.JWT)
	if err != nil {
		t.Fatalf("parse minted token: %v", err)
	}
	if verifier.APIKey() != apiKey {
		t.Errorf("token names api key %q, want %q", verifier.APIKey(), apiKey)
	}
	_, grants, err := verifier.Verify(apiSecret)
	if err != nil {
		t.Fatalf("the minted token does not verify against its own secret: %v", err)
	}
	return grants, tok
}

func TestAMintedTokenVerifiesAndNamesItsRoomAndIdentity(t *testing.T) {
	t.Parallel()
	grants, tok := mint(t, config.RoleParticipant)

	if grants.Identity != identity {
		t.Errorf("identity is %q, want %q", grants.Identity, identity)
	}
	if grants.Video.Room != room || !grants.Video.RoomJoin {
		t.Errorf("token does not grant a join to %q: %+v", room, grants.Video)
	}
	if tok.URL != "ws://127.0.0.1:7880" {
		t.Errorf("token carries url %q", tok.URL)
	}
	if tok.ExpiresAt.Before(time.Now()) {
		t.Errorf("token expired at mint time: %s", tok.ExpiresAt)
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
			v := mustVideo(t, tc.role)
			if v.GetCanPublish() != tc.publish {
				t.Errorf("canPublish = %v, want %v", v.GetCanPublish(), tc.publish)
			}
			if v.GetCanSubscribe() != tc.subscribe {
				t.Errorf("canSubscribe = %v, want %v", v.GetCanSubscribe(), tc.subscribe)
			}
			if v.Hidden != tc.hidden || v.Recorder != tc.recorder || v.Agent != tc.agent {
				t.Errorf("hidden/recorder/agent = %v/%v/%v, want %v/%v/%v",
					v.Hidden, v.Recorder, v.Agent, tc.hidden, tc.recorder, tc.agent)
			}
		})
	}
}

func TestNoRoleEverReceivesAnOperatorGrant(t *testing.T) {
	t.Parallel()
	for _, role := range config.AllRoles {
		v := mustVideo(t, role)
		if v.RoomAdmin || v.RoomCreate || v.RoomList || v.RoomRecord || v.IngressAdmin {
			t.Errorf("%s holds an operator grant: %+v", role, v)
		}
	}
}

func mustVideo(t *testing.T, role config.Role) *auth.VideoGrant {
	t.Helper()
	grants, _ := mint(t, role)
	if grants.Video == nil {
		t.Fatalf("%s token carries no video grant", role)
	}
	return grants.Video
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
