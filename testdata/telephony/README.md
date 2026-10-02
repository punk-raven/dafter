# Telephony rule vectors

`rules.json` pins what the cross-field rules decide about a session on the
telephony channel, fed to both halves unchanged:
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
