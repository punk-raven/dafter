# Cross-language config vector

A realistic resolved session config, spelled the way a merge produces one:
unordered keys, a trailing-zero number, a unicode literal, and its own
`configHash` stamped in. Both halves hash this file and must agree on
`ef78983a74be44b82029a492662ac14e6ab23ca75f0e40fc977ddb2e01f5c7b3`, which is
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
