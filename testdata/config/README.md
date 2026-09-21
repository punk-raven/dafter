# Cross-language config vector

A realistic resolved session config, spelled the way a merge produces one:
unordered keys, a trailing-zero number, a unicode literal, and its own
`configHash` stamped in. Both halves hash this file and must agree on
`e2cbfc6025890c0d0318fc2f734115aa0de90b27465906981f0124be808997f4`, which is
pinned in `go/internal/config/config_test.go` and
`python/dafter_core/tests/test_hashing.py`. An untested half proves nothing, so
neither half may change its hashing without the other failing.

It is a telephony session, which is why its media profile carries no video.
That is the case worth pinning: a channel turns video off by stating it,
because overlays compose by merging and a merge cannot delete a key.
