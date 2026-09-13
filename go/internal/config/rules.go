package config

import "github.com/punk-raven/dafter/go/internal/errs"

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
}

func (c *ResolvedSessionConfig) validateCrossFieldRules() error {
	for _, rule := range crossFieldRules {
		if rule.broken(c) {
			return errs.Errorf(rule.code, "session %s: %s", c.SessionID, rule.because)
		}
	}
	return nil
}
