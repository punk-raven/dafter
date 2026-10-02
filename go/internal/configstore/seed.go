package configstore

import (
	"bytes"
	"context"
	"errors"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/configcheck"
)

const CatalogActor = "catalog.json"

type Outcome string

const (
	Seeded    Outcome = "seeded"
	Imported  Outcome = "imported"
	Unchanged Outcome = "unchanged"
)

func (e *Editor) Seed(ctx context.Context, catalog config.Documents) (Release, Outcome, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	git := config.Documents{}
	for kind, byName := range catalog {
		for name, doc := range byName {
			compacted, err := compact(doc)
			if err != nil {
				return Release{}, "", err
			}
			git.Set(kind, name, compacted)
		}
	}
	live, err := e.Store.LiveRelease(ctx)
	if errors.Is(err, ErrNoRelease) {
		return e.seed(ctx, git)
	}
	if err != nil {
		return Release{}, "", err
	}
	desired := Documents(live.Revisions)
	for _, kind := range config.GitKinds {
		delete(desired, kind)
		for _, name := range git.Names(kind) {
			desired.Set(kind, name, git[kind][name])
		}
	}
	if sameGitKinds(Documents(live.Revisions), desired) {
		return live, Unchanged, nil
	}
	if _, err := configcheck.All(desired); err != nil {
		return Release{}, "", err
	}
	members, err := e.importGitKinds(ctx, live, desired)
	if err != nil {
		return Release{}, "", err
	}
	rel, err := e.Store.Publish(ctx, Publication{
		Revisions: members, Action: ActionImport, Actor: CatalogActor, Note: "Dafter's defaults changed in git",
	})
	return rel, Imported, err
}

func (e *Editor) seed(ctx context.Context, docs config.Documents) (Release, Outcome, error) {
	if _, err := configcheck.All(docs); err != nil {
		return Release{}, "", err
	}
	heads, err := e.converge(ctx, docs, ActionSeed, CatalogActor, "first start: seeded from catalog.json")
	if err != nil {
		return Release{}, "", err
	}
	rel, err := e.Store.Publish(ctx, Publication{
		Revisions: ids(heads), Action: ActionSeed, Actor: CatalogActor, Note: "first start: seeded from catalog.json",
	})
	return rel, Seeded, err
}

func (e *Editor) importGitKinds(ctx context.Context, live Release, desired config.Documents) ([]int64, error) {
	var members []int64
	liveGit := map[key]Revision{}
	for _, r := range live.Revisions {
		if r.Kind.Editable() {
			members = append(members, r.ID)
			continue
		}
		liveGit[key{r.Kind, r.Name}] = r
	}
	for _, kind := range config.GitKinds {
		for _, name := range desired.Names(kind) {
			doc := desired[kind][name]
			if r, ok := liveGit[key{kind, name}]; ok && bytes.Equal(r.Document, doc) {
				members = append(members, r.ID)
				delete(liveGit, key{kind, name})
				continue
			}
			rev, err := e.Store.Put(ctx, Write{Kind: kind, Name: name, Document: doc, Action: ActionImport, Actor: CatalogActor})
			if err != nil {
				return nil, err
			}
			delete(liveGit, key{kind, name})
			members = append(members, rev.ID)
		}
	}
	for k := range liveGit {
		if _, err := e.Store.Put(ctx, Write{Kind: k.kind, Name: k.name, Action: ActionImport, Actor: CatalogActor}); err != nil {
			return nil, err
		}
	}
	return members, nil
}

func sameGitKinds(a, b config.Documents) bool {
	for _, kind := range config.GitKinds {
		if len(a[kind]) != len(b[kind]) {
			return false
		}
		for name, doc := range a[kind] {
			if other, ok := b[kind][name]; !ok || !bytes.Equal(doc, other) {
				return false
			}
		}
	}
	return true
}
