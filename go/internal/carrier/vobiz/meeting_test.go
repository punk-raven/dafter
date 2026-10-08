package vobiz_test

import (
	"bytes"
	"strings"
	"testing"
	"time"

	"github.com/punk-raven/dafter/go/internal/carrier/vobiz"
)

const pinURL = "https://calls.example.com/telephony/vobiz/pin"

func TestTheMeetingAnswersArePinned(t *testing.T) {
	t.Parallel()
	for name, got := range map[string][]byte{
		"ask-pin.xml":       vobiz.AskForPIN(pinURL, 8, false),
		"ask-pin-again.xml": vobiz.AskForPIN(pinURL, 8, true),
		"admit.xml":         vobiz.Admit(token, "https://calls.example.com/telephony/vobiz/held/"+token, 30*time.Minute),
		"refuse.xml":        vobiz.Refuse(),
	} {
		if want := bytes.TrimSpace(fixture(t, name)); !bytes.Equal(got, want) {
			t.Errorf("%s differs\n got: %s\nwant: %s", name, got, want)
		}
	}
}

func TestDigitsAreTakenOnlyAsKeyedDigitsAndTheCallerOnlyAsANumber(t *testing.T) {
	t.Parallel()
	d, err := vobiz.ParseDigits(form(t, "pin.form"))
	if err != nil || d != (vobiz.Digits{CallUUID: "550e8400-e29b-41d4-a716-446655440000", To: "+12025550100", Entered: "48151623"}) {
		t.Fatalf("digits = %+v, %v", d, err)
	}
	for name, change := range map[string]func(map[string][]string){
		"a timeout":       func(f map[string][]string) { f["Digits"] = []string{""} },
		"a star":          func(f map[string][]string) { f["Digits"] = []string{"4815*623"} },
		"speech":          func(f map[string][]string) { f["InputType"] = []string{"speech"} },
		"too many digits": func(f map[string][]string) { f["Digits"] = []string{strings.Repeat("1", 33)} },
	} {
		f := form(t, "pin.form")
		change(f)
		if d, err := vobiz.ParseDigits(f); err != nil || d.Entered != "" || d.CallUUID == "" {
			t.Errorf("%s was taken as a PIN: %+v %v", name, d, err)
		}
	}
	unsigned := form(t, "pin.form")
	unsigned.Set("Direction", "outbound")
	if _, err := vobiz.ParseDigits(unsigned); err == nil || strings.Contains(err.Error(), "48151623") {
		t.Errorf("an outbound leg's digits were taken or echoed: %v", err)
	}

	for from, want := range map[string]string{
		"+12025550143": "+12025550143",
		"12025550143":  "+12025550143",
		" 12025550143": "+12025550143",
		"":             "",
		"anonymous":    "",
		"Restricted":   "",
		"0987654321":   "",
		"sip:a@b.c":    "",
	} {
		f := form(t, "pin.form")
		f.Set("From", from)
		if got := vobiz.Caller(f); got != want {
			t.Errorf("caller %q read as %q, want %q", from, got, want)
		}
	}
}
