# Vobiz webhook fixtures

What the control plane reads from and answers to Vobiz on an inbound call,
asserted byte for byte by `go/internal/carrier/vobiz/vobiz_test.go` and fed
to the inbound routes by `go/internal/control/inbound_test.go`. The numbers
are from the fictional 555-01xx range and the ids are made up.

Requests Vobiz sends, form encoded (docs.vobiz.ai/xml/overview/how-it-works,
/applications, /xml/conference/conference-callbacks, /xml/request/sip-headers):

- `answer.form`: the answer URL of the application the Vobiz number is
  attached to, when a caller rings it. `From` is the caller's number, which
  the control plane never reads; `To` is the Vobiz number, sent without its
  plus.
- `conference-enter.form`: the conference callback when the caller has
  entered the hold conference, `ConferenceAction=enter` with the caller's
  `CallUUID`.
- `bridge.form`: the answer URL of the bridge application when the media
  server's SIP call reaches it; the `X-VH-Bridge` header the call carried
  arrives as a form field.

Answers the control plane returns, Vobiz XML:

- `hold.xml`: puts the caller in a conference named after the per-call bridge
  token. `stayAlone` keeps them there alone until the agent's leg arrives
  (it defaults to false, which would hang up a caller alone in a room);
  `endConferenceOnExit` on both legs ends the call for the other side when
  either hangs up; `callbackUrl` carries the token in its path, which is what
  authenticates the callback, because Vobiz does not sign every callback type
  (docs.vobiz.ai/concepts/validating-callbacks). `timeLimit` is the session's
  `telephony.maxCallDurationSeconds`.
- `join.xml`: puts the media server's leg in the same conference.
- `hangup.xml`: ends a call the control plane will not take.

Signatures: `X-Vobiz-Signature-V3` is base64 of HMAC-SHA256 over the callback
URL without its query, a dot and `X-Vobiz-Signature-V3-Nonce`, keyed with the
account Auth Token. The reference value in the test was computed with
Python's `hmac` from the documented formula.
