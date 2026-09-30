package transport

import (
	"context"
	"encoding/json"
	"errors"
	"strconv"
	"time"

	"github.com/punk-raven/dafter/go/internal/errs"
)

const (
	twirpSIPPrefix          = "/twirp/livekit.SIP/"
	methodCreateParticipant = "CreateSIPParticipant"
	phoneParticipantName    = "Phone"
	roleAttribute           = "dafter.role"
)

var sipTransportNames = map[string]string{
	"":    "SIP_TRANSPORT_AUTO",
	"udp": "SIP_TRANSPORT_UDP",
	"tcp": "SIP_TRANSPORT_TCP",
	"tls": "SIP_TRANSPORT_TLS",
}

type PhoneCall struct {
	Room            string
	Identity        string
	To              string
	Trunk           Trunk
	RingingTimeout  time.Duration
	MaxCallDuration time.Duration
}

type CallInfo struct {
	ParticipantID string
	Identity      string
	Room          string
	CallID        string
}

type sipGrant struct {
	Admin bool `json:"admin"`
	Call  bool `json:"call"`
}

type sipOutboundConfig struct {
	Hostname           string `json:"hostname"`
	DestinationCountry string `json:"destinationCountry,omitempty"`
	Transport          string `json:"transport"`
	AuthUsername       string `json:"authUsername,omitempty"`
	AuthPassword       string `json:"authPassword,omitempty"`
}

type createSIPParticipantRequest struct {
	Trunk                 sipOutboundConfig `json:"trunk"`
	SIPCallTo             string            `json:"sipCallTo"`
	SIPNumber             string            `json:"sipNumber"`
	RoomName              string            `json:"roomName"`
	ParticipantIdentity   string            `json:"participantIdentity"`
	ParticipantName       string            `json:"participantName"`
	ParticipantAttributes map[string]string `json:"participantAttributes"`
	HidePhoneNumber       bool              `json:"hidePhoneNumber"`
	RingingTimeout        string            `json:"ringingTimeout"`
	MaxCallDuration       string            `json:"maxCallDuration"`
	WaitUntilAnswered     bool              `json:"waitUntilAnswered"`
}

type sipParticipantJSON struct {
	ParticipantID            string `json:"participant_id"`
	ParticipantIDCamel       string `json:"participantId"`
	ParticipantIdentity      string `json:"participant_identity"`
	ParticipantIdentityCamel string `json:"participantIdentity"`
	RoomName                 string `json:"room_name"`
	RoomNameCamel            string `json:"roomName"`
	SIPCallID                string `json:"sip_call_id"`
	SIPCallIDCamel           string `json:"sipCallId"`
}

func (l *LiveKit) PlaceCall(ctx context.Context, c PhoneCall) (CallInfo, error) {
	if c.Room == "" || c.Identity == "" {
		return CallInfo{}, errs.Errorf(errs.CodeInvalidConfig, "a phone call joins a room under an identity, and this one names none")
	}
	if !PhoneNumber.MatchString(c.To) {
		return CallInfo{}, errs.Errorf(errs.CodeInvalidConfig, "a phone call goes to an E.164 number")
	}
	if c.Trunk.Address == "" || len(c.Trunk.Numbers) == 0 {
		return CallInfo{}, errs.Errorf(errs.CodeInvalidConfig, "a phone call goes out on a trunk with an address and a number to call from")
	}
	if c.RingingTimeout <= 0 || c.MaxCallDuration <= 0 {
		return CallInfo{}, errs.Errorf(errs.CodeInvalidConfig, "a phone call states how long it rings and how long it may last")
	}
	token, err := l.signService(serviceGrant{}, &sipGrant{Call: true})
	if err != nil {
		return CallInfo{}, err
	}
	raw, err := l.call(ctx, twirpSIPPrefix+methodCreateParticipant, token, createSIPParticipantRequest{
		Trunk: sipOutboundConfig{
			Hostname:           c.Trunk.Address,
			DestinationCountry: c.Trunk.DestinationCountry,
			Transport:          sipTransportNames[c.Trunk.Transport],
			AuthUsername:       c.Trunk.AuthUsername,
			AuthPassword:       c.Trunk.AuthPassword,
		},
		SIPCallTo:             c.To,
		SIPNumber:             c.Trunk.Numbers[0],
		RoomName:              c.Room,
		ParticipantIdentity:   c.Identity,
		ParticipantName:       phoneParticipantName,
		ParticipantAttributes: map[string]string{roleAttribute: "participant"},
		HidePhoneNumber:       true,
		RingingTimeout:        seconds(c.RingingTimeout),
		MaxCallDuration:       seconds(c.MaxCallDuration),
		WaitUntilAnswered:     false,
	})
	if err != nil {
		var de *errs.Error
		if errors.As(err, &de) {
			de.Details = nil
		}
		return CallInfo{}, err
	}
	var parsed sipParticipantJSON
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return CallInfo{}, errs.Wrap(errs.CodeInternal, err, "decode %s response", methodCreateParticipant)
	}
	info := CallInfo{
		ParticipantID: first(parsed.ParticipantID, parsed.ParticipantIDCamel),
		Identity:      first(parsed.ParticipantIdentity, parsed.ParticipantIdentityCamel),
		Room:          first(parsed.RoomName, parsed.RoomNameCamel),
		CallID:        first(parsed.SIPCallID, parsed.SIPCallIDCamel),
	}
	if info.Identity != c.Identity {
		return CallInfo{}, errs.Errorf(errs.CodeInternal, "the media server answered %s for another participant than the one the call was placed as", methodCreateParticipant)
	}
	return info, nil
}

func seconds(d time.Duration) string {
	return strconv.FormatInt(int64(d/time.Second), 10) + "s"
}
