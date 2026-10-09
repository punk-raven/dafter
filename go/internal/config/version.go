package config

import (
	"bytes"
	"encoding/json"
	"maps"
	"slices"

	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/schema"
)

const canarySchema = "https://schemas.dafter.dev/config/v1/canary.schema.json"

type Version struct {
	ID        string `json:"id"`
	Candidate bool   `json:"candidate,omitempty"`
}

type Canary struct {
	Profile string `json:"profile"`
	Percent int    `json:"percent"`
}

type CanaryRoute struct {
	Canary
	CandidateVersion string
}

func (c *ResolvedSessionConfig) VersionID() string {
	if c.Version == nil {
		return ""
	}
	return c.Version.ID
}

func (c *ResolvedSessionConfig) OnCandidate() bool {
	return c.Version != nil && c.Version.Candidate
}

func layerFields(raw json.RawMessage) map[string]json.RawMessage {
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil {
		return nil
	}
	return fields
}

func statedVersion(fields map[string]json.RawMessage) (Version, bool) {
	var v Version
	raw, ok := fields["version"]
	if !ok || json.Unmarshal(raw, &v) != nil {
		return Version{}, false
	}
	return v, v.ID != ""
}

func statesCandidate(fields map[string]json.RawMessage) bool {
	var v map[string]json.RawMessage
	if json.Unmarshal(fields["version"], &v) != nil {
		return false
	}
	_, ok := v["candidate"]
	return ok
}

func parseCanary(raw json.RawMessage) (*Canary, error) {
	if len(raw) == 0 {
		return nil, nil
	}
	if err := schema.ValidateDocument(canarySchema, raw, errs.CodeInvalidConfig); err != nil {
		return nil, err
	}
	var canary Canary
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if err := d.Decode(&canary); err != nil {
		return nil, errs.Wrap(errs.CodeInvalidConfig, err, "decode canary")
	}
	return &canary, nil
}

func (c *Catalog) checkVersions() error {
	owners := map[string]string{}
	if v, ok := statedVersion(layerFields(c.Defaults)); ok {
		owners[v.ID] = "/defaults"
	}
	var problems []string
	profiles := slices.Sorted(maps.Keys(c.Profiles))
	for _, name := range profiles {
		at := "/profiles/" + pointerEscaper.Replace(name)
		fields := layerFields(c.Profiles[name])
		if statesCandidate(fields) {
			problems = append(problems, located(at+"/version/candidate",
				"is stamped by resolution when the canary routes a session, never stated by a layer"))
		}
		v, ok := statedVersion(fields)
		if !ok {
			continue
		}
		if owner, taken := owners[v.ID]; taken {
			problems = append(problems, located(at+"/version/id",
				"is already the version of "+owner+", and every bundle has its own immutable id"))
			continue
		}
		owners[v.ID] = at
	}
	for _, name := range profiles {
		problems = append(problems, c.canaryProblems(name)...)
	}
	if len(problems) > 0 {
		return detailed(errs.CodeInvalidConfig, problems, "the catalog carries %d version problem(s)")
	}
	return nil
}

func (c *Catalog) canaryProblems(name string) []string {
	at := "/profiles/" + pointerEscaper.Replace(name) + "/canary"
	canary, err := parseCanary(layerFields(c.Profiles[name])["canary"])
	if err != nil {
		return prefixed(at, err)
	}
	if canary == nil {
		return nil
	}
	candidate, ok := c.Profiles[canary.Profile]
	switch {
	case canary.Profile == name:
		return []string{located(at+"/profile", "names the profile it sits on, so no session could ever change arm")}
	case !ok:
		return []string{located(at+"/profile", "no profile of that name is registered")}
	}
	fields := layerFields(candidate)
	var problems []string
	if _, chained := fields["canary"]; chained {
		problems = append(problems, located(at+"/profile", "the candidate carries a canary of its own, and canaries do not chain"))
	}
	if _, ok := statedVersion(fields); !ok {
		problems = append(problems, located(at+"/profile",
			"the candidate states no version id, so its sessions could not be told apart from the stable ones"))
	}
	return problems
}

func (c *Catalog) CanaryFor(req Request) (CanaryRoute, bool) {
	canary, err := parseCanary(layerFields(c.Profiles[c.profileFor(req)])["canary"])
	if err != nil || canary == nil || canary.Percent == 0 {
		return CanaryRoute{}, false
	}
	candidate, ok := statedVersion(layerFields(c.Profiles[canary.Profile]))
	if !ok {
		return CanaryRoute{}, false
	}
	return CanaryRoute{Canary: *canary, CandidateVersion: candidate.ID}, true
}

func (c *Catalog) profileFor(req Request) string {
	if req.Profile != "" || req.Agent == "" {
		return req.Profile
	}
	def, err := ParseAgentDefinition(c.Agents[req.Agent])
	if err != nil {
		return ""
	}
	return def.Profile
}

func (c *Catalog) profileLayer(name, pointer string, candidate bool) (source, bool, []string) {
	raw, ok := c.Profiles[name]
	if !ok {
		return source{}, false, []string{located(pointer, "no profile of that name is registered")}
	}
	fields := layerFields(raw)
	routed := false
	if candidate {
		canary, err := parseCanary(fields["canary"])
		if candidateRaw, known := c.Profiles[canaryProfile(canary)]; err == nil && known {
			raw, fields, routed = candidateRaw, layerFields(candidateRaw), true
		}
	}
	if _, stated := fields["canary"]; stated {
		delete(fields, "canary")
		stripped, err := json.Marshal(fields)
		if err != nil {
			return source{}, false, []string{located(pointer, "could not be composed")}
		}
		raw = stripped
	}
	return source{name: "profile", raw: raw}, routed, nil
}

func canaryProfile(canary *Canary) string {
	if canary == nil {
		return ""
	}
	return canary.Profile
}

func stampVersion(doc map[string]any, candidate bool) {
	version, ok := doc["version"].(map[string]any)
	if !ok {
		return
	}
	if candidate {
		version["candidate"] = true
		return
	}
	delete(version, "candidate")
}
