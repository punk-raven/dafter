package transport_test

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
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

// Decoded and verified with the standard library rather than with a JWT
// package, so the test cannot agree with the minter by sharing its bugs.
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
