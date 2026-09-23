package config

import (
	"github.com/punk-raven/dafter/go/internal/errs"
)

type crossFieldRule struct {
	broken  func(*ResolvedSessionConfig) bool
	code    errs.ErrorCode
	pointer string
	because string
}

var crossFieldRules = []crossFieldRule{
	{
		broken:  func(c *ResolvedSessionConfig) bool { return c.PrivacyMode == PrivacySealed && c.Agent.Enabled },
		code:    errs.CodePrivacyModeForbids,
		pointer: "/agent/enabled",
		because: "a sealed session cannot have an agent dispatched into it, because an agent that transcribes or responds must decrypt the audio",
	},
	{
		broken:  func(c *ResolvedSessionConfig) bool { return c.Recording.Enabled && c.Recording.ConsentArtifactID == "" },
		code:    errs.CodeConsentRequired,
		pointer: "/recording/consentArtifactId",
		because: "recording cannot proceed without a consent artifact",
	},
	{
		broken: func(c *ResolvedSessionConfig) bool {
			return c.Recording.Enabled &&
				c.Recording.StartAt == StartAtSessionCreate &&
				c.Recording.Layout != LayoutRoomComposite
		},
		code:    errs.CodeInvalidConfig,
		pointer: "/recording/layout",
		because: "capture at session creation needs a room composite, because a track egress attaches to a published track and cannot start before one exists",
	},
	{
		broken: func(c *ResolvedSessionConfig) bool {
			return c.Channel == ChannelTelephony && c.VideoEnabled()
		},
		code:    errs.CodeInvalidConfig,
		pointer: "/media/video/enabled",
		because: "telephony carries narrowband audio and no video at all, so a video profile on this channel describes a stream that cannot exist",
	},
	{
		broken: func(c *ResolvedSessionConfig) bool {
			v := c.video()
			return v != nil && v.ScalabilityMode != "" && !v.Codec.Layered()
		},
		code:    errs.CodeInvalidConfig,
		pointer: "/media/video/scalabilityMode",
		because: "a scalability mode names spatial and temporal layers that only a layered codec produces, so with this codec it promises layering the session will not get",
	},
	{
		broken: func(c *ResolvedSessionConfig) bool {
			e := c.Egress()
			return e != nil && e.Preset != "" && e.StatesExplicitFields()
		},
		code:    errs.CodeInvalidConfig,
		pointer: "/media/egress/preset",
		because: "a preset and explicit encode fields are two answers to one question, and the recording can only be encoded one way",
	},
	{
		broken: func(c *ResolvedSessionConfig) bool {
			return c.Recording.Enabled && !c.VideoEnabled() && c.Egress().StatesVideo()
		},
		code:    errs.CodeInvalidConfig,
		pointer: "/media/egress",
		because: "the session publishes no video, so an egress profile that names a video size, framerate, bitrate, codec or preset describes an encode of a stream that does not exist",
	},
	{
		broken: func(c *ResolvedSessionConfig) bool {
			return c.EncryptionMode() != c.PrivacyMode.Encryption()
		},
		code:    errs.CodeInvalidConfig,
		pointer: "/media/encryption/mode",
		because: "the privacy mode decides the encryption mode, open is transport only and sealed or trusted_agent is end to end, so a media profile stating otherwise promises a guarantee the session will not get",
	},
	{
		broken: func(c *ResolvedSessionConfig) bool {
			return c.PrivacyMode != PrivacyOpen && c.Recording.Enabled
		},
		code:    errs.CodePrivacyModeForbids,
		pointer: "/recording/enabled",
		because: "every recording layout is a server-side egress, and under end-to-end encryption the media server and its egress see only ciphertext, so this session is recorded client-side or not at all",
	},
}

func (c *ResolvedSessionConfig) validateCrossFieldRules() error {
	var broken []crossFieldRule
	for _, rule := range crossFieldRules {
		if rule.broken(c) {
			broken = append(broken, rule)
		}
	}
	if len(broken) == 0 {
		return nil
	}
	e := errs.Errorf(broken[0].code, "%d rule(s) rejected session %s", len(broken), c.SessionID)
	for _, rule := range broken {
		e.Details = append(e.Details, located(rule.pointer, rule.because))
	}
	return e
}
