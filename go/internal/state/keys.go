package state

import (
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"database/sql"
	"encoding/base64"
	"errors"
	"strings"

	"github.com/punk-raven/dafter/go/internal/errs"
)

const sealedKeyPrefix = "v1:"

type KeyCipher struct {
	aead cipher.AEAD
}

func NewKeyCipher(secret []byte) (*KeyCipher, error) {
	if len(secret) != 32 {
		return nil, errs.Errorf(errs.CodeInvalidConfig, "the key that seals session encryption keys at rest must be 32 bytes")
	}
	block, err := aes.NewCipher(secret)
	if err != nil {
		return nil, errs.Wrap(errs.CodeInternal, err, "build session key cipher")
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, errs.Wrap(errs.CodeInternal, err, "build session key cipher")
	}
	return &KeyCipher{aead: aead}, nil
}

func ParseKeyCipher(encoded string) (*KeyCipher, error) {
	secret, err := base64.StdEncoding.DecodeString(strings.TrimSpace(encoded))
	if err != nil {
		return nil, errs.Wrap(errs.CodeInvalidConfig, err, "the key that seals session encryption keys at rest is not base64")
	}
	return NewKeyCipher(secret)
}

func EphemeralKeyCipher() (*KeyCipher, error) {
	secret := make([]byte, 32)
	if _, err := rand.Read(secret); err != nil {
		return nil, errs.Wrap(errs.CodeInternal, err, "mint session key cipher")
	}
	return NewKeyCipher(secret)
}

func (k *KeyCipher) seal(sessionID, key string) (string, error) {
	if key == "" {
		return "", nil
	}
	nonce := make([]byte, k.aead.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return "", errs.Wrap(errs.CodeInternal, err, "seal session encryption key")
	}
	sealed := k.aead.Seal(nonce, nonce, []byte(key), []byte(sessionID))
	return sealedKeyPrefix + base64.RawStdEncoding.EncodeToString(sealed), nil
}

func (k *KeyCipher) open(sessionID, stored string) (string, error) {
	if stored == "" {
		return "", nil
	}
	encoded, ok := strings.CutPrefix(stored, sealedKeyPrefix)
	if !ok {
		return "", errs.Errorf(errs.CodeInternal, "a session encryption key is stored unsealed")
	}
	sealed, err := base64.RawStdEncoding.DecodeString(encoded)
	if err != nil || len(sealed) < k.aead.NonceSize() {
		return "", errs.Errorf(errs.CodeInternal, "a sealed session encryption key is malformed")
	}
	nonce, body := sealed[:k.aead.NonceSize()], sealed[k.aead.NonceSize():]
	plain, err := k.aead.Open(nil, nonce, body, []byte(sessionID))
	if err != nil {
		return "", errs.Errorf(errs.CodeInternal,
			"a session encryption key cannot be opened: it was sealed under another DAFTER_STATE_KEY")
	}
	return string(plain), nil
}

func (k *KeyCipher) sealUnsealedKeys(ctx context.Context, db *sql.DB) error {
	rows, err := db.QueryContext(ctx,
		`SELECT session_id, encryption_key FROM sessions WHERE encryption_key != '' AND encryption_key NOT LIKE ?`,
		sealedKeyPrefix+"%")
	if err != nil {
		return errs.Wrap(errs.CodeInternal, err, "find unsealed session keys")
	}
	unsealed := map[string]string{}
	for rows.Next() {
		var id, key string
		if err := rows.Scan(&id, &key); err != nil {
			return errors.Join(errs.Wrap(errs.CodeInternal, err, "find unsealed session keys"), rows.Close())
		}
		unsealed[id] = key
	}
	if err := errors.Join(rows.Err(), rows.Close()); err != nil {
		return errs.Wrap(errs.CodeInternal, err, "find unsealed session keys")
	}
	for id, key := range unsealed {
		sealed, err := k.seal(id, key)
		if err != nil {
			return err
		}
		if _, err := db.ExecContext(ctx, `UPDATE sessions SET encryption_key = ? WHERE session_id = ?`, sealed, id); err != nil {
			return errs.Wrap(errs.CodeInternal, err, "seal session key at rest")
		}
	}
	return nil
}
