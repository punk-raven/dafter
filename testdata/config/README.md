# Cross-language config vector

A realistic resolved session config, spelled the way a merge produces one:
unordered keys, a trailing-zero number, a unicode literal, and its own
`configHash` stamped in. Both halves hash this file and must agree on
`83e6309ac80b7060f9cffb62db49e7f18a810a36418ce7ebf311d5f5aeb5829e`, which is
pinned in `go/internal/config/config_test.go` and
`python/dafter_core/tests/test_config.py`. An untested half proves nothing, so
neither half may change its hashing without the other failing.
