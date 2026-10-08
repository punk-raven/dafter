---
paths:
  - "go/internal/control/{call,call_test,inbound,inbound_test,held,meeting,meeting_test}.go"
  - "go/internal/control/meeting_call_test.go"
  - "go/internal/control/{dialin,dialin_test}.go"
  - "go/internal/transport/{sip,sip_test,hangup,hangup_test}.go"
  - "go/internal/transport/trunks*.go"
  - "go/internal/transport/testdata/sip/**"
  - "go/internal/carrier/vobiz/**"
  - "go/internal/state/{dialin,dialin_test}.go"
  - "go/internal/config/telephony*.go"
  - "python/dafter_core/src/dafter_core/telephony.py"
  - "python/dafter_core/tests/test_telephony.py"
  - "schemas/config/v1/telephony.schema.json"
  - "testdata/telephony/**"
  - "python/dafter_runtime/src/dafter_runtime/telephony.py"
  - "python/dafter_runtime/tests/test_{telephony,phone_lines}.py"
  - "go/cmd/dafter-control/trunks.json"
  - "go/cmd/dafter-control/client-{guests,dialin,phone}.js"
  - "go/cmd/dafter-control/{guests,phone}.css"
---

# Rules enforced in code: telephony

Identical on both halves (Go and Python) unless stated. Index:
`.agents/skills/project-context/SKILL.md`.

## Telephony

### Outbound and phone guests

- `POST /sessions/{id}/call/start` with `{"to": "+E.164"}` dials into a stored session on the
  telephony channel, or on any other channel whose `telephony.phoneGuests` is `dial_out` or `both`.
  Once per call; each phone gets its own `p_` id.
- `telephony.phoneGuests`: `off`, `dial_out`, `dial_in` or `both`, chosen by the session creator;
  default `off`; must stay `off` on the telephony channel.
- The trunk is the tenant's phone line (`tenants.<id>.telephony.trunk` in the catalog), never a
  profile's or a session's.
- Resolution drops the telephony block from every session that takes no phone (`dropIdleTelephony`
  in `resolve.go`, which refuses phone tuning in such a session by pointer).
- Code: `go/internal/control/call.go`, `PlaceCall` in `transport/sip.go`, request pinned in
  `go/internal/transport/testdata/sip/`. Calls use an inline trunk from the operator's table
  (`go/cmd/dafter-control/trunks.json`, or `DAFTER_TRUNKS`) that the stored `telephony.trunk` names.
- Every per-account value of a trunk (address, numbers, credentials, inbound settings) is a
  `secret://` ref that resolves only to `<PROVIDER>_SIP_*`. A trunk whose variables are all unset is
  left out; the stack boots without a carrier.
- The phone joins as a minted `p_` id with `hidePhoneNumber`. The number is never stored, logged or
  echoed in an error.
- `POST /sessions/{id}/call/{participantId}/stop` hangs one phone up: `HangUp` in
  `transport/hangup.go` removes the participant only when the room lists it as kind SIP, under a
  per-call `roomAdmin` token on that room (request pinned in `testdata/sip/`).

### Worker on a call

- The worker watches every SIP participant that joins, at any time, waits for its `sip.callStatus`
  `active` (bounded by the ringing timeout from its own join), then says the recording notice, not
  interruptible.
- Telephony channel: the first caller then hears the greeting. A meeting's phone guest hears only
  the notice (`PhoneLines` in `telephony.py`, `recording_notice` in `personas.py`).
- An always-mode session that takes calls never closes on disconnect and relinks to the next person
  (`Relink`).
- Test client: the Create Session form sets Phone guests; the in-meeting Phone guests section and
  tiles are `client-guests.js` and `guests.css`.

### Inbound (Vobiz)

- Inbound never uses LiveKit's dispatch rules (they pick the room and name the caller `sip_` plus
  the number or its hash).
- Vobiz holds the caller in a conference while `POST /telephony/{trunk}/answer` (V3 signature
  required) stores the session and dispatches the agent.
- `.../held/{token}` (authenticated by the token; Vobiz does not sign every callback) dials the held
  caller back by calling `registrar.vobiz.ai:5060` as the Vobiz SIP Endpoint attached to the bridge
  application, with the token as `X-VH-Bridge`, which Vobiz posts to the bridge answer URL as a form
  field.
  - Digest credentials: `VOBIZ_SIP_BRIDGE_USERNAME`/`PASSWORD`. `sip.vobiz.ai` answers
    `404 Trunk Not Found`.
- `.../bridge` joins that leg to the same conference.
- Code: `go/internal/control/inbound.go`; dialect in `go/internal/carrier/vobiz`, forms and XML
  pinned in its `testdata/`.
- Held calls and PIN attempts live in memory.

### Meeting dial-in (`dial_in`/`both`)

- A number in the trunk's `inbound.meetingNumbersRef` (`VOBIZ_SIP_MEETING_NUMBERS`; the rest answer
  the agent) answers with a `Gather` for the meeting PIN.
- The signed `POST /telephony/{trunk}/pin` admits the caller into the running session's own room
  through the same hold, held and bridge path; no new session or dispatch (`control/meeting.go`).
- `telephony.dialIn.callerCheck`: `pin`, `pin_and_number` or `number`. Every failure gets one
  uniform retry until `inbound.pinAttempts` (default 3).
- The control plane mints an 8-digit PIN per dial-in session at create and returns it as `dialIn`
  beside the token only to participants and presenters (`DisclosesDialInTo`), never in the document.
- Allowed numbers come only through `PUT /sessions/{id}/dial-in/numbers`.
- PIN and numbers are stored as HMAC indexes (the PIN also sealed) under keys derived from
  `DAFTER_STATE_KEY` (`state/dialin.go`), deleted once the room closes after opening, or after a day
  unopened (`SweepDialIns`, `control/dialin.go`).
- Test client dial-in fields and line: `client-dialin.js`.
