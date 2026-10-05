package config

import (
	"encoding/json"
	"fmt"
	"maps"
	"reflect"
	"slices"
	"strings"

	"github.com/punk-raven/dafter/go/internal/errs"
)

type Catalog struct {
	Defaults  json.RawMessage            `json:"defaults"`
	Tenants   map[string]json.RawMessage `json:"tenants"`
	Profiles  map[string]json.RawMessage `json:"profiles"`
	Languages map[string]Axis            `json:"languages"`
	Channels  map[Channel]Axis           `json:"channels"`
	LLMs      map[string]json.RawMessage `json:"llms"`
	Agents    map[string]json.RawMessage `json:"agents,omitempty"`
}

type Axis struct {
	Tuning  json.RawMessage `json:"tuning,omitempty"`
	Overlay json.RawMessage `json:"overlay,omitempty"`
}

type Request struct {
	SessionID string
	TenantID  string
	Agent     string
	Profile   string
	Language  string
	Channel   Channel
	LLM       string
	Overrides json.RawMessage
}

type Resolution struct {
	Config   *ResolvedSessionConfig
	Document []byte
	Hash     string
}

var reservedOverrides = []string{"sessionId", "tenantId", "configHash"}

var operatorOnlyStageFields = [][]string{{"credentialRef"}, {"options", "endpoint"}, {"options", "baseUrl"}}

func (c *Catalog) Resolve(req Request) (*Resolution, error) {
	doc, err := c.compose(req)
	if err != nil {
		return nil, err
	}

	doc["sessionId"] = req.SessionID
	doc["tenantId"] = req.TenantID
	doc["language"] = req.Language
	doc["channel"] = string(req.Channel)
	if req.LLM != "" {
		doc["llm"] = req.LLM
	}
	stampEncryption(doc)

	raw, err := json.Marshal(doc)
	if err != nil {
		return nil, errs.Wrap(errs.CodeInternal, err, "marshal resolved document")
	}
	document, hash, err := Seal(raw)
	if err != nil {
		return nil, err
	}
	cfg, err := Parse(document)
	if err != nil {
		return nil, err
	}
	return &Resolution{Config: cfg, Document: document, Hash: hash}, nil
}

type source struct {
	name     string
	raw      json.RawMessage
	replaces string
}

