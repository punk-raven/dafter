package main

import (
	"io"
	"net/http"
	"net/http/httptest"
	"regexp"
	"slices"
	"strings"
	"testing"
)

func panelServer(t *testing.T) (*httptest.Server, *[]string) {
	t.Helper()
	var reached []string
	api := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reached = append(reached, r.Method+" "+r.URL.Path)
		w.WriteHeader(http.StatusTeapot)
	})
	srv := httptest.NewServer(adminHandler(api))
	t.Cleanup(srv.Close)
	return srv, &reached
}

func fetch(t *testing.T, method, url string) (*http.Response, string) {
	t.Helper()
	req, err := http.NewRequestWithContext(t.Context(), method, url, nil)
	if err != nil {
		t.Fatal(err)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = resp.Body.Close() }()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	return resp, string(body)
}

func TestPanelServesThePageWithItsPolicy(t *testing.T) {
	srv, _ := panelServer(t)
	resp, body := fetch(t, http.MethodGet, srv.URL+"/")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("GET / = %d, want 200", resp.StatusCode)
	}
	if got := resp.Header.Get("Content-Type"); !strings.HasPrefix(got, "text/html") {
		t.Errorf("Content-Type = %q, want text/html", got)
	}
	if got := resp.Header.Get("Content-Security-Policy"); got != panelPolicy {
		t.Errorf("Content-Security-Policy = %q, want %q", got, panelPolicy)
	}
	if got := resp.Header.Get("X-Content-Type-Options"); got != "nosniff" {
		t.Errorf("X-Content-Type-Options = %q, want nosniff", got)
	}
	if !strings.Contains(body, `id="main"`) {
		t.Error("the page has no main region")
	}
}

func TestPanelServesEveryAssetThePageLoads(t *testing.T) {
	srv, _ := panelServer(t)
	_, page := fetch(t, http.MethodGet, srv.URL+"/")
	var loaded []string
	for _, m := range regexp.MustCompile(`(?:src|href)="/([^"]+)"`).FindAllStringSubmatch(page, -1) {
		loaded = append(loaded, m[1])
	}
	slices.Sort(loaded)
	want := slices.Clone(panelAssetPaths)
	slices.Sort(want)
	if !slices.Equal(loaded, want) {
		t.Fatalf("the page loads %v, the panel serves %v", loaded, want)
	}
	types := map[string]string{".js": "text/javascript", ".css": "text/css"}
	for _, name := range panelAssetPaths {
		resp, body := fetch(t, http.MethodGet, srv.URL+"/"+name)
		if resp.StatusCode != http.StatusOK || body == "" {
			t.Errorf("GET /%s = %d with %d bytes", name, resp.StatusCode, len(body))
			continue
		}
		if got, want := resp.Header.Get("Content-Type"), types[name[strings.LastIndex(name, "."):]]; !strings.HasPrefix(got, want) {
			t.Errorf("GET /%s Content-Type = %q, want %s", name, got, want)
		}
		if resp.Header.Get("Content-Security-Policy") != panelPolicy {
			t.Errorf("GET /%s has no panel policy", name)
		}
	}
}

func TestPanelSendsOnlyTheAPIPrefixToTheAPI(t *testing.T) {
	srv, reached := panelServer(t)
	if resp, _ := fetch(t, http.MethodPut, srv.URL+"/admin/v1/agents/maya"); resp.StatusCode != http.StatusTeapot {
		t.Errorf("PUT /admin/v1/agents/maya = %d, want the API's answer", resp.StatusCode)
	}
	for _, path := range []string{"/admin.html", "/main.go", "/catalog.json", "/testclient.html", "/admin/", "/nope.js"} {
		if resp, _ := fetch(t, http.MethodGet, srv.URL+path); resp.StatusCode != http.StatusNotFound {
			t.Errorf("GET %s = %d, want 404", path, resp.StatusCode)
		}
	}
	if resp, _ := fetch(t, http.MethodPost, srv.URL+"/"); resp.StatusCode != http.StatusMethodNotAllowed {
		t.Errorf("POST / = %d, want 405", resp.StatusCode)
	}
	if !slices.Equal(*reached, []string{"PUT /admin/v1/agents/maya"}) {
		t.Errorf("the API was reached by %v", *reached)
	}
}

func TestPanelIsNotOnThePublicListener(t *testing.T) {
	for path, name := range clientAssetPaths {
		if strings.HasPrefix(name, "admin") {
			t.Errorf("the public listener serves %s at %s", name, path)
		}
	}
}
