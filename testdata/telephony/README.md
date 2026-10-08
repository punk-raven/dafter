# Telephony rule vectors

`rules.json` pins what the cross-field rules decide about a session on the
telephony channel, and about a session on another channel that takes phone
guests, fed to both halves unchanged:
`go/internal/config/telephony_test.go` and
`python/dafter_core/tests/test_telephony.py`.

Each case merges `patch` over `base` key by key at the top level. `notice` and
`trunk` are what a valid document resolves to (the notice defaults to
`always`, an absent trunk reads as the empty string); `rejected` is the error
code of the first broken rule and the pointer of every broken rule, in the
order the rule tables report them.

What the rules say: a phone call cannot be end-to-end encrypted, because the
media server's SIP bridge decodes every frame between the phone network and
the room, so telephony needs privacy mode `open`; and a recorded telephony
session needs the agent, because a person on a phone sees no recording
indicator and only the agent tells them. A `trusted_agent` session on
`webrtc` is not a phone call and passes.

A session on another channel takes phone guests only when its telephony
block says `phoneGuests` `dial_out`, `dial_in` or `both`: the trunk is the tenant's phone line and
says which carrier a call would use, not whether the session wants phones, so
a trunk with `phoneGuests` off admits no phone and changes nothing. Phone
guests need a trunk to be called in on (`/telephony/phoneGuests`), and on the
telephony channel `phoneGuests` must stay off, because the session is the
call itself (`/telephony/phoneGuests`). The same two privacy rules then apply
to a meeting that takes phone guests: an end-to-end encrypted one cannot
(`/telephony/phoneGuests`), and a recorded one needs the agent
(`/agent/enabled`). On the telephony channel the channel rules above report
instead, so a broken phone call is reported once.

Every one of those rules reads "phone guests other than off", so `dial_in`
and `both` are held to each of them exactly as `dial_out` is. A session that
takes dial-in states how a caller is admitted in `dialIn.callerCheck`
(`callerCheck` is what a valid document resolves to, `pin` when unstated); a
`dialIn` block in a session that takes no dial-in (`off`, `dial_out`, or the
telephony channel) admits nobody and is refused at `/telephony/dialIn`.