func (c *Catalog) compose(req Request) (map[string]any, error) {
	sources := []source{{name: "defaults", raw: c.Defaults}}
	var problems []string

	profile, profilePointer := req.Profile, "/profile"
	if req.Agent != "" {
		agent, agentProfile, unknown := c.agentLayer(req.Agent)
		problems = append(problems, unknown...)
		sources = append(sources, agent)
		if profile == "" && agentProfile != "" {
			profile, profilePointer = agentProfile, "/agents/"+pointerEscaper.Replace(req.Agent)+"/profile"
		}
	}
	if raw, ok := c.Tenants[req.TenantID]; ok {
		sources = append(sources, source{name: "tenant", raw: raw})
	} else {
		problems = append(problems, located("/tenantId", "no tenant configuration is registered"))
	}
	if profile != "" {
		if raw, ok := c.Profiles[profile]; ok {
			sources = append(sources, source{name: "profile", raw: raw})
		} else {
			problems = append(problems, located(profilePointer, "no profile of that name is registered"))
		}
	}
	if len(req.Overrides) > 0 {
		problems = append(problems, reservedProblems(req.Overrides)...)
		problems = append(problems, routeProblems(req.Overrides)...)
	}
	if len(problems) > 0 {
		return nil, detailed(errs.CodeInvalidConfig, problems, "%d layer(s) could not be resolved")
	}

	language, ok := c.Languages[req.Language]
	if !ok {
		problems = append(problems, located("/language",
			"no language overlay is configured, so a turn strategy cannot be resolved for this language"))
	}
	channel, ok := c.Channels[req.Channel]
	if !ok {
		problems = append(problems, located("/channel",
			"no channel overlay is configured, so turn constants cannot be resolved for this channel"))
	}
	overlays := []source{
		{name: req.Language + " language overlay", raw: language.Overlay},
		{name: string(req.Channel) + " channel overlay", raw: channel.Overlay},
	}
	if req.LLM != "" {
		route, unknown := c.llmRoute(req.LLM)
		problems = append(problems, unknown...)
		overlays = append(overlays, route)
	}
	if len(problems) > 0 {
		return nil, detailed(errs.CodeUnsupportedCapability, problems, "%d composition axis/axes could not be resolved")
	}

	sources = append(sources, source{name: "language tuning", raw: language.Tuning}, source{name: "channel tuning", raw: channel.Tuning})
	doc := map[string]any{}
	for _, s := range sources {
		m, err := decode(s)
		if err != nil {
			return nil, err
		}
		doc = merge(doc, m, "")
	}
	overrides, err := decode(source{name: "overrides", raw: req.Overrides})
	if err != nil {
		return nil, err
	}
	doc = merge(doc, overrides, "")
	pins := make([]map[string]any, len(overlays))
	for i, s := range overlays {
		if pins[i], err = s.pin(); err != nil {
			return nil, err
		}
		doc = merge(cut(doc, s.replaces), pins[i], "")
	}

	for _, pointer := range dropped(overrides, doc, "") {
		problems = append(problems, located(pointer,
			"the "+pinnedBy(overlays, pins, pointer)+" pins this, so a session override cannot change it"))
	}
	if len(problems) > 0 {
		return nil, detailed(errs.CodeInvalidConfig, problems, "%d session override(s) would be dropped by an overlay")
	}
	if problems = dropIdleTelephony(doc, overrides, req.Channel); len(problems) > 0 {
		return nil, detailed(errs.CodeInvalidConfig, problems, "%d session override(s) tune phone calls in a session that takes none")
	}
	return doc, nil
}

func dropIdleTelephony(doc, overrides map[string]any, channel Channel) []string {
	if channel == ChannelTelephony {
		return nil
	}
	telephony, _ := doc["telephony"].(map[string]any)
	if guests, _ := telephony["phoneGuests"].(string); guests != "" && guests != string(PhoneGuestsOff) {
		return nil
	}
	delete(doc, "telephony")
	stated, _ := overrides["telephony"].(map[string]any)
	var problems []string
	for _, k := range slices.Sorted(maps.Keys(stated)) {
		if k != "phoneGuests" {
			problems = append(problems, located("/telephony/"+pointerEscaper.Replace(k),
				"tunes phone calls, but this session takes none, so it applies only with phoneGuests dial_out, dial_in or both"))
		}
	}
	return problems
}

func decode(s source) (map[string]any, error) {
	m := map[string]any{}
	if len(s.raw) == 0 {
		return m, nil
	}
	if err := json.Unmarshal(s.raw, &m); err != nil {
		return nil, errs.Wrap(errs.CodeInvalidConfig, err, "the %s layer is not a JSON object", s.name)
	}
	return m, nil
}

func dropped(over, doc map[string]any, pointer string) []string {
	var pointers []string
	for _, k := range slices.Sorted(maps.Keys(over)) {
		at := pointer + "/" + pointerEscaper.Replace(k)
		om, overIsObject := over[k].(map[string]any)
		dm, docIsObject := doc[k].(map[string]any)
		if overIsObject && docIsObject {
			pointers = append(pointers, dropped(om, dm, at)...)
			continue
		}
		if at == nearMissesPointer && keptAll(over[k], doc[k]) {
			continue
		}
		if !reflect.DeepEqual(over[k], doc[k]) {
			pointers = append(pointers, at)
		}
	}
	return pointers
}

var (
	pointerEscaper   = strings.NewReplacer("~", "~0", "/", "~1")
	pointerUnescaper = strings.NewReplacer("~1", "/", "~0", "~")
)

