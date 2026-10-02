package configstore_test

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/configstore"
)

const (
	tenantID = "t_9c21a4be"
	asha     = `{"name":"Asha","aliases":["आशा"],"nearMisses":["Usha"],"profile":"support"}`
	ashaV2   = `{"name":"Asha","aliases":["आशा","ಆಶಾ"],"nearMisses":["Usha","Isha"],"profile":"support"}`
)

func shipped(t *testing.T) config.Documents {
	t.Helper()
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
	return docs
}

type fixture struct {
	path   string
	store  *configstore.SQLite
	editor *configstore.Editor
}

func seeded(t *testing.T) fixture {
	t.Helper()
	path := filepath.Join(t.TempDir(), "dafter.db")
	store, err := configstore.Open(t.Context(), path)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	t.Cleanup(func() {
		if err := store.Close(); err != nil {
			t.Errorf("close: %v", err)
		}
	})
	f := fixture{path: path, store: store, editor: &configstore.Editor{Store: store}}
	rel, outcome, err := f.editor.Seed(t.Context(), shipped(t))
	if err != nil || outcome != configstore.Seeded || rel.ID != 1 {
		t.Fatalf("seed: release %d outcome %q err %v", rel.ID, outcome, err)
	}
	return f
}

func (f fixture) put(t *testing.T, kind config.Kind, name, doc string) configstore.Revision {
	t.Helper()
	rev, err := f.editor.Put(t.Context(), kind, name, json.RawMessage(doc), "ops@example", "")
	if err != nil {
		t.Fatalf("put %s/%s: %v", kind, name, err)
	}
	return rev
}

func (f fixture) publish(t *testing.T) configstore.Release {
	t.Helper()
	rel, err := f.editor.Publish(t.Context(), "ops@example", "")
	if err != nil {
		t.Fatalf("publish: %v", err)
	}
	return rel
}

func agentName(t *testing.T, snapshot config.Snapshot) string {
	t.Helper()
	res, err := snapshot.Catalog.Resolve(config.Request{
		SessionID: "s_7f3a9c21", TenantID: tenantID, Agent: "asha", Language: "hi", Channel: config.ChannelWebRTC,
	})
	if err != nil {
		return "unresolvable"
	}
	return res.Config.Agent.Name + " " + res.Config.Agent.Addressing.Aliases[len(res.Config.Agent.Addressing.Aliases)-1]
}

func TestFirstStartSeedsTheCatalogAsReleaseOne(t *testing.T) {
	t.Parallel()
	f := seeded(t)
	live, err := f.store.LiveRelease(t.Context())
	if err != nil {
		t.Fatal(err)
	}
	docs := configstore.Documents(live.Revisions)
	want := shipped(t)
	for _, kind := range append(append([]config.Kind{}, config.GitKinds...), config.EditableKinds...) {
		if len(docs[kind]) != len(want[kind]) {
			t.Errorf("%s: %d documents seeded, want %d", kind, len(docs[kind]), len(want[kind]))
		}
	}
	if live.Actor != configstore.CatalogActor || !live.Live {
		t.Errorf("release 1 by %q live %v", live.Actor, live.Live)
	}
	if _, outcome, err := f.editor.Seed(t.Context(), shipped(t)); err != nil || outcome != configstore.Unchanged {
		t.Errorf("a second start: outcome %q err %v, want unchanged", outcome, err)
	}
}

func TestAChangeInGitDefaultsIsImportedWithoutPublishingDrafts(t *testing.T) {
	t.Parallel()
	f := seeded(t)
	draft := f.put(t, config.KindAgents, "asha", asha)
	docs := shipped(t)
	var defaults map[string]any
	if err := json.Unmarshal(docs[config.KindDefaults][config.DefaultsName], &defaults); err != nil {
		t.Fatal(err)
	}
	defaults["budgets"].(map[string]any)["turnGapP50Ms"] = 700
	raw, err := json.Marshal(defaults)
	if err != nil {
		t.Fatal(err)
	}
	docs.Set(config.KindDefaults, config.DefaultsName, raw)
	rel, outcome, err := f.editor.Seed(t.Context(), docs)
	if err != nil || outcome != configstore.Imported || rel.ID != 2 {
		t.Fatalf("import: release %d outcome %q err %v", rel.ID, outcome, err)
	}
	full, err := f.store.Release(t.Context(), rel.ID)
	if err != nil {
		t.Fatal(err)
	}
	for _, r := range full.Revisions {
		if r.ID == draft.ID {
			t.Error("importing git defaults published an operator's unpublished draft")
		}
	}
	if string(configstore.Documents(full.Revisions)[config.KindDefaults][config.DefaultsName]) == "" {
		t.Error("the imported release carries no defaults")
	}
}

