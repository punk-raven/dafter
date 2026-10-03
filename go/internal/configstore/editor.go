package configstore

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"slices"
	"sync"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/configcheck"
	"github.com/punk-raven/dafter/go/internal/errs"
)

type Editor struct {
	Store Store

	mu sync.Mutex
}

type key struct {
	kind config.Kind
	name string
}

func (e *Editor) Draft(ctx context.Context) ([]Revision, config.Documents, error) {
	heads, err := e.Store.Heads(ctx)
	if err != nil {
		return nil, nil, err
	}
	return heads, Documents(heads), nil
}

func (e *Editor) Put(ctx context.Context, kind config.Kind, name string, document json.RawMessage, actor, note string) (Revision, error) {
	if document == nil {
		return Revision{}, errs.Errorf(errs.CodeInvalidConfig, "a stored document is a JSON value, never empty")
	}
	return e.write(ctx, kind, name, document, actor, note)
}

func (e *Editor) Delete(ctx context.Context, kind config.Kind, name, actor, note string) (Revision, error) {
	return e.write(ctx, kind, name, nil, actor, note)
}

func (e *Editor) Restore(ctx context.Context, kind config.Kind, name, actor, note string) (Revision, error) {
	if err := editable(kind); err != nil {
		return Revision{}, err
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	trail, err := e.Store.Revisions(ctx, kind)
	if err != nil {
		return Revision{}, err
	}
	for _, r := range Removed(trail) {
		if r.Name == name {
			return e.apply(ctx, Write{Kind: kind, Name: name, Document: r.Last.Document, Action: ActionRestore, Actor: actor, Note: note})
		}
	}
	return Revision{}, errs.Errorf(errs.CodeInvalidConfig,
		"at '/%s/%s': is not a removed document; only a deleted document is restored", kind, name)
}

func editable(kind config.Kind) error {
	if kind.Editable() {
		return nil
	}
	return errs.Errorf(errs.CodeInvalidConfig,
		"at '/%s': is Dafter's own default, kept in git as catalog.json and imported on start", kind)
}

func (e *Editor) write(ctx context.Context, kind config.Kind, name string, document json.RawMessage, actor, note string) (Revision, error) {
	if err := editable(kind); err != nil {
		return Revision{}, err
	}
	if document != nil {
		var err error
		if document, err = compact(document); err != nil {
			return Revision{}, err
		}
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.apply(ctx, Write{Kind: kind, Name: name, Document: document, Actor: actor, Note: note})
}

func (e *Editor) apply(ctx context.Context, w Write) (Revision, error) {
	_, draft, err := e.Draft(ctx)
	if err != nil {
		return Revision{}, err
	}
	if _, err := configcheck.Write(draft, configcheck.Change{Kind: w.Kind, Name: w.Name, Document: w.Document}); err != nil {
		return Revision{}, err
	}
	return e.Store.Put(ctx, w)
}

func (e *Editor) Publish(ctx context.Context, actor, note string) (Release, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	heads, draft, err := e.Draft(ctx)
	if err != nil {
		return Release{}, err
	}
	live, err := e.Store.LiveRelease(ctx)
	if err != nil && !errors.Is(err, ErrNoRelease) {
		return Release{}, err
	}
	if err == nil && sameRevisions(live.Revisions, heads) {
		return Release{}, errs.Errorf(errs.CodeInvalidConfig, "nothing to publish: the stored documents match the live release")
	}
	if _, err := configcheck.All(draft); err != nil {
		return Release{}, err
	}
	return e.Store.Publish(ctx, Publication{Revisions: ids(heads), Actor: actor, Note: note})
}

func (e *Editor) Rollback(ctx context.Context, to int64, actor, note string) (Release, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	target, err := e.Store.Release(ctx, to)
	if err != nil {
		return Release{}, err
	}
	live, err := e.Store.LiveRelease(ctx)
	if err != nil {
		return Release{}, err
	}
	desired := Documents(target.Revisions)
	for _, kind := range config.GitKinds {
		delete(desired, kind)
	}
	for _, r := range live.Revisions {
		if !r.Kind.Editable() {
			desired.Set(r.Kind, r.Name, r.Document)
		}
	}
	if _, err := configcheck.All(desired); err != nil {
		return Release{}, err
	}
	heads, err := e.converge(ctx, desired, ActionRestore, actor, note)
	if err != nil {
		return Release{}, err
	}
	return e.Store.Publish(ctx, Publication{
		Revisions: ids(heads), Action: ActionRollback, Actor: actor, Note: note, RolledBackFrom: to,
	})
}

func (e *Editor) converge(ctx context.Context, desired config.Documents, action Action, actor, note string) ([]Revision, error) {
	heads, err := e.Store.Heads(ctx)
	if err != nil {
		return nil, err
	}
	current := map[key]Revision{}
	for _, r := range heads {
		current[key{r.Kind, r.Name}] = r
	}
	for _, kind := range append(slices.Clone(config.GitKinds), config.EditableKinds...) {
		for _, name := range desired.Names(kind) {
			doc := desired[kind][name]
			if head, ok := current[key{kind, name}]; ok && bytes.Equal(head.Document, doc) {
				continue
			}
			if _, err := e.Store.Put(ctx, Write{Kind: kind, Name: name, Document: doc, Action: action, Actor: actor, Note: note}); err != nil {
				return nil, err
			}
		}
	}
	for k := range current {
		if _, ok := desired[k.kind][k.name]; !ok {
			if _, err := e.Store.Put(ctx, Write{Kind: k.kind, Name: k.name, Action: action, Actor: actor, Note: note}); err != nil {
				return nil, err
			}
		}
	}
	return e.Store.Heads(ctx)
}

func sameRevisions(a, b []Revision) bool {
	return slices.Equal(sorted(ids(a)), sorted(ids(b)))
}

func ids(revisions []Revision) []int64 {
	out := make([]int64, len(revisions))
	for i, r := range revisions {
		out[i] = r.ID
	}
	return out
}

func sorted(ids []int64) []int64 {
	slices.Sort(ids)
	return ids
}

func compact(document json.RawMessage) (json.RawMessage, error) {
	var buf bytes.Buffer
	if err := json.Compact(&buf, document); err != nil {
		return nil, errs.Wrap(errs.CodeInvalidConfig, err, "the document is not JSON")
	}
	return buf.Bytes(), nil
}
