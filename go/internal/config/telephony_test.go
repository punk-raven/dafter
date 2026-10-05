package config_test

import (
	"encoding/json"
	"errors"
	"os"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
)

const telephonyVectors = "../../../testdata/telephony/rules.json"

type telephonyCase struct {
	Name     string                     `json:"name"`
	Patch    map[string]json.RawMessage `json:"patch"`
	Notice   string                     `json:"notice"`
	Trunk    string                     `json:"trunk"`
	Caller   string                     `json:"callerCheck"`
	Rejected *struct {
		Code     errs.ErrorCode `json:"code"`
		Pointers []string       `json:"pointers"`
	} `json:"rejected"`
}

type telephonyVectorFile struct {
	Base  map[string]json.RawMessage `json:"base"`
	Cases []telephonyCase            `json:"cases"`
}

func TestTelephonyRulesMatchTheSharedVectors(t *testing.T) {
	t.Parallel()
	raw, err := os.ReadFile(telephonyVectors)
	if err != nil {
		t.Fatal(err)
	}
	var f telephonyVectorFile
	if err := json.Unmarshal(raw, &f); err != nil {
		t.Fatal(err)
	}
	for _, c := range f.Cases {
		t.Run(c.Name, func(t *testing.T) {
			t.Parallel()
			doc := map[string]json.RawMessage{}
			for k, v := range f.Base {
				doc[k] = v
			}
			for k, v := range c.Patch {
				doc[k] = v
			}
			document, err := json.Marshal(doc)
			if err != nil {
				t.Fatal(err)
			}
			cfg, err := config.Parse(document)
			if c.Rejected == nil {
				if err != nil {
					t.Fatalf("rejected: %v", err)
				}
				notice := config.NoticeAlways
				if cfg.Telephony != nil && cfg.Telephony.RecordingNotice != "" {
					notice = cfg.Telephony.RecordingNotice
				}
				if string(notice) != c.Notice || cfg.TrunkName() != c.Trunk {
					t.Errorf("notice %s trunk %q, want %s and %q", notice, cfg.TrunkName(), c.Notice, c.Trunk)
				}
				if c.Caller != "" && string(cfg.CallerCheck()) != c.Caller {
					t.Errorf("caller check %s, want %s", cfg.CallerCheck(), c.Caller)
				}
				return
			}
			var de *errs.Error
			if !errors.As(err, &de) {
				t.Fatalf("want %s, got %v", c.Rejected.Code, err)
			}
			if de.Code != c.Rejected.Code {
				t.Errorf("code %s, want %s", de.Code, c.Rejected.Code)
			}
			var pointers []string
			for _, d := range de.Details {
				if m := detailPointer.FindStringSubmatch(d); m != nil {
					pointers = append(pointers, m[1])
				}
			}
			if !slices.Equal(pointers, c.Rejected.Pointers) {
				t.Errorf("pointers %v, want %v", pointers, c.Rejected.Pointers)
			}
		})
	}
}

func TestACallRingsAndLastsWhatTheSessionStatesOrTheDefaults(t *testing.T) {
	t.Parallel()
	bare := &config.ResolvedSessionConfig{}
	if bare.RingingTimeout() != 30*time.Second || bare.MaxCallDuration() != 30*time.Minute || bare.TrunkName() != "" {
		t.Errorf("defaults: rings %s, lasts %s, trunk %q", bare.RingingTimeout(), bare.MaxCallDuration(), bare.TrunkName())
	}
	stated := &config.ResolvedSessionConfig{Telephony: &config.Telephony{
		Trunk: "carrier-in", RingingTimeoutSeconds: 20, MaxCallDurationSeconds: 600,
	}}
	if stated.RingingTimeout() != 20*time.Second || stated.MaxCallDuration() != 10*time.Minute || stated.TrunkName() != "carrier-in" {
		t.Errorf("stated: rings %s, lasts %s, trunk %q", stated.RingingTimeout(), stated.MaxCallDuration(), stated.TrunkName())
	}
}

func TestResolveRefusesAnOverrideThatPicksTheTrunk(t *testing.T) {
	t.Parallel()
	req := request()
	req.Overrides = json.RawMessage(`{"telephony": {"trunk": "someone-elses", "ringingTimeoutSeconds": 15}}`)
	de := resolveError(t, req)
	if de.Code != errs.CodeInvalidConfig {
		t.Errorf("want %s, got %s", errs.CodeInvalidConfig, de.Code)
	}
	if joined := strings.Join(de.Details, "\n"); !strings.Contains(joined, "/telephony/trunk") || strings.Contains(joined, "ringingTimeoutSeconds") {
		t.Errorf("want only the trunk refused, the operator's to choose: %v", de.Details)
	}

	req.Overrides = json.RawMessage(`{"telephony": {"phoneGuests": "dial_out", "ringingTimeoutSeconds": 15, "recordingNotice": "when_recorded"}}`)
	tel := resolve(t, req).Config.Telephony
	if tel == nil || tel.RingingTimeoutSeconds != 15 || tel.RecordingNotice != config.NoticeWhenRecorded || tel.Trunk != "vobiz" {
		t.Errorf("a session override that names no trunk was not applied over the tenant's trunk: %+v", tel)
	}
}

func TestASessionThatTakesNoPhoneKeepsNoTelephonyBlock(t *testing.T) {
	t.Parallel()
	plain := resolve(t, request())
	if plain.Config.Telephony != nil || strings.Contains(string(plain.Document), "telephony") {
		t.Errorf("the tenant's phone line reached a session that takes no phone: %s", plain.Document)
	}
	off := request()
	off.Overrides = json.RawMessage(`{"telephony": {"phoneGuests": "off"}}`)
	if got := resolve(t, off); got.Hash != plain.Hash {
		t.Errorf("phoneGuests off changed the document: %s", got.Document)
	}

	guests := request()
	guests.Overrides = json.RawMessage(`{"telephony": {"phoneGuests": "dial_out"}}`)
	cfg := resolve(t, guests).Config
	if !cfg.TakesPhoneCalls() || cfg.TrunkName() != "vobiz" {
		t.Errorf("a meeting that asked for phone guests did not get the tenant's trunk: %+v", cfg.Telephony)
	}

	phone := request()
	phone.Channel = config.ChannelTelephony
	phone.Language = "hi"
	if cfg := resolve(t, phone).Config; !cfg.TakesPhoneCalls() || cfg.TrunkName() != "vobiz" {
		t.Errorf("a telephony session did not get the tenant's trunk: %+v", cfg.Telephony)
	}

	idle := request()
	idle.Overrides = json.RawMessage(`{"telephony": {"ringingTimeoutSeconds": 15}}`)
	de := resolveError(t, idle)
	if de.Code != errs.CodeInvalidConfig || !strings.Contains(strings.Join(de.Details, "\n"), "/telephony/ringingTimeoutSeconds") {
		t.Errorf("tuning phone calls in a session that takes none was not refused by pointer: %+v", de)
	}
}
