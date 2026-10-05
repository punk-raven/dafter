package vobiz

import (
	"encoding/xml"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"
)

const (
	PromptLanguage   = "en-GB"
	PromptVoice      = "WOMAN"
	pinTimeoutSecs   = 15
	digitGapSecs     = "5"
	finishOnKey      = "#"
	askForPIN        = "Welcome. Please enter your meeting PIN, followed by the hash key."
	askAgainForPIN   = "That PIN did not open a meeting in progress. Please enter your meeting PIN again, followed by the hash key."
	noPINHeard       = "We did not receive a PIN. Goodbye."
	joiningMeeting   = "Thank you. Joining the meeting now."
	notJoinedMeeting = "Sorry, we could not join you to a meeting. Goodbye."
)

var digitsPattern = regexp.MustCompile(`^[0-9]{1,32}$`)

type speak struct {
	XMLName  xml.Name `xml:"Speak"`
	Voice    string   `xml:"voice,attr"`
	Language string   `xml:"language,attr"`
	Text     string   `xml:",chardata"`
}

type gather struct {
	XMLName          xml.Name `xml:"Gather"`
	Action           string   `xml:"action,attr"`
	Method           string   `xml:"method,attr"`
	InputType        string   `xml:"inputType,attr"`
	NumDigits        int      `xml:"numDigits,attr"`
	FinishOnKey      string   `xml:"finishOnKey,attr"`
	ExecutionTimeout int      `xml:"executionTimeout,attr"`
	DigitEndTimeout  string   `xml:"digitEndTimeout,attr"`
	Redirect         bool     `xml:"redirect,attr"`
	Prompt           speak
}

func say(text string) speak {
	return speak{Voice: PromptVoice, Language: PromptLanguage, Text: text}
}

func AskForPIN(actionURL string, digits int, again bool) []byte {
	prompt := askForPIN
	if again {
		prompt = askAgainForPIN
	}
	return render(gather{
		Action: actionURL, Method: http.MethodPost, InputType: "dtmf", NumDigits: digits,
		FinishOnKey: finishOnKey, ExecutionTimeout: pinTimeoutSecs, DigitEndTimeout: digitGapSecs,
		Redirect: true, Prompt: say(prompt),
	}, say(noPINHeard), hangup{})
}

func Admit(room, callbackURL string, limit time.Duration) []byte {
	return render(say(joiningMeeting), hold(room, callbackURL, limit))
}

func Refuse() []byte {
	return render(say(notJoinedMeeting), hangup{})
}

type Digits struct {
	CallUUID string
	To       string
	Entered  string
}

func ParseDigits(form url.Values) (Digits, error) {
	answer, err := ParseAnswer(form)
	if err != nil {
		return Digits{}, err
	}
	d := Digits{CallUUID: answer.CallUUID, To: answer.To}
	if entered := form.Get("Digits"); form.Get("InputType") == "dtmf" && digitsPattern.MatchString(entered) {
		d.Entered = entered
	}
	return d, nil
}

func Caller(form url.Values) string {
	from := strings.TrimSpace(form.Get("From"))
	if !numberPattern.MatchString(from) {
		return ""
	}
	return "+" + strings.TrimPrefix(from, "+")
}
