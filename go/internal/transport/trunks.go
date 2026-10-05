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
	Inbound            *Inbound
}

type Trunks map[string]Trunk

type trunkEntry struct {
	Provider           string        `json:"provider"`
	Address            string        `json:"address,omitempty"`
	AddressRef         string        `json:"addressRef,omitempty"`
	Transport          string        `json:"transport,omitempty"`
	Numbers            []string      `json:"numbers,omitempty"`
	NumbersRef         string        `json:"numbersRef,omitempty"`
	DestinationCountry string        `json:"destinationCountry,omitempty"`
	AuthUsernameRef    string        `json:"authUsernameRef,omitempty"`
	AuthPasswordRef    string        `json:"authPasswordRef,omitempty"`
	Inbound            *inboundEntry `json:"inbound,omitempty"`
}

type trunkProblem struct {
	field   string
	because string
}

type resolver struct {
	provider string
	env      func(string) string
	prefix   string
	problems []trunkProblem
	set      int
	unset    []string
}

func (r *resolver) add(field, because string) {
	r.problems = append(r.problems, trunkProblem{r.prefix + field, because})
}

func (r *resolver) ref(field, ref string) string {
	if ref == "" {
		return ""
	}
	name, because := envName(r.provider, ref)
	if because != "" {
		r.add(field, because)
		return ""
	}
	value := strings.TrimSpace(r.env(name))
	if value == "" {
		r.unset = append(r.unset, field)
		return ""
	}
	r.set++
	return value
}

func (r *resolver) optional(field, ref string) string {
	if ref == "" {
		return ""
	}
	name, because := envName(r.provider, ref)
	if because != "" {
		r.add(field, because)
		return ""
	}
	value := strings.TrimSpace(r.env(name))
	if value != "" {
		r.set++
	}
	return value
}

func numberList(raw string) []string {
	if raw == "" {
		return nil
	}
	numbers := strings.Split(raw, ",")
	for i := range numbers {
		numbers[i] = strings.TrimSpace(numbers[i])
	}
	return numbers
}

func (r *resolver) missing() {
	for _, field := range r.unset {
		r.add(field, "its variable is not in the control plane's environment while the trunk's others are")
	}
}

func LoadTrunks(raw []byte, env func(string) string) (Trunks, []string, error) {
	var entries map[string]trunkEntry
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if err := d.Decode(&entries); err != nil {
		return nil, nil, errs.Wrap(errs.CodeInvalidConfig, err, "decode the SIP trunk table")
	}
	trunks := Trunks{}
	var skipped, problems []string
	for _, name := range slices.Sorted(maps.Keys(entries)) {
		if !trunkNamePattern.MatchString(name) {
			problems = append(problems, fmt.Sprintf("at '/%s': a trunk name is lower case letters, digits and dashes, as a session names it", name))
		}
		trunk, configured, found := entries[name].resolve(env)
		for _, p := range found {
			problems = append(problems, fmt.Sprintf("at '/%s/%s': %s", name, p.field, p.because))
		}
		if !configured {
			skipped = append(skipped, name)
			continue
		}
		trunks[name] = trunk
	}
	if len(problems) > 0 {
		e := errs.Errorf(errs.CodeInvalidConfig, "%d problem(s) with the SIP trunk table", len(problems))
		e.Details = problems
		return nil, nil, e
	}
	return trunks, skipped, nil
}

func (e trunkEntry) resolve(env func(string) string) (Trunk, bool, []trunkProblem) {
	r := &resolver{provider: e.Provider, env: env}
	if !providerPattern.MatchString(e.Provider) {
		r.add("provider", "names the carrier in lower case, and its credentials are read under that name")
	}
	if !slices.Contains(sipTransports, e.Transport) {
		r.add("transport", "udp, tcp or tls")
	}
	if e.DestinationCountry != "" && !countryPattern.MatchString(e.DestinationCountry) {
		r.add("destinationCountry", "an ISO 3166-1 alpha-2 code")
	}
	if (e.Address == "") == (e.AddressRef == "") {
		r.add("address", "a trunk states its address or a reference to it, one of the two")
	}
	if (e.Numbers == nil) == (e.NumbersRef == "") {
		r.add("numbers", "a trunk states its numbers or a reference to them, one of the two")
	}
	if (e.AuthUsernameRef == "") != (e.AuthPasswordRef == "") {
		r.add("authPasswordRef", "a trunk authenticates with a username and a password, or with neither")
	}
	trunk := Trunk{
		Provider: e.Provider, Transport: e.Transport, DestinationCountry: e.DestinationCountry,
		Address: e.Address, Numbers: e.Numbers,
	}
	if address := r.ref("addressRef", e.AddressRef); address != "" {
		trunk.Address = address
	}
	if numbers := r.ref("numbersRef", e.NumbersRef); numbers != "" {
		trunk.Numbers = numberList(numbers)
	}
	trunk.AuthUsername = r.ref("authUsernameRef", e.AuthUsernameRef)
	trunk.AuthPassword = r.ref("authPasswordRef", e.AuthPasswordRef)
	if r.set == 0 && len(r.unset) > 0 {
		return Trunk{}, false, r.problems
	}
	r.missing()
	checkOutbound(r, trunk)
	if e.Inbound != nil {
		trunk.Inbound = e.Inbound.resolve(r, e.Provider, trunk.Numbers)
	}
	return trunk, true, r.problems
}

func checkOutbound(r *resolver, trunk Trunk) {
	if trunk.Address != "" && !addressPattern.MatchString(trunk.Address) {
		r.add("address", "a host name or IP address with an optional port, never a URI")
	}
	if len(trunk.Numbers) == 0 {
		r.add("numbers", "a trunk places calls from at least one number of the operator's")
	}
	if slices.ContainsFunc(trunk.Numbers, func(n string) bool { return !PhoneNumber.MatchString(n) }) {
		r.add("numbers", "every number is E.164, a plus and up to 15 digits")
	}
}

func envName(provider, ref string) (string, string) {
	if !secretPattern.MatchString(ref) {
		return "", "a secret:// reference, never a value"
	}
	parts := strings.Split(strings.Trim(strings.TrimPrefix(ref, secretScheme), "/"), "/")
	if len(parts) < 2 || parts[len(parts)-2] != provider {
		return "", "a reference whose last two segments are the trunk's own provider and a name"
	}
	name := strings.NewReplacer("-", "_", ".", "_").Replace(strings.ToUpper(parts[len(parts)-2] + "_" + parts[len(parts)-1]))
	if !strings.HasPrefix(name, strings.ToUpper(provider)+"_SIP_") {
		return "", "a trunk reads only its provider's SIP settings, a name starting sip-"
	}
	return name, ""
}
