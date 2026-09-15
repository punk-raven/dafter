# RFC 8785 conformance vectors

Vendored from the JSON Canonicalization Scheme reference implementation
(`github.com/cyberphone/json-canonicalization`, `testdata/`), the common ancestor
of the Go (`github.com/gowebpki/jcs`) and Python (`rfc8785`) libraries Dafter
uses. Both halves canonicalize `input/<name>.json` and must produce
`output/<name>.json` byte for byte, which is what makes the cross-language
config hash a checked claim rather than an assumption about two upstreams.
