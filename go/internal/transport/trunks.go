package transport

import (
	"bytes"
	"encoding/json"
	"fmt"
	"maps"
	"regexp"
	"slices"
	"strings"

	"github.com/punk-raven/dafter/go/internal/errs"
)

const secretScheme = "secret://"

var (
	trunkNamePattern = regexp.MustCompile(`^[a-z][a-z0-9-]{1,31}$`)
	providerPattern  = regexp.MustCompile(`^[a-z][a-z0-9_]{1,31}$`)
	addressPattern   = regexp.MustCompile(`^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(?::[0-9]{1,5})?$`)
	countryPattern   = regexp.MustCompile(`^[A-Z]{2}$`)
	secretPattern    = regexp.MustCompile(`^secret://[a-z0-9][a-z0-9._/-]{0,190}$`)
	PhoneNumber      = regexp.MustCompile(`^\+[1-9][0-9]{6,14}$`)
	sipTransports    = []string{"", "udp", "tcp", "tls"}
)

type Trunk struct {
	Provider           string
	Address            string
	Transport          string
	Numbers            []string
	DestinationCountry string
	AuthUsername       string
	AuthPassword       string
}

type Trunks map[string]Trunk

type trunkEntry struct {
	Provider           string   `json:"provider"`
	Address            string   `json:"address"`
	Transport          string   `json:"transport,omitempty"`
	Numbers            []string `json:"numbers"`
	DestinationCountry string   `json:"destinationCountry,omitempty"`
	AuthUsernameRef    string   `json:"authUsernameRef,omitempty"`
	AuthPasswordRef    string   `json:"authPasswordRef,omitempty"`
}

type trunkProblem struct {
	field   string
	because string
}

func LoadTrunks(raw []byte, env func(string) string) (Trunks, error) {
	var entries map[string]trunkEntry
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if err := d.Decode(&entries); err != nil {
		return nil, errs.Wrap(errs.CodeInvalidConfig, err, "decode the SIP trunk table")
	}
	trunks := Trunks{}
	var problems []string
	for _, name := range slices.Sorted(maps.Keys(entries)) {
		if !trunkNamePattern.MatchString(name) {
			problems = append(problems, fmt.Sprintf("at '/%s': a trunk name is lower case letters, digits and dashes, as a session names it", name))
		}
		trunk, found := entries[name].resolve(env)
		for _, p := range found {
			problems = append(problems, fmt.Sprintf("at '/%s/%s': %s", name, p.field, p.because))
		}
		trunks[name] = trunk
	}
	if len(problems) > 0 {
		e := errs.Errorf(errs.CodeInvalidConfig, "%d problem(s) with the SIP trunk table", len(problems))
		e.Details = problems
		return nil, e
	}
	return trunks, nil
}

func (e trunkEntry) resolve(env func(string) string) (Trunk, []trunkProblem) {
	var problems []trunkProblem
	add := func(field, because string) {
		problems = append(problems, trunkProblem{field, because})
	}
	if !providerPattern.MatchString(e.Provider) {
		add("provider", "names the carrier in lower case, and its credentials are read under that name")
	}
	if !addressPattern.MatchString(e.Address) {
		add("address", "a host name or IP address with an optional port, never a URI")
	}
	if !slices.Contains(sipTransports, e.Transport) {
		add("transport", "udp, tcp or tls")
	}
	if len(e.Numbers) == 0 {
		add("numbers", "a trunk places calls from at least one number of the operator's")
	}
	if slices.ContainsFunc(e.Numbers, func(n string) bool { return !PhoneNumber.MatchString(n) }) {
		add("numbers", "every number is E.164, a plus and up to 15 digits")
	}
	if e.DestinationCountry != "" && !countryPattern.MatchString(e.DestinationCountry) {
		add("destinationCountry", "an ISO 3166-1 alpha-2 code")
	}
	trunk := Trunk{
		Provider: e.Provider, Address: e.Address, Transport: e.Transport,
		Numbers: e.Numbers, DestinationCountry: e.DestinationCountry,
	}
	if (e.AuthUsernameRef == "") != (e.AuthPasswordRef == "") {
		add("authPasswordRef", "a trunk authenticates with a username and a password, or with neither")
		return trunk, problems
	}
	if e.AuthUsernameRef == "" {
		return trunk, problems
	}
	var because string
	if trunk.AuthUsername, because = e.secret(e.AuthUsernameRef, env); because != "" {
		add("authUsernameRef", because)
	}
	if trunk.AuthPassword, because = e.secret(e.AuthPasswordRef, env); because != "" {
		add("authPasswordRef", because)
	}
	return trunk, problems
}

func (e trunkEntry) secret(ref string, env func(string) string) (string, string) {
	if !secretPattern.MatchString(ref) {
		return "", "a secret:// reference, never a value"
	}
	parts := strings.Split(strings.Trim(strings.TrimPrefix(ref, secretScheme), "/"), "/")
	if len(parts) < 2 || parts[len(parts)-2] != e.Provider {
		return "", "a reference whose last two segments are the trunk's own provider and a name"
	}
	name := strings.NewReplacer("-", "_", ".", "_").Replace(strings.ToUpper(parts[len(parts)-2] + "_" + parts[len(parts)-1]))
	if !strings.HasPrefix(name, strings.ToUpper(e.Provider)+"_SIP_") {
		return "", "a trunk reads only its provider's SIP credentials, a name starting sip-"
	}
	value := env(name)
	if value == "" {
		return "", name + " is not in the control plane's environment"
	}
	return value, ""
}
