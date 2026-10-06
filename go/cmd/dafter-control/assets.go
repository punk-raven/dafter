package main

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io/fs"
	"log/slog"
	"net/http"
	"strings"
)

const (
	assetVersionParam = "v"
	revalidate        = "no-cache"
	immutable         = "public, max-age=31536000, immutable"
)

func versionedClientPage() ([]byte, error) {
	page := string(testClientHTML)
	for path, name := range clientAssetPaths {
		raw, err := fs.ReadFile(clientAssets, name)
		if err != nil {
			return nil, fmt.Errorf("client asset %s: %w", name, err)
		}
		sum := sha256.Sum256(raw)
		versioned := path + "?" + assetVersionParam + "=" + hex.EncodeToString(sum[:6])
		page = strings.ReplaceAll(page, `"`+path+`"`, `"`+versioned+`"`)
	}
	return []byte(page), nil
}

func serveClient(page []byte) func(http.ResponseWriter, *http.Request) bool {
	return func(w http.ResponseWriter, r *http.Request) bool {
		if r.Method != http.MethodGet {
			return false
		}
		if r.URL.Path == "/" {
			w.Header().Set("Content-Type", "text/html; charset=utf-8")
			w.Header().Set("Cache-Control", revalidate)
			if _, err := w.Write(page); err != nil {
				slog.Error("write test client", "error", err)
			}
			return true
		}
		name, ok := clientAssetPaths[r.URL.Path]
		if !ok {
			return false
		}
		if r.URL.Query().Has(assetVersionParam) {
			w.Header().Set("Cache-Control", immutable)
		} else {
			w.Header().Set("Cache-Control", revalidate)
		}
		http.ServeFileFS(w, r, clientAssets, name)
		return true
	}
}
