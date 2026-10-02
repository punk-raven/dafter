package transport

import (
	"net/url"
	"regexp"
	"strings"
)

var (
	sipUserPattern  = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,64}$`)
	tenantPattern   = regexp.MustCompile(`^t_[0-9a-f]{8}$`)
	languagePattern = regexp.MustCompile(`^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$`)
	profilePattern  = regexp.MustCompile(`^[a-z][a-z0-9-]{0,62}$`)
)

var inboundDialects = map[string]bool{"vobiz": true}

type Inbound struct {
	PublicURL  string
	SigningKey string
	BridgeHost string
	BridgeUser string
	Session    InboundSession
}

type InboundSession struct {
	TenantID string `json:"tenantId"`
	Language string `json:"language"`
	Profile  string `json:"profile,omitempty"`
}

type inboundEntry struct {
	PublicURLRef  string         `json:"publicUrlRef"`
	SigningKeyRef string         `json:"signingKeyRef"`
	BridgeHost    string         `json:"bridgeHost"`
	BridgeUserRef string         `json:"bridgeUserRef"`
	Session       InboundSession `json:"session"`
}

func (e inboundEntry) resolve(outer *resolver, provider string, numbers []string) *Inbound {
	r := &resolver{provider: provider, env: outer.env, prefix: "inbound/"}
	in := &Inbound{
		PublicURL:  strings.TrimRight(r.ref("publicUrlRef", e.PublicURLRef), "/"),
		SigningKey: r.ref("signingKeyRef", e.SigningKeyRef),
		BridgeHost: e.BridgeHost,
		BridgeUser: r.ref("bridgeUserRef", e.BridgeUserRef),
		Session:    e.Session,
	}
	if !inboundDialects[provider] {
		r.add("provider", "no carrier webhook is built for this provider, so it takes no inbound calls")
	}
	for _, ref := range [][2]string{{"publicUrlRef", e.PublicURLRef}, {"signingKeyRef", e.SigningKeyRef}, {"bridgeUserRef", e.BridgeUserRef}} {
		if ref[1] == "" {
			r.add(ref[0], "an inbound trunk needs it")
		}
	}
	defer func() { outer.problems = append(outer.problems, r.problems...) }()
	if r.set == 0 && len(r.unset) > 0 {
		return nil
	}
	r.missing()
	if u, err := url.Parse(in.PublicURL); in.PublicURL != "" && (err != nil || u.Scheme != "https" || u.Host == "" || u.RawQuery != "" || u.Fragment != "") {
		r.add("publicUrlRef", "the https address the carrier reaches the control plane at, with no query")
	}
	if !addressPattern.MatchString(e.BridgeHost) {
		r.add("bridgeHost", "the carrier's host name that answers a call into its application")
	}
	if in.BridgeUser != "" && !sipUserPattern.MatchString(in.BridgeUser) {
		r.add("bridgeUserRef", "the user part of the carrier application's SIP address")
	}
	if !tenantPattern.MatchString(e.Session.TenantID) || !languagePattern.MatchString(e.Session.Language) ||
		(e.Session.Profile != "" && !profilePattern.MatchString(e.Session.Profile)) {
		r.add("session", "the tenant, language and optional profile an inbound call's session resolves with")
	}
	if len(numbers) == 0 {
		r.add("session", "an inbound trunk answers only its own numbers, and it has none")
	}
	return in
}
