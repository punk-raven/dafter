package admin

import (
	"bytes"
	"encoding/json"
	"errors"
	"net/http"
	"time"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/configstore"
	"github.com/punk-raven/dafter/go/internal/errs"
)

const previewSession = "s_00000000"

type documentView struct {
	Kind         config.Kind     `json:"kind"`
	Name         string          `json:"name"`
	Revision     int64           `json:"revision"`
	Document     json.RawMessage `json:"document"`
	LiveRevision int64           `json:"liveRevision,omitempty"`
	Published    bool            `json:"published"`
	UpdatedAt    time.Time       `json:"updatedAt"`
	UpdatedBy    string          `json:"updatedBy"`
}

func kindOf(r *http.Request) (config.Kind, error) {
	kind := config.Kind(r.PathValue("kind"))
	if !kind.Known() {
		return "", errs.Errorf(errs.CodeInvalidConfig,
			"at '/%s': no such kind; the kinds are agents, tenants, profiles, languages, channels, defaults and llms", r.PathValue("kind"))
	}
	return kind, nil
}

func (a *API) views(r *http.Request, kind config.Kind) ([]documentView, error) {
	heads, _, err := a.Editor.Draft(r.Context())
	if err != nil {
		return nil, err
	}
	live, err := a.Editor.Store.LiveRelease(r.Context())
	if err != nil && !errors.Is(err, configstore.ErrNoRelease) {
		return nil, err
	}
	published := map[string]int64{}
	for _, rev := range live.Revisions {
		if rev.Kind == kind {
			published[rev.Name] = rev.ID
		}
	}
	views := []documentView{}
	for _, rev := range heads {
		if rev.Kind != kind {
			continue
		}
		views = append(views, documentView{
			Kind: kind, Name: rev.Name, Revision: rev.ID, Document: rev.Document,
			LiveRevision: published[rev.Name], Published: published[rev.Name] == rev.ID,
			UpdatedAt: rev.CreatedAt, UpdatedBy: rev.Actor,
		})
	}
	return views, nil
}

func (a *API) listDocuments(w http.ResponseWriter, r *http.Request) {
	kind, err := kindOf(r)
	if err != nil {
		a.fail(w, err)
		return
	}
	views, err := a.views(r, kind)
	if err != nil {
		a.fail(w, err)
		return
	}
	a.write(w, http.StatusOK, views)
}

func (a *API) readDocument(w http.ResponseWriter, r *http.Request) {
	kind, err := kindOf(r)
	if err != nil {
		a.fail(w, err)
		return
	}
	views, err := a.views(r, kind)
	if err != nil {
		a.fail(w, err)
		return
	}
	for _, v := range views {
		if v.Name == r.PathValue("name") {
			a.write(w, http.StatusOK, v)
			return
		}
	}
	a.fail(w, configstore.ErrNotFound)
}

func (a *API) putDocument(w http.ResponseWriter, r *http.Request) {
	a.change(w, r, true)
}

func (a *API) deleteDocument(w http.ResponseWriter, r *http.Request) {
	a.change(w, r, false)
}

func (a *API) change(w http.ResponseWriter, r *http.Request, put bool) {
	kind, err := kindOf(r)
	if err != nil {
		a.fail(w, err)
		return
	}
	who, err := authorOf(r)
	if err != nil {
		a.fail(w, err)
		return
	}
	var rev configstore.Revision
	if put {
		var body []byte
		if body, err = readBody(w, r); err == nil {
			rev, err = a.Editor.Put(r.Context(), kind, r.PathValue("name"), body, who.actor, who.note)
		}
	} else {
		rev, err = a.Editor.Delete(r.Context(), kind, r.PathValue("name"), who.actor, who.note)
	}
	if err != nil {
		a.fail(w, err)
		return
	}
	a.write(w, http.StatusOK, rev)
}

type change struct {
	Kind   config.Kind     `json:"kind"`
	Name   string          `json:"name"`
	Change string          `json:"change"`
	Live   json.RawMessage `json:"live,omitempty"`
	Draft  json.RawMessage `json:"draft,omitempty"`
}

type diffView struct {
	LiveRelease int64    `json:"liveRelease"`
	Changes     []change `json:"changes"`
}

func (a *API) diff(w http.ResponseWriter, r *http.Request) {
	_, draft, err := a.Editor.Draft(r.Context())
	if err != nil {
		a.fail(w, err)
		return
	}
	live, err := a.Editor.Store.LiveRelease(r.Context())
	if err != nil && !errors.Is(err, configstore.ErrNoRelease) {
		a.fail(w, err)
		return
	}
	published := configstore.Documents(live.Revisions)
	view := diffView{LiveRelease: live.ID, Changes: []change{}}
	for _, kind := range append(append([]config.Kind{}, config.GitKinds...), config.EditableKinds...) {
		for _, name := range union(published.Names(kind), draft.Names(kind)) {
			before, after := published[kind][name], draft[kind][name]
			c := change{Kind: kind, Name: name, Live: before, Draft: after}
			switch {
			case before == nil:
				c.Change = "added"
			case after == nil:
				c.Change = "removed"
			case !bytes.Equal(before, after):
				c.Change = "changed"
			default:
				continue
			}
			view.Changes = append(view.Changes, c)
		}
	}
	a.write(w, http.StatusOK, view)
}

func union(a, b []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, list := range [][]string{a, b} {
		for _, s := range list {
			if !seen[s] {
				seen[s] = true
				out = append(out, s)
			}
		}
	}
	return out
}

type previewRequest struct {
	Source    string          `json:"source,omitempty"`
	TenantID  string          `json:"tenantId"`
	Agent     string          `json:"agent,omitempty"`
	Profile   string          `json:"profile,omitempty"`
	Language  string          `json:"language"`
	Channel   config.Channel  `json:"channel"`
	LLM       string          `json:"llm,omitempty"`
	Overrides json.RawMessage `json:"overrides,omitempty"`
}

type previewResponse struct {
	Source     string          `json:"source"`
	Release    int64           `json:"release,omitempty"`
	ConfigHash string          `json:"configHash"`
	Config     json.RawMessage `json:"config"`
}

func (a *API) preview(w http.ResponseWriter, r *http.Request) {
	var req previewRequest
	raw, err := readBody(w, r)
	if err == nil {
		err = decodeStrict(raw, &req)
	}
	if err != nil {
		a.fail(w, err)
		return
	}
	out := previewResponse{Source: req.Source}
	var catalog *config.Catalog
	switch req.Source {
	case "", "draft":
		out.Source = "draft"
		var draft config.Documents
		if _, draft, err = a.Editor.Draft(r.Context()); err == nil {
			catalog, err = config.Assemble(draft)
		}
	case "live":
		snapshot := a.Live.Snapshot()
		catalog, out.Release = snapshot.Catalog, snapshot.Release
	default:
		err = errs.Errorf(errs.CodeInvalidConfig, "at '/source': is draft or live")
	}
	if err != nil {
		a.fail(w, err)
		return
	}
	resolved, err := catalog.Resolve(config.Request{
		SessionID: previewSession, TenantID: req.TenantID, Agent: req.Agent, Profile: req.Profile,
		Language: req.Language, Channel: req.Channel, LLM: req.LLM, Overrides: req.Overrides,
	})
	if err != nil {
		a.fail(w, err)
		return
	}
	out.ConfigHash, out.Config = resolved.Hash, resolved.Document
	a.write(w, http.StatusOK, out)
}
