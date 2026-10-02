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
		broken: func(c *ResolvedSessionConfig) bool {
			return c.Agent.Addressing.WaitsToBeCalled() && c.Agent.Name == ""
		},
		code:    errs.CodeInvalidConfig,
		pointer: "/agent/name",
		because: "an agent that stays quiet until it is called by name needs a name to be called by",
	},
	{
		broken:  func(c *ResolvedSessionConfig) bool { return c.Agent.NearMissIsItsName() },
		code:    errs.CodeInvalidConfig,
		pointer: "/agent/addressing/nearMisses",
		because: "a near miss is a word that must never wake the agent, so one that is also its name or an alias contradicts itself",
	},
	{
		broken:  func(c *ResolvedSessionConfig) bool { return c.Agent.LanguageSwitching.LeavesOut(c.Language) },
		code:    errs.CodeInvalidConfig,
		pointer: "/agent/languageSwitching/languages",
		because: "the agent starts in the session's language, so a list of languages to switch between that leaves it out could never switch back to it",
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
			return c.Channel == ChannelTelephony && c.EncryptionMode() == EncryptionE2EE
		},
		code:    errs.CodePrivacyModeForbids,
		pointer: "/channel",
		because: "a phone call cannot be end-to-end encrypted, because the media server's SIP bridge decodes every frame between the phone network and the room, so telephony needs privacy mode open",
	},
	{
		broken: func(c *ResolvedSessionConfig) bool {
			return c.Channel == ChannelTelephony && c.Recording.Enabled && !c.Agent.Enabled
		},
		code:    errs.CodeInvalidConfig,
		pointer: "/agent/enabled",
		because: "a person on a phone sees no recording indicator and only the agent tells them the call is recorded, so a recorded telephony session needs the agent",
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
	{
		broken: func(c *ResolvedSessionConfig) bool {
			return c.TranscriptionMode().Transcribes() && c.transcriptionConsent() == ""
		},
		code:    errs.CodeConsentRequired,
		pointer: "/transcription/consentArtifactId",
		because: "transcribing the people in the call cannot proceed without a consent artifact",
	},
	{
		broken: func(c *ResolvedSessionConfig) bool {
			return c.PrivacyMode == PrivacySealed && c.TranscriptionMode().Transcribes()
		},
		code:    errs.CodePrivacyModeForbids,
		pointer: "/transcription/mode",
		because: "a sealed session never sends its audio to a transcription provider",
	},
	{
		broken: func(c *ResolvedSessionConfig) bool {
			return c.TranscriptionMode().Live() && !c.Agent.Enabled
		},
		code:    errs.CodeInvalidConfig,
		pointer: "/transcription/mode",
		because: "live captions are transcribed by the session's agent worker, so a session without an agent has nobody to caption it",
	},
	{
		broken: func(c *ResolvedSessionConfig) bool {
			return c.TranscriptionMode().AfterCall() &&
				(!c.Recording.Enabled || c.Recording.EffectiveLayout() != LayoutTrack)
		},
		code:    errs.CodeInvalidConfig,
		pointer: "/recording/layout",
		because: "the transcript after the call is made from each participant's own recorded track, so it needs recording on with the track layout",
	},
	{
		broken: func(c *ResolvedSessionConfig) bool {
			return c.TranscriptionMode().AfterCall() && c.transcriptionBatch() == nil
		},
		code:    errs.CodeInvalidConfig,
		pointer: "/transcription/batch",
		because: "the transcript after the call is made by a batch provider, and the session pins none",
	},
	{
		broken:  func(c *ResolvedSessionConfig) bool { return c.PrivacyMode == PrivacySealed && c.ScribeEnabled() },
		code:    errs.CodePrivacyModeForbids,
		pointer: "/scribe/enabled",
		because: "a sealed session never has a scribe, because it reads what everyone said and sends it to its LLM provider",
	},
	{
		broken:  func(c *ResolvedSessionConfig) bool { return c.ScribeEnabled() && c.scribeConsent() == "" },
		code:    errs.CodeConsentRequired,
		pointer: "/scribe/consentArtifactId",
		because: "the scribe cannot read the call and keep its minutes without a consent artifact",
	},
	{
		broken:  func(c *ResolvedSessionConfig) bool { return c.ScribeEnabled() && !c.TranscriptionMode().Live() },
		code:    errs.CodeInvalidConfig,
		pointer: "/transcription/mode",
		because: "the scribe reads the live captions the agent worker publishes rather than transcribing again, so it needs transcription live or both",
	},
	{
		broken:  func(c *ResolvedSessionConfig) bool { return c.ScribeEnabled() && c.scribeLLM() == nil },
		code:    errs.CodeInvalidConfig,
		pointer: "/scribe/llm",
		because: "the scribe writes its notes and minutes with an LLM, and the session pins none",
	},
	{
		broken:  func(c *ResolvedSessionConfig) bool { return c.ScribeEnabled() && c.ScribePool() == c.Agent.Pool },
		code:    errs.CodeInvalidConfig,
		pointer: "/scribe/pool",
		because: "the scribe runs a different program from the agent, so a pool serving both would hand the agent's job to the scribe or the scribe's to the agent",
	},
	{
		broken: func(c *ResolvedSessionConfig) bool {
			return c.Turn.Strategy == TurnProviderEndpointing && c.Turn.LocalVADDecidesTurn()
		},
		code:    errs.CodeInvalidConfig,
		pointer: "/turn/localVadEnabled",
		because: "under provider endpointing the recognizer's own VAD decides the turn, so a local VAD deciding it too runs two detectors on one stream; a local VAD that only catches barge-in is turn.interruption.localVadEnabled",
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
