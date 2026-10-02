package configcheck

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/ids"
)

const dryRunSession = "s_00000000"

var (
	profileName  = regexp.MustCompile(`^[a-z][a-z0-9-]{0,62}$`)
	languageName = regexp.MustCompile(`^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$`)

	pointerEscaper = strings.NewReplacer("~", "~0", "/", "~1")
)

type Change struct {
	Kind     config.Kind
	Name     string
	Document json.RawMessage
}

func (c Change) Deletes() bool { return c.Document == nil }

func (c Change) pointer() string {
	return "/" + string(c.Kind) + "/" + pointerEscaper.Replace(c.Name)
}

func Write(draft config.Documents, change Change) (config.Documents, error) {
	structural, secrets := shape(change)
	if len(structural) > 0 {
		problems := append(structural, secrets...)
		return nil, detailed(problems, fmt.Sprintf("the %s document has %d problem(s)", change.Kind, len(problems)))
	}
	next := draft.Clone()
	if change.Deletes() {
		if _, ok := next[change.Kind][change.Name]; !ok {
			return nil, errs.Errorf(errs.CodeInvalidConfig, "at '%s': no document of that name is stored", change.pointer())
		}
		delete(next[change.Kind], change.Name)
		if _, err := All(next); err != nil {
			return nil, err
		}
		return next, nil
	}
	next.Set(change.Kind, change.Name, change.Document)
	catalog, err := config.Assemble(next)
	if err == nil {
		err = dryRun(catalog, combinations(catalog, change))
	}
	if err != nil || len(secrets) > 0 {
		return nil, joined(err, secrets)
	}
	return next, nil
}

func joined(err error, secrets []string) error {
	var de *errs.Error
	switch {
	case err == nil:
		return detailed(secrets, fmt.Sprintf("%d problem(s) in the document", len(secrets)))
	case len(secrets) == 0:
		return err
	case errors.As(err, &de):
		problems := append(slices.Clone(de.Details), secrets...)
		return detailed(problems, fmt.Sprintf("%d problem(s) in the document and the sessions it affects", len(problems)))
	default:
		return err
	}
}

func All(docs config.Documents) (*config.Catalog, error) {
	var problems []string
	for _, kind := range append(slices.Clone(config.GitKinds), config.EditableKinds...) {
		for _, name := range docs.Names(kind) {
			structural, secrets := shape(Change{Kind: kind, Name: name, Document: docs[kind][name]})
			problems = append(append(problems, structural...), secrets...)
		}
	}
	if len(problems) > 0 {
		return nil, detailed(problems, fmt.Sprintf("the document set has %d problem(s)", len(problems)))
	}
	catalog, err := config.Assemble(docs)
	if err != nil {
		return nil, err
	}
	if err := dryRun(catalog, combinations(catalog, Change{})); err != nil {
		return nil, err
	}
	return catalog, nil
}

func shape(change Change) (structural, secrets []string) {
	at := change.pointer()
	if problem := nameProblem(change.Kind, change.Name); problem != "" {
		return []string{located(at, problem)}, nil
	}
	if change.Deletes() {
		return nil, nil
	}
	var problems []string
	switch change.Kind {
	case config.KindAgents:
		if _, err := config.ParseAgentDefinition(change.Document); err != nil {
			problems = append(problems, relocated(at, err)...)
		}
	case config.KindLanguages, config.KindChannels:
		var axis config.Axis
		d := json.NewDecoder(bytes.NewReader(change.Document))
		d.DisallowUnknownFields()
		if err := d.Decode(&axis); err != nil {
			problems = append(problems, located(at, "an overlay states only tuning and overlay, each a JSON object"))
		}
		if len(axis.Tuning) > 0 && !isObject(axis.Tuning) {
			problems = append(problems, located(at+"/tuning", "is not a JSON object"))
		}
		if len(axis.Overlay) > 0 && !isObject(axis.Overlay) {
			problems = append(problems, located(at+"/overlay", "is not a JSON object"))
		}
	default:
		if !isObject(change.Document) {
			problems = append(problems, located(at, "is not a JSON object"))
		}
	}
	for _, p := range ScreenSecrets(change.Document) {
		secrets = append(secrets, strings.Replace(p, "at '", "at '"+at, 1))
	}
	return problems, secrets
}

