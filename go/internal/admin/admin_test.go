package admin_test

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/admin"
	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/configstore"
	"github.com/punk-raven/dafter/go/internal/errs"
)

const (
	token = "admin-token-for-tests"
	asha  = `{"name": "Asha", "aliases": ["आशा"], "nearMisses": ["Usha"], "profile": "support"}`
)

type harness struct {
	server *httptest.Server
	live   *configstore.Live
}

func serve(t *testing.T) *harness {
	t.Helper()
	store, err := configstore.Open(t.Context(), filepath.Join(t.TempDir(), "dafter.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	raw, err := os.ReadFile("../../cmd/dafter-control/catalog.json")
	if err != nil {
		t.Fatal(err)
	}
	catalog, err := config.LoadCatalog(raw)
	if err != nil {
		t.Fatal(err)
	}
	docs, err := catalog.Documents()
	if err != nil {
		t.Fatal(err)
	}
	editor := &configstore.Editor{Store: store}
	if _, _, err := editor.Seed(t.Context(), docs); err != nil {
		t.Fatal(err)
	}
	live, err := configstore.NewLive(t.Context(), store, nil)
	if err != nil {
		t.Fatal(err)
	}
	api, err := admin.New(editor, live, token, nil)
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(api.Handler())
	t.Cleanup(server.Close)
	return &harness{server: server, live: live}
}

func (h *harness) call(t *testing.T, method, path, body string, header map[string]string) (int, []byte) {
	t.Helper()
	req, err := http.NewRequestWithContext(t.Context(), method, h.server.URL+path, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+token)
	for k, v := range header {
		if v == "" {
			req.Header.Del(k)
			continue
		}
		req.Header.Set(k, v)
	}
	resp, err := h.server.Client().Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = resp.Body.Close() }()
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	return resp.StatusCode, raw
}

func decoded[T any](t *testing.T, raw []byte) T {
	t.Helper()
	var v T
	if err := json.Unmarshal(raw, &v); err != nil {
		t.Fatalf("decode %s: %v", raw, err)
	}
	return v
}

func TestEveryRouteNeedsTheAdminToken(t *testing.T) {
	t.Parallel()
	h := serve(t)
	for name, auth := range map[string]string{
		"missing":      "",
		"wrong":        "Bearer not-the-token",
		"wrong scheme": "Basic " + token,
		"a prefix":     "Bearer " + token[:5],
	} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			for _, route := range [][2]string{
				{"GET", "/admin/v1/agents"}, {"POST", "/admin/v1/releases"}, {"PUT", "/admin/v1/agents/asha"},
				{"GET", "/admin/v1/removed/agents"}, {"GET", "/admin/v1/agents/asha/revisions"}, {"POST", "/admin/v1/agents/asha/restore"},
			} {
				status, raw := h.call(t, route[0], route[1], asha, map[string]string{"Authorization": auth})
				e := decoded[errs.Error](t, raw)
				if status != http.StatusUnauthorized || e.Code != errs.CodeAuthenticationFailed {
					t.Errorf("%s %s: %d %s", route[0], route[1], status, raw)
				}
			}
		})
	}
	if _, err := admin.New(nil, nil, "", nil); err == nil {
		t.Error("an admin API without a token was built")
	}
}