func pinnedBy(overlays []source, pins []map[string]any, pointer string) string {
	tokens := strings.Split(strings.TrimPrefix(pointer, "/"), "/")
	for i := len(pins) - 1; i > 0; i-- {
		if overlays[i].pins(pointer) {
			return overlays[i].name
		}
		var node any = pins[i]
		for _, token := range tokens {
			m, _ := node.(map[string]any)
			node = m[pointerUnescaper.Replace(token)]
		}
		if node != nil {
			return overlays[i].name
		}
	}
	return overlays[0].name
}

func stampEncryption(doc map[string]any) {
	mode, ok := doc["privacyMode"].(string)
	if !ok || !PrivacyMode(mode).Valid() {
		return
	}
	media, _ := doc["media"].(map[string]any)
	if media == nil {
		media = map[string]any{}
		doc["media"] = media
	}
	encryption, _ := media["encryption"].(map[string]any)
	if encryption == nil {
		encryption = map[string]any{}
		media["encryption"] = encryption
	}
	if _, stated := encryption["mode"]; !stated {
		encryption["mode"] = string(PrivacyMode(mode).Encryption())
	}
	if _, stated := encryption["keyModel"]; !stated && encryption["mode"] == string(EncryptionE2EE) {
		encryption["keyModel"] = string(KeyModelServerShared)
	}
}

func reservedProblems(overrides json.RawMessage) []string {
	var m map[string]any
	if json.Unmarshal(overrides, &m) != nil {
		return nil
	}
	var problems []string
	for _, k := range reservedOverrides {
		if _, ok := m[k]; ok {
			problems = append(problems, located("/"+k, "is minted by the control plane and cannot be supplied as a session override"))
		}
	}
	return append(problems, operatorOnlyProblems(m)...)
}

func operatorOnlyProblems(overrides map[string]any) []string {
	agent, _ := overrides["agent"].(map[string]any)
	pipeline, _ := agent["pipeline"].(map[string]any)
	scribe, _ := overrides["scribe"].(map[string]any)
	var problems []string
	for _, stage := range slices.Sorted(maps.Keys(pipeline)) {
		problems = append(problems, redirections("/agent/pipeline/"+stage, pipeline[stage])...)
	}
	for _, field := range []string{"llm", "judge"} {
		problems = append(problems, redirections("/scribe/"+field, scribe[field])...)
	}
	telephony, _ := overrides["telephony"].(map[string]any)
	if _, ok := telephony["trunk"]; ok {
		problems = append(problems, located("/telephony/trunk",
			"names the SIP trunk a call goes out on, which only the operator's configuration sets, never a session override"))
	}
	return problems
}

func redirections(at string, stage any) []string {
	ref, _ := stage.(map[string]any)
	var problems []string
	for _, path := range operatorOnlyStageFields {
		if present(ref, path) {
			problems = append(problems, located(at+"/"+strings.Join(path, "/"),
				"names the endpoint or credential a stage uses, which only the operator's configuration sets, never a session override"))
		}
	}
	return problems
}

func present(doc map[string]any, path []string) bool {
	for _, k := range path[:len(path)-1] {
		doc, _ = doc[k].(map[string]any)
	}
	_, ok := doc[path[len(path)-1]]
	return ok
}

func merge(base, over map[string]any, pointer string) map[string]any {
	out := make(map[string]any, len(base)+len(over))
	for k, v := range base {
		out[k] = v
	}
	for k, v := range over {
		at := pointer + "/" + pointerEscaper.Replace(k)
		bm, baseIsObject := out[k].(map[string]any)
		om, overIsObject := v.(map[string]any)
		if baseIsObject && overIsObject {
			out[k] = merge(bm, om, at)
			continue
		}
		if at == nearMissesPointer {
			if union, ok := unionNearMisses(out[k], v); ok {
				out[k] = union
				continue
			}
		}
		out[k] = v
	}
	return out
}

func located(pointer, because string) string {
	return fmt.Sprintf("at '%s': %s", pointer, because)
}

func detailed(code errs.ErrorCode, problems []string, format string) *errs.Error {
	e := errs.Errorf(code, format, len(problems))
	e.Details = problems
	return e
}
