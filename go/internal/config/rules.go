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
