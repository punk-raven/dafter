package main

import (
	"embed"
	"net/http"
)

//go:embed admin.html admin.css
//go:embed admin-api.js admin-fields.js admin-diff.js admin-agent.js admin-docs.js admin-publish.js admin-releases.js admin.js
var panelAssets embed.FS

var panelAssetPaths = []string{
	"admin.css",
	"admin-api.js",
	"admin-fields.js",
	"admin-diff.js",
	"admin-agent.js",
	"admin-docs.js",
	"admin-publish.js",
	"admin-releases.js",
	"admin.js",
}

const panelPolicy = "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

func adminHandler(api http.Handler) http.Handler {
	mux := http.NewServeMux()
	mux.Handle("/admin/v1/", api)
	mux.HandleFunc("GET /{$}", func(w http.ResponseWriter, r *http.Request) {
		panelFile(w, r, "admin.html")
	})
	for _, name := range panelAssetPaths {
		mux.HandleFunc("GET /"+name, func(w http.ResponseWriter, r *http.Request) {
			panelFile(w, r, name)
		})
	}
	return mux
}

func panelFile(w http.ResponseWriter, r *http.Request, name string) {
	w.Header().Set("Content-Security-Policy", panelPolicy)
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Referrer-Policy", "no-referrer")
	w.Header().Set("Cache-Control", "no-cache")
	http.ServeFileFS(w, r, panelAssets, name)
}