func TestDocumentsRoundTripAsAppendOnlyRevisions(t *testing.T) {
	t.Parallel()
	f := seeded(t)
	first := f.put(t, config.KindAgents, "asha", asha)
	second := f.put(t, config.KindAgents, "asha", " {\n \"name\": \"Asha\" } ")
	if second.ID <= first.ID || string(second.Document) != `{"name":"Asha"}` {
		t.Fatalf("revision %d %s after %d", second.ID, second.Document, first.ID)
	}
	_, draft, err := f.editor.Draft(t.Context())
	if err != nil {
		t.Fatal(err)
	}
	if got := string(draft[config.KindAgents]["asha"]); got != `{"name":"Asha"}` {
		t.Errorf("draft holds %s, want the latest revision", got)
	}
	if _, err := f.editor.Delete(t.Context(), config.KindAgents, "asha", "ops@example", "gone"); err != nil {
		t.Fatal(err)
	}
	if _, draft, _ = f.editor.Draft(t.Context()); draft[config.KindAgents]["asha"] != nil {
		t.Error("a deleted document is still in the draft")
	}
	if _, err := f.editor.Put(t.Context(), config.KindDefaults, config.DefaultsName, json.RawMessage(`{}`), "ops", ""); err == nil {
		t.Error("the admin path edited Dafter's git-owned defaults")
	}
	db, err := sql.Open("sqlite", f.path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = db.Close() }()
	for _, stmt := range []string{`UPDATE config_revisions SET actor = 'x'`, `DELETE FROM config_changes`, `DELETE FROM config_releases`} {
		if _, err := db.ExecContext(t.Context(), stmt); err == nil {
			t.Errorf("%s rewrote history", stmt)
		}
	}
}

func TestPublishAndRollbackMakeNewReleasesAndKeepHistory(t *testing.T) {
	t.Parallel()
	f := seeded(t)
	f.put(t, config.KindAgents, "asha", asha)
	v1 := f.publish(t)
	f.put(t, config.KindAgents, "asha", ashaV2)
	v2 := f.publish(t)
	if v1.ID != 2 || v2.ID != 3 {
		t.Fatalf("releases %d and %d, want 2 and 3", v1.ID, v2.ID)
	}
	if _, err := f.editor.Publish(t.Context(), "ops", ""); err == nil {
		t.Error("publishing an unchanged draft made a release")
	}
	back, err := f.editor.Rollback(t.Context(), v1.ID, "ops@example", "Kannada alias misfires")
	if err != nil {
		t.Fatalf("rollback: %v", err)
	}
	if back.ID != 4 || back.RolledBackFrom != v1.ID {
		t.Fatalf("rollback made release %d from %d", back.ID, back.RolledBackFrom)
	}
	full, err := f.store.Release(t.Context(), back.ID)
	if err != nil {
		t.Fatal(err)
	}
	if got := string(configstore.Documents(full.Revisions)[config.KindAgents]["asha"]); got != asha {
		t.Errorf("rolled back release holds %s, want %s", got, asha)
	}
	if _, draft, _ := f.editor.Draft(t.Context()); string(draft[config.KindAgents]["asha"]) != asha {
		t.Error("the draft still holds the rolled back revision, so the next publish would bring it back")
	}
	releases, err := f.store.Releases(t.Context())
	if err != nil || len(releases) != 4 || !releases[0].Live || releases[1].Live {
		t.Fatalf("releases %+v err %v", releases, err)
	}
	history, err := f.store.History(t.Context(), 0)
	if err != nil {
		t.Fatal(err)
	}
	if history[0].Action != configstore.ActionRollback || history[0].Actor != "ops@example" || history[0].Note != "Kannada alias misfires" {
		t.Errorf("latest change %+v, want the rollback with its actor and note", history[0])
	}
	if _, err := f.editor.Rollback(t.Context(), 99, "ops", ""); !errors.Is(err, configstore.ErrNotFound) {
		t.Errorf("rolling back to a release that does not exist: %v", err)
	}
}

func TestARunningCatalogSwapsInANewReleaseWithoutARestart(t *testing.T) {
	t.Parallel()
	f := seeded(t)
	f.put(t, config.KindAgents, "asha", asha)
	f.publish(t)
	live, err := configstore.NewLive(t.Context(), f.store, nil)
	if err != nil {
		t.Fatal(err)
	}
	if got := agentName(t, live.Snapshot()); got != "Asha आशा" {
		t.Fatalf("live agent %q", got)
	}
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	go live.Watch(ctx, 5*time.Millisecond)
	f.put(t, config.KindAgents, "asha", ashaV2)
	rel := f.publish(t)
	deadline := time.Now().Add(5 * time.Second)
	for live.Snapshot().Release != rel.ID {
		if time.Now().After(deadline) {
			t.Fatalf("the poller never swapped in release %d", rel.ID)
		}
		time.Sleep(5 * time.Millisecond)
	}
	if got := agentName(t, live.Snapshot()); got != "Asha ಆಶಾ" {
		t.Errorf("after the swap the agent resolves as %q", got)
	}
}

func TestAReleaseThatFailsToLoadIsNeverSwappedIn(t *testing.T) {
	t.Parallel()
	f := seeded(t)
	live, err := configstore.NewLive(t.Context(), f.store, nil)
	if err != nil {
		t.Fatal(err)
	}
	before := live.Snapshot()
	broken, err := f.store.Put(t.Context(), configstore.Write{
		Kind: config.KindTenants, Name: tenantID, Document: json.RawMessage(`{"budgets":{"turnGapP50Ms":-5}}`), Actor: "elsewhere",
	})
	if err != nil {
		t.Fatal(err)
	}
	heads, err := f.store.Heads(t.Context())
	if err != nil {
		t.Fatal(err)
	}
	ids := make([]int64, 0, len(heads))
	for _, h := range heads {
		ids = append(ids, h.ID)
	}
	rel, err := f.store.Publish(t.Context(), configstore.Publication{Revisions: ids, Actor: "elsewhere"})
	if err != nil {
		t.Fatal(err)
	}
	if err := live.Refresh(t.Context()); err == nil {
		t.Fatalf("release %d with revision %d loaded", rel.ID, broken.ID)
	}
	if after := live.Snapshot(); after != before {
		t.Errorf("snapshot moved from release %d to %d", before.Release, after.Release)
	}
}