func TestAWriteIsRefusedWithEveryProblemLocated(t *testing.T) {
	t.Parallel()
	h := serve(t)
	cases := map[string]struct {
		method, path, body string
		status             int
		pointer            string
	}{
		"a schema problem":   {"PUT", "/admin/v1/agents/asha", `{"name": "", "voice": "priya"}`, 400, "/agents/asha/voice"},
		"a pasted key":       {"PUT", "/admin/v1/tenants/t_9c21a4be", `{"agent": {"pipeline": {"llm": {"provider": "groq", "options": {"apiKey": "gsk_0123456789abcdefghij"}}}}}`, 400, "/tenants/t_9c21a4be/agent/pipeline/llm/options/apiKey"},
		"a broken session":   {"PUT", "/admin/v1/profiles/support", `{"budgets": {"turnGapP95Ms": -1}}`, 400, "/budgets/turnGapP95Ms"},
		"git-owned defaults": {"PUT", "/admin/v1/defaults/defaults", `{}`, 400, "/defaults"},
		"an unknown kind":    {"GET", "/admin/v1/roles", ``, 400, "/roles"},
		"not JSON":           {"PUT", "/admin/v1/profiles/sales", `{"agent":`, 400, ""},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			status, raw := h.call(t, tc.method, tc.path, tc.body, nil)
			e := decoded[errs.Error](t, raw)
			if status != tc.status || e.Code != errs.CodeInvalidConfig {
				t.Fatalf("%d %s", status, raw)
			}
			found := tc.pointer == ""
			for _, d := range append(slices.Clone(e.Details), e.Message) {
				found = found || strings.HasPrefix(d, "at '"+tc.pointer+"'")
			}
			if !found {
				t.Errorf("no problem at %q: %s", tc.pointer, raw)
			}
		})
	}
	if status, _ := h.call(t, "GET", "/admin/v1/agents/nobody", ``, nil); status != http.StatusNotFound {
		t.Errorf("reading a document that is not stored: %d", status)
	}
}

type preview struct {
	Release    int64  `json:"release"`
	ConfigHash string `json:"configHash"`
	Config     struct {
		Agent struct {
			Name       string `json:"name"`
			PersonaRef string `json:"personaRef"`
		} `json:"agent"`
	} `json:"config"`
}

func (h *harness) agentIn(t *testing.T, source string) (int, preview) {
	t.Helper()
	status, raw := h.call(t, "POST", "/admin/v1/preview",
		`{"source": "`+source+`", "tenantId": "t_9c21a4be", "agent": "asha", "language": "hi", "channel": "webrtc"}`, nil)
	if status != http.StatusOK {
		return status, preview{}
	}
	return status, decoded[preview](t, raw)
}

func TestAnAgentIsWrittenPreviewedPublishedAndRolledBack(t *testing.T) {
	t.Parallel()
	h := serve(t)
	who := map[string]string{admin.ActorHeader: "priya@ops", admin.NoteHeader: "add Asha"}
	if status, raw := h.call(t, "PUT", "/admin/v1/agents/asha", asha, who); status != http.StatusOK {
		t.Fatalf("put: %d %s", status, raw)
	}
	_, raw := h.call(t, "GET", "/admin/v1/diff", ``, nil)
	diff := decoded[struct {
		LiveRelease int64 `json:"liveRelease"`
		Changes     []struct {
			Kind, Name, Change string
		} `json:"changes"`
	}](t, raw)
	if diff.LiveRelease != 1 || len(diff.Changes) != 1 || diff.Changes[0].Change != "added" || diff.Changes[0].Name != "asha" {
		t.Fatalf("diff %s", raw)
	}
	if status, p := h.agentIn(t, "draft"); status != http.StatusOK || p.Config.Agent.Name != "Asha" || p.Config.Agent.PersonaRef != "persona://support/v3" {
		t.Fatalf("draft preview: %d %+v", status, p)
	}
	if status, _ := h.agentIn(t, "live"); status != http.StatusBadRequest {
		t.Fatalf("the live release resolved an unpublished agent: %d", status)
	}
	status, raw := h.call(t, "POST", "/admin/v1/releases", ``, who)
	if rel := decoded[configstore.Release](t, raw); status != http.StatusCreated || rel.ID != 2 {
		t.Fatalf("publish: %d %s", status, raw)
	}
	if h.live.Snapshot().Release != 2 {
		t.Fatalf("publishing did not swap the running catalog: release %d", h.live.Snapshot().Release)
	}
	if status, p := h.agentIn(t, "live"); status != http.StatusOK || p.Release != 2 || p.Config.Agent.Name != "Asha" {
		t.Fatalf("live preview after publish: %d %+v", status, p)
	}
	status, raw = h.call(t, "POST", "/admin/v1/releases/1/rollback", ``, map[string]string{admin.ActorHeader: "priya@ops"})
	if rel := decoded[configstore.Release](t, raw); status != http.StatusCreated || rel.ID != 3 || rel.RolledBackFrom != 1 {
		t.Fatalf("rollback: %d %s", status, raw)
	}
	if status, _ := h.agentIn(t, "live"); status != http.StatusBadRequest {
		t.Errorf("the rolled back release still resolves the agent: %d", status)
	}
	_, raw = h.call(t, "GET", "/admin/v1/history?limit=3", ``, nil)
	history := decoded[[]configstore.Change](t, raw)
	actions := []configstore.Action{}
	for _, c := range history {
		actions = append(actions, c.Action)
		if c.Actor != "priya@ops" {
			t.Errorf("change %+v not attributed to the actor header", c)
		}
	}
	if want := []configstore.Action{configstore.ActionRollback, configstore.ActionRestore, configstore.ActionPublish}; !slices.Equal(actions, want) {
		t.Errorf("history %v, want %v", actions, want)
	}
	_, raw = h.call(t, "GET", "/admin/v1/releases", ``, nil)
	if releases := decoded[[]configstore.Release](t, raw); len(releases) != 3 || !releases[0].Live {
		t.Errorf("releases %s", raw)
	}
}

