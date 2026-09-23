# Cross-language config vector

A realistic resolved session config, spelled the way a merge produces one:
unordered keys, a trailing-zero number, a unicode literal, and its own
`configHash` stamped in. Both halves hash this file and must agree on
`afbac94fda3552ab176ee1fa701f7d9f30023591d92ef2f2aceeea5a32a9d156`, which is
pinned in `go/internal/config/config_test.go` and
`python/dafter_core/tests/test_hashing.py`. An untested half proves nothing, so
neither half may change its hashing without the other failing.

It is a telephony session, which is why its media profile carries no video.
That is the case worth pinning: a channel turns video off by stating it,
because overlays compose by merging and a merge cannot delete a key.

Its egress profile is audio-only for the same reason: the composite video
encode lives on the channels that carry video, so the telephony document
states an audio bitrate and nothing else, and both halves agree that this is
the shape a telephony recording is allowed to have.

Its media profile also states `encryption.mode`, which resolution derives
from `privacyMode` (`open` is `transport`, `sealed` and `trusted_agent` are
`e2ee`) so the hashed document says how the session was encrypted rather than
leaving a reader to infer it. Both halves refuse a document whose stated mode
contradicts its privacy mode.
