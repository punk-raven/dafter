package vobiz

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/xml"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"

	"github.com/punk-raven/dafter/go/internal/errs"
)

const (
	SignatureHeader = "X-Vobiz-Signature-V3"
	NonceHeader     = "X-Vobiz-Signature-V3-Nonce"
	BridgeHeader    = "X-VH-Bridge"
	ContentType     = "application/xml"
)

var (
	callUUIDPattern = regexp.MustCompile(`^[A-Za-z0-9-]{8,64}$`)
	noncePattern    = regexp.MustCompile(`^[0-9]{8,40}$`)
	TokenPattern    = regexp.MustCompile(`^[0-9a-f]{32}$`)
	numberPattern   = regexp.MustCompile(`^\+?[1-9][0-9]{6,14}$`)
)

func Verify(baseURL, key string, h http.Header) (string, bool) {
	signature, nonce := h.Get(SignatureHeader), h.Get(NonceHeader)
	if key == "" || signature == "" || !noncePattern.MatchString(nonce) {
		return "", false
	}
	mac := hmac.New(sha256.New, []byte(key))
	mac.Write([]byte(baseURL + "." + nonce))
	want := base64.StdEncoding.EncodeToString(mac.Sum(nil))
	return nonce, hmac.Equal([]byte(signature), []byte(want))
}

type Answer struct {
	CallUUID string
	To       string
}

func ParseAnswer(form url.Values) (Answer, error) {
	var problems []string
	uuid := form.Get("CallUUID")
	if !callUUIDPattern.MatchString(uuid) {
		problems = append(problems, "at '/CallUUID': is not a call id the carrier could have minted")
	}
	to := form.Get("To")
	if !numberPattern.MatchString(to) {
		problems = append(problems, "at '/To': is not a phone number")
	}
	if form.Get("Direction") != "inbound" {
		problems = append(problems, "at '/Direction': an answer is for an inbound call")
	}
	if len(problems) > 0 {
		return Answer{}, refused(problems)
	}
	return Answer{CallUUID: uuid, To: "+" + strings.TrimPrefix(to, "+")}, nil
}

type Bridge struct {
	CallUUID string
	Token    string
}

func ParseBridge(form url.Values) (Bridge, error) {
	var problems []string
	uuid := form.Get("CallUUID")
	if !callUUIDPattern.MatchString(uuid) {
		problems = append(problems, "at '/CallUUID': is not a call id the carrier could have minted")
	}
	token := form.Get(BridgeHeader)
	if !TokenPattern.MatchString(token) {
		problems = append(problems, "at '/"+BridgeHeader+"': is not a bridge token the control plane could have minted")
	}
	if len(problems) > 0 {
		return Bridge{}, refused(problems)
	}
	return Bridge{CallUUID: uuid, Token: token}, nil
}

func Entered(form url.Values) (string, bool) {
	uuid := form.Get("CallUUID")
	return uuid, form.Get("ConferenceAction") == "enter" && callUUIDPattern.MatchString(uuid)
}

func refused(problems []string) *errs.Error {
	e := errs.Errorf(errs.CodeInvalidConfig, "%d problem(s) with the carrier's webhook", len(problems))
	e.Details = problems
	return e
}

type conference struct {
	StayAlone      bool   `xml:"stayAlone,attr"`
	StartOnEnter   bool   `xml:"startConferenceOnEnter,attr"`
	EndOnExit      bool   `xml:"endConferenceOnExit,attr"`
	Beep           bool   `xml:"beep,attr"`
	TimeLimit      int    `xml:"timeLimit,attr"`
	CallbackURL    string `xml:"callbackUrl,attr,omitempty"`
	CallbackMethod string `xml:"callbackMethod,attr,omitempty"`
	Room           string `xml:",chardata"`
}

type response struct {
	XMLName    xml.Name    `xml:"Response"`
	Conference *conference `xml:"Conference,omitempty"`
	Hangup     *struct{}   `xml:"Hangup,omitempty"`
}

func Hold(room, callbackURL string, limit time.Duration) []byte {
	c := join(room, limit)
	c.CallbackURL, c.CallbackMethod = callbackURL, http.MethodPost
	return render(response{Conference: c})
}

func Join(room string, limit time.Duration) []byte {
	return render(response{Conference: join(room, limit)})
}

func Hangup() []byte {
	return render(response{Hangup: &struct{}{}})
}

func join(room string, limit time.Duration) *conference {
	return &conference{
		StayAlone: true, StartOnEnter: true, EndOnExit: true, Beep: false,
		TimeLimit: int(limit / time.Second), Room: room,
	}
}

func render(r response) []byte {
	body, err := xml.Marshal(r)
	if err != nil {
		return []byte(xml.Header + "<Response><Hangup></Hangup></Response>")
	}
	return append([]byte(xml.Header), body...)
}