type removedEntry struct {
	Name            string `json:"name"`
	Revision        int64  `json:"revision"`
	DeletedRevision int64  `json:"deletedRevision"`
	DeletedBy       string `json:"deletedBy"`
	Note            string `json:"note"`
	Live            bool   `json:"live"`
}

func TestADeletedDocumentIsListedAsRemovedAndRestoredAsADraft(t *testing.T) {
	t.Parallel()
	h := serve(t)
	who := map[string]string{admin.ActorHeader: "priya@ops"}
	for _, doc := range []string{asha, `{"name": "Asha", "aliases": ["Aasha"], "profile": "support"}`} {
		if status, raw := h.call(t, "PUT", "/admin/v1/agents/asha", doc, who); status != http.StatusOK {
			t.Fatalf("put: %d %s", status, raw)
		}
	}
	h.call(t, "POST", "/admin/v1/releases", ``, who)
	_, raw := h.call(t, "GET", "/admin/v1/agents/asha/revisions", ``, nil)
	if revisions := decoded[[]configstore.Revision](t, raw); len(revisions) != 2 || !strings.Contains(string(revisions[0].Document), "Usha") {
		t.Fatalf("revisions %s", raw)
	}
	if status, raw := h.call(t, "POST", "/admin/v1/agents/asha/restore", ``, nil); status != http.StatusBadRequest {
		t.Fatalf("restoring a stored document: %d %s", status, raw)
	}
	h.call(t, "DELETE", "/admin/v1/agents/asha", ``, map[string]string{admin.ActorHeader: "priya@ops", admin.NoteHeader: "retire"})
	_, raw = h.call(t, "GET", "/admin/v1/removed/agents", ``, nil)
	removed := decoded[[]removedEntry](t, raw)
	if len(removed) != 1 || removed[0].Name != "asha" || removed[0].DeletedBy != "priya@ops" || removed[0].Note != "retire" || !removed[0].Live {
		t.Fatalf("removed %s", raw)
	}
	status, raw := h.call(t, "POST", "/admin/v1/agents/asha/restore", ``, who)
	restored := decoded[configstore.Revision](t, raw)
	if status != http.StatusOK || restored.ID <= removed[0].DeletedRevision || !strings.Contains(string(restored.Document), "Aasha") {
		t.Fatalf("restore: %d %s", status, raw)
	}
	if _, raw = h.call(t, "GET", "/admin/v1/removed/agents", ``, nil); len(decoded[[]removedEntry](t, raw)) != 0 {
		t.Errorf("still removed after a restore: %s", raw)
	}
	_, raw = h.call(t, "GET", "/admin/v1/diff", ``, nil)
	if !strings.Contains(string(raw), `"changes":[]`) {
		t.Errorf("restoring the published revision left a pending change: %s", raw)
	}
	if h.live.Snapshot().Release != 2 {
		t.Errorf("a restore published release %d", h.live.Snapshot().Release)
	}
	for path, want := range map[string]int{
		"/admin/v1/agents/nobody/revisions": http.StatusNotFound,
		"/admin/v1/removed/roles":           http.StatusBadRequest,
	} {
		if status, raw := h.call(t, "GET", path, ``, nil); status != want {
			t.Errorf("GET %s: %d %s", path, status, raw)
		}
	}
}
