# RFC 8785 conformance vectors - vendored verbatim, never edit

From the JSON Canonicalization Scheme reference implementation
(`github.com/cyberphone/json-canonicalization`, `testdata/`), the common ancestor of the
Go (`github.com/gowebpki/jcs`) and Python (`rfc8785`) libraries Dafter uses. Both halves
canonicalize `input/<name>.json` and must produce `expected/<name>.json` byte for byte,
which is what makes the cross-language config hash a checked claim rather than an
assumption that two upstreams agree.

**The exact bytes are the test:** `4.50` not `4.5`, `1E30` not `1e+30`, the escape
`\u0042` not a literal `B`, a combining ring not a precomposed character. A formatter
tidying those erases the rule each one exercises and leaves the check passing while
proving nothing. Do not reformat, do not fix newlines, do not merge them into one
document.

They change only by being re-copied from upstream. Diff `input/` against the reference
suite's `testdata/input` and `expected/` against its `testdata/output`; its
`testdata/outhex/` settles any argument about encoding.
