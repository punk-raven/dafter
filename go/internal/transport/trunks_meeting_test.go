package transport_test

import (
	"errors"
	"maps"
	"slices"
	"strconv"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/transport"
)

func vobizWith(extra map[string]string) func(string) string {
	vars := maps.Clone(vobizEnv)
	maps.Copy(vars, extra)
	return environment(vars)
}

func problems(err error) string {
	var de *errs.Error
	if !errors.As(err, &de) {
		return ""
	}
	return strings.Join(de.Details, "\n")
}

func TestEachVobizNumberAnswersTheAgentOrTheMeetingsAsTheOperatorSets(t *testing.T) {
	t.Parallel()
	for name, c := range map[string]struct {
		meetings       string
		agent, meeting []string
	}{
		"no meeting numbers":            {"", []string{"+12025550100", "+12025550101"}, nil},
		"one number each":               {"+12025550101", []string{"+12025550100"}, []string{"+12025550101"}},
		"every number answers meetings": {"+12025550100,+12025550101", nil, []string{"+12025550100", "+12025550101"}},
	} {
		trunks, _, err := transport.LoadTrunks(shippedTable(t), vobizWith(map[string]string{"VOBIZ_SIP_MEETING_NUMBERS": c.meetings}))
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		in := trunks["vobiz"].Inbound
		for _, n := range c.agent {
			if in.AnswersMeetings(n) {
				t.Errorf("%s: %s answers meetings", name, n)
			}
		}
		for _, n := range c.meeting {
			if !in.AnswersMeetings(n) {
				t.Errorf("%s: %s does not answer meetings", name, n)
			}
		}
		if !slices.Equal(in.MeetingNumbers, c.meeting) || in.Attempts() != transport.DefaultPINAttempts {
			t.Errorf("%s: meeting numbers %v, attempts %d", name, in.MeetingNumbers, in.Attempts())
		}
	}
}

func TestAMeetingNumberIsTheTrunksOwnAndAMeetingsOnlyTrunkNeedsNoAgentSession(t *testing.T) {
	t.Parallel()
	_, _, err := transport.LoadTrunks(shippedTable(t), vobizWith(map[string]string{"VOBIZ_SIP_MEETING_NUMBERS": "+12025550199"}))
	if !strings.Contains(problems(err), "/vobiz/inbound/meetingNumbersRef") || strings.Contains(problems(err), "2025550199") {
		t.Errorf("a meeting number the trunk does not own was accepted or echoed: %v", err)
	}

	table := func(attempts int) []byte {
		return []byte(`{"vobiz": {"provider": "vobiz", "addressRef": "secret://operator/vobiz/sip-domain",
			"numbersRef": "secret://operator/vobiz/sip-numbers",
			"inbound": {"publicUrlRef": "secret://operator/vobiz/sip-webhook-url", "signingKeyRef": "secret://operator/vobiz/sip-auth-token",
				"bridgeHost": "sip.vobiz.ai", "bridgeUserRef": "secret://operator/vobiz/sip-bridge-app",
				"meetingNumbersRef": "secret://operator/vobiz/sip-meeting-numbers", "pinAttempts": ` + strconv.Itoa(attempts) + `}}}`)
	}
	every := vobizWith(map[string]string{"VOBIZ_SIP_MEETING_NUMBERS": "+12025550100,+12025550101"})
	trunks, _, err := transport.LoadTrunks(table(5), every)
	if err != nil || trunks["vobiz"].Inbound.Attempts() != 5 {
		t.Fatalf("a meetings-only trunk without an agent session: %v", err)
	}
	if _, _, err := transport.LoadTrunks(table(0), vobizWith(map[string]string{"VOBIZ_SIP_MEETING_NUMBERS": "+12025550101"})); !strings.Contains(problems(err), "/vobiz/inbound/session") {
		t.Errorf("a number answering the agent with no session to open was accepted: %v", err)
	}
	for _, attempts := range []int{-1, 6} {
		if _, _, err := transport.LoadTrunks(table(attempts), every); !strings.Contains(problems(err), "/vobiz/inbound/pinAttempts") {
			t.Errorf("%d PIN attempts accepted: %v", attempts, err)
		}
	}
	bad := vobizWith(map[string]string{"VOBIZ_SIP_MEETING_NUMBERS": "+12025550100"})
	if _, _, err := transport.LoadTrunks([]byte(strings.Replace(string(table(0)), "sip-meeting-numbers", "meeting-numbers", 1)), bad); !strings.Contains(problems(err), "/vobiz/inbound/meetingNumbersRef") {
		t.Errorf("a meeting numbers reference outside the trunk's SIP settings was accepted: %v", err)
	}
}
