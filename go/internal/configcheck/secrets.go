package configcheck

import (
	"encoding/json"
	"maps"
	"regexp"
	"slices"
	"strings"
	"unicode"
)

var secretRef = regexp.MustCompile(`^secret://[a-z0-9][a-z0-9._/-]{0,190}$`)

var credentialWords = [][]string{
	{"secret"}, {"secrets"}, {"password"}, {"passwd"}, {"passphrase"}, {"token"},
	{"credential"}, {"credentials"}, {"apikey"}, {"api", "key"}, {"access", "key"},
	{"private", "key"}, {"client", "secret"}, {"bearer"}, {"authorization"}, {"auth"},
}

var keyShapes = []*regexp.Regexp{
	regexp.MustCompile(`^sk[-_][A-Za-z0-9_-]{16,}$`),
	regexp.MustCompile(`^(gsk|ghp|gho|ghs|xox[abpr]|AKIA|ASIA|AIza|hf|glpat)[-_A-Za-z0-9]{16,}$`),
	regexp.MustCompile(`^(?i:bearer|basic)\s+\S{12,}$`),
	regexp.MustCompile(`^eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$`),
	regexp.MustCompile(`^-----BEGIN [A-Z ]*PRIVATE KEY-----`),
}

func ScreenSecrets(doc json.RawMessage) []string {
	var value any
	if json.Unmarshal(doc, &value) != nil {
		return nil
	}
	return screen(value, "")
}

func screen(value any, pointer string) []string {
	var problems []string
	switch v := value.(type) {
	case map[string]any:
		for _, k := range slices.Sorted(maps.Keys(v)) {
			at := pointer + "/" + pointerEscaper.Replace(k)
			if credentialLike(k) && !mayHoldCredential(v[k]) {
				problems = append(problems, located(at,
					"names a credential, so it may only hold a secret:// reference; the value itself belongs in the environment"))
				continue
			}
			problems = append(problems, screen(v[k], at)...)
		}
	case []any:
		for i, item := range v {
			problems = append(problems, screen(item, pointer+"/"+itoa(i))...)
		}
	case string:
		for _, shape := range keyShapes {
			if shape.MatchString(strings.TrimSpace(v)) {
				return []string{located(pointer,
					"looks like a pasted credential; a document may only carry a secret:// reference to one")}
			}
		}
	}
	return problems
}

func mayHoldCredential(value any) bool {
	switch v := value.(type) {
	case string:
		return secretRef.MatchString(v)
	case map[string]any, []any:
		return false
	default:
		return true
	}
}

func credentialLike(key string) bool {
	words := splitWords(key)
	for _, pattern := range credentialWords {
		for i := 0; i+len(pattern) <= len(words); i++ {
			if slices.Equal(words[i:i+len(pattern)], pattern) {
				return true
			}
		}
	}
	return false
}

func splitWords(key string) []string {
	var words []string
	var current []rune
	runes := []rune(key)
	flush := func() {
		if len(current) > 0 {
			words = append(words, strings.ToLower(string(current)))
			current = current[:0]
		}
	}
	for i, r := range runes {
		switch {
		case r == '_' || r == '-' || r == '.' || unicode.IsSpace(r):
			flush()
		case unicode.IsUpper(r) && i > 0 && (unicode.IsLower(runes[i-1]) ||
			(i+1 < len(runes) && unicode.IsLower(runes[i+1]) && unicode.IsUpper(runes[i-1]))):
			flush()
			current = append(current, r)
		default:
			current = append(current, r)
		}
	}
	flush()
	return words
}