func nameProblem(kind config.Kind, name string) string {
	switch kind {
	case config.KindDefaults:
		if name != config.DefaultsName {
			return "the defaults layer is one document named defaults"
		}
	case config.KindAgents:
		if !config.AgentSlug.MatchString(name) {
			return "an agent is named by a lowercase slug of 2 to 32 letters, digits and hyphens"
		}
	case config.KindTenants:
		if ids.ValidateID(ids.PrefixTenant, name) != nil {
			return "a tenant is named by its opaque tenant id"
		}
	case config.KindProfiles:
		if !profileName.MatchString(name) {
			return "a profile is named by a lowercase slug of up to 63 letters, digits and hyphens"
		}
	case config.KindLanguages:
		if !languageName.MatchString(name) {
			return "a language overlay is named by its BCP 47 language tag"
		}
	case config.KindChannels:
		if !config.Channel(name).Valid() {
			return "a channel overlay is named by a channel a session can select"
		}
	case config.KindLLMs:
		return ""
	default:
		return "no such kind of document is stored"
	}
	return ""
}

func isObject(raw json.RawMessage) bool {
	var m map[string]json.RawMessage
	return json.Unmarshal(raw, &m) == nil && m != nil
}

type combination struct {
	tenant, agent, profile, language string
	channel                          config.Channel
}

func (c combination) String() string {
	parts := []string{"tenant " + c.tenant}
	if c.agent != "" {
		parts = append(parts, "agent "+c.agent)
	}
	if c.profile != "" {
		parts = append(parts, "profile "+c.profile)
	}
	return strings.Join(append(parts, "language "+c.language, "channel "+string(c.channel)), ", ")
}

func combinations(catalog *config.Catalog, change Change) []combination {
	docs, _ := catalog.Documents()
	only := func(kind config.Kind, withNone bool) []string {
		if change.Kind == kind {
			if kind == config.KindProfiles {
				return []string{change.Name, ""}
			}
			return []string{change.Name}
		}
		names := docs.Names(kind)
		if withNone {
			names = append([]string{""}, names...)
		}
		return names
	}
	var out []combination
	for _, tenant := range only(config.KindTenants, false) {
		for _, agent := range only(config.KindAgents, true) {
			for _, profile := range only(config.KindProfiles, true) {
				for _, language := range only(config.KindLanguages, false) {
					for _, channel := range only(config.KindChannels, false) {
						out = append(out, combination{tenant, agent, profile, language, config.Channel(channel)})
					}
				}
			}
		}
	}
	return out
}

func dryRun(catalog *config.Catalog, combos []combination) error {
	var problems []string
	firstSeen := map[string]int{}
	seen := map[string]int{}
	for _, c := range combos {
		_, err := catalog.Resolve(config.Request{
			SessionID: dryRunSession, TenantID: c.tenant, Agent: c.agent, Profile: c.profile,
			Language: c.language, Channel: c.channel,
		})
		if err == nil {
			continue
		}
		var de *errs.Error
		details := []string{err.Error()}
		if errors.As(err, &de) && len(de.Details) > 0 {
			details = de.Details
		} else if errors.As(err, &de) {
			details = []string{de.Message}
		}
		for _, d := range details {
			if _, ok := seen[d]; !ok {
				firstSeen[d] = len(problems)
				problems = append(problems, d+" (resolving "+c.String()+")")
			}
			seen[d]++
		}
	}
	for d, n := range seen {
		if n > 1 {
			problems[firstSeen[d]] += fmt.Sprintf(" and %d other combination(s)", n-1)
		}
	}
	if len(problems) > 0 {
		return detailed(problems, fmt.Sprintf("%d problem(s) resolving the sessions the change affects", len(problems)))
	}
	return nil
}

func relocated(at string, err error) []string {
	var de *errs.Error
	if !errors.As(err, &de) || len(de.Details) == 0 {
		return []string{located(at, "is not a valid document")}
	}
	out := make([]string, 0, len(de.Details))
	for _, d := range de.Details {
		if rest, ok := strings.CutPrefix(d, "at '"); ok {
			out = append(out, "at '"+at+rest)
			continue
		}
		out = append(out, located(at, d))
	}
	return out
}

func located(pointer, because string) string {
	return fmt.Sprintf("at '%s': %s", pointer, because)
}

func detailed(problems []string, message string) *errs.Error {
	e := errs.Errorf(errs.CodeInvalidConfig, "%s", message)
	e.Details = problems
	return e
}

func itoa(i int) string { return strconv.Itoa(i) }
