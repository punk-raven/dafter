package admin

import (
	"encoding/json"
	"errors"
	"net/http"
	"time"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/configstore"
)

type removedView struct {
	Kind            config.Kind     `json:"kind"`
	Name            string          `json:"name"`
	Revision        int64           `json:"revision"`
	Document        json.RawMessage `json:"document"`
	DeletedRevision int64           `json:"deletedRevision"`
	DeletedAt       time.Time       `json:"deletedAt"`
	DeletedBy       string          `json:"deletedBy"`
	Note            string          `json:"note,omitempty"`
	Live            bool            `json:"live"`
}

func (a *API) listRemoved(w http.ResponseWriter, r *http.Request) {
	kind, err := kindOf(r)
	if err != nil {
		a.fail(w, err)
		return
	}
	trail, err := a.Editor.Store.Revisions(r.Context(), kind)
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
	views := []removedView{}
	for _, gone := range configstore.Removed(trail) {
		_, stillLive := published[kind][gone.Name]
		views = append(views, removedView{
			Kind: kind, Name: gone.Name, Revision: gone.Last.ID, Document: gone.Last.Document,
			DeletedRevision: gone.Deletion.ID, DeletedAt: gone.Deletion.CreatedAt, DeletedBy: gone.Deletion.Actor,
			Note: gone.Deletion.Note, Live: stillLive,
		})
	}
	a.write(w, http.StatusOK, views)
}

func (a *API) listRevisions(w http.ResponseWriter, r *http.Request) {
	kind, err := kindOf(r)
	if err != nil {
		a.fail(w, err)
		return
	}
	trail, err := a.Editor.Store.Revisions(r.Context(), kind)
	if err != nil {
		a.fail(w, err)
		return
	}
	revisions := configstore.Trail(trail, r.PathValue("name"))
	if len(revisions) == 0 {
		a.fail(w, configstore.ErrNotFound)
		return
	}
	a.write(w, http.StatusOK, revisions)
}

func (a *API) restoreDocument(w http.ResponseWriter, r *http.Request) {
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
	rev, err := a.Editor.Restore(r.Context(), kind, r.PathValue("name"), who.actor, who.note)
	if err != nil {
		a.fail(w, err)
		return
	}
	a.write(w, http.StatusOK, rev)
}
