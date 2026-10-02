package config_test

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
)

const (
	testdataDir    = "../../../testdata"
	rfc8785Vectors = testdataDir + "/rfc8785"
	sharedConfig   = testdataDir + "/config/resolved-session-config.json"
)

func TestCanonicalizeMatchesTheReferenceVectors(t *testing.T) {
	t.Parallel()
	inputs, err := filepath.Glob(filepath.Join(rfc8785Vectors, "input", "*.json"))
	if err != nil || len(inputs) == 0 {
		t.Fatalf("no conformance vectors at %s: %v", rfc8785Vectors, err)
	}
	for _, in := range inputs {
		t.Run(filepath.Base(in), func(t *testing.T) {
			t.Parallel()
			raw, err := os.ReadFile(in)
			if err != nil {
				t.Fatal(err)
			}
			want, err := os.ReadFile(filepath.Join(rfc8785Vectors, "expected", filepath.Base(in)))
			if err != nil {
				t.Fatal(err)
			}
			got, err := config.Canonicalize(raw)
			if err != nil {
				t.Fatalf("canonicalize: %v", err)
			}
			if !bytes.Equal(got, want) {
				t.Errorf("canonicalization differs from the reference\n got: %q\nwant: %q", got, want)
			}
		})
	}
}

func TestResolveStampsTheDocumentWithItsOwnHash(t *testing.T) {
	t.Parallel()
	res := resolve(t, request())

	if res.Config.ConfigHash != res.Hash {
		t.Fatalf("stamped %q but reported %q", res.Config.ConfigHash, res.Hash)
	}
	recomputed, err := config.HashDocument(res.Document)
	if err != nil {
		t.Fatal(err)
	}
	if recomputed != res.Hash {
		t.Errorf("a reader recomputes %s, not the stamped %s; the stored hash is unverifiable", recomputed, res.Hash)
	}
	if canonical, err := config.Canonicalize(res.Document); err != nil || !bytes.Equal(canonical, res.Document) {
		t.Errorf("the stored document is not already canonical: %v", err)
	}
}

func TestHashIgnoresInputSpellingButNotContent(t *testing.T) {
	t.Parallel()
	const spaced = `{"b": 1.0,  "a": [2, {"d": "ö", "c": null}]}`
	const reordered = "{\"a\":[2,{\"c\":null,\"d\":\"ö\"}],\n\"b\":1}"
	const changed = `{"a":[2,{"c":null,"d":"ö"}],"b":2}`

	first, err := config.HashDocument([]byte(spaced))
	if err != nil {
		t.Fatal(err)
	}
	second, err := config.HashDocument([]byte(reordered))
	if err != nil {
		t.Fatal(err)
	}
	if first != second {
		t.Errorf("key order, whitespace or unicode escaping changed the hash: %s vs %s", first, second)
	}
	other, err := config.HashDocument([]byte(changed))
	if err != nil {
		t.Fatal(err)
	}
	if other == first {
		t.Error("a changed value did not change the hash")
	}
}

func TestResolvedConfigHashIsPinnedAcrossBothHalves(t *testing.T) {
	t.Parallel()
	raw, err := os.ReadFile(sharedConfig)
	if err != nil {
		t.Fatal(err)
	}
	const want = "afbac94fda3552ab176ee1fa701f7d9f30023591d92ef2f2aceeea5a32a9d156"
	got, err := config.HashDocument(raw)
	if err != nil {
		t.Fatal(err)
	}
	if got != want {
		t.Errorf("hash of the shared vector is %s, want %s", got, want)
	}
	if _, err := config.Parse(raw); err != nil {
		t.Errorf("the shared vector is not a valid resolved config: %v", err)
	}
}

const transcriptVersionHash = "4010587f3b7e5c5efc778288684a658a53c3266c6a572909d1b157b1d44efe9a"

func TestTranscriptHashIsPinnedAcrossBothHalves(t *testing.T) {
	t.Parallel()
	raw, err := os.ReadFile(testdataDir + "/events/transcript-version-created.json")
	if err != nil {
		t.Fatal(err)
	}
	var envelope struct {
		Payload json.RawMessage `json:"payload"`
	}
	if err := json.Unmarshal(raw, &envelope); err != nil {
		t.Fatal(err)
	}
	got, err := config.HashWithout(envelope.Payload, "transcriptHash")
	if err != nil {
		t.Fatal(err)
	}
	if got != transcriptVersionHash {
		t.Fatalf("transcript hash %s, pinned %s", got, transcriptVersionHash)
	}
	var payload struct {
		TranscriptHash string `json:"transcriptHash"`
	}
	if err := json.Unmarshal(envelope.Payload, &payload); err != nil {
		t.Fatal(err)
	}
	if payload.TranscriptHash != got {
		t.Errorf("the vector states %s, and hashes to %s", payload.TranscriptHash, got)
	}
}
