package config

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"

	"github.com/gowebpki/jcs"

	"github.com/punk-raven/dafter/go/internal/errs"
)

func Canonicalize(raw []byte) ([]byte, error) {
	out, err := jcs.Transform(raw)
	if err != nil {
		return nil, errs.Wrap(errs.CodeInvalidConfig, err, "canonicalize document")
	}
	return out, nil
}

func HashDocument(raw []byte) (string, error) {
	var doc map[string]json.RawMessage
	if err := json.Unmarshal(raw, &doc); err != nil {
		return "", errs.Wrap(errs.CodeInvalidConfig, err, "document is not a JSON object")
	}
	delete(doc, "configHash")

	stripped, err := json.Marshal(doc)
	if err != nil {
		return "", errs.Wrap(errs.CodeInternal, err, "marshal document for hashing")
	}
	canonical, err := Canonicalize(stripped)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(canonical)
	return hex.EncodeToString(sum[:]), nil
}

func Seal(raw []byte) (document []byte, hash string, err error) {
	hash, err = HashDocument(raw)
	if err != nil {
		return nil, "", err
	}
	var doc map[string]json.RawMessage
	if err := json.Unmarshal(raw, &doc); err != nil {
		return nil, "", errs.Wrap(errs.CodeInvalidConfig, err, "document is not a JSON object")
	}
	doc["configHash"] = json.RawMessage(`"` + hash + `"`)

	stamped, err := json.Marshal(doc)
	if err != nil {
		return nil, "", errs.Wrap(errs.CodeInternal, err, "marshal stamped document")
	}
	document, err = Canonicalize(stamped)
	if err != nil {
		return nil, "", err
	}
	return document, hash, nil
}
