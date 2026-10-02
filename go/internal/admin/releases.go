package admin

import (
	"net/http"
	"strconv"

	"github.com/punk-raven/dafter/go/internal/configstore"
	"github.com/punk-raven/dafter/go/internal/errs"
)

func (a *API) listReleases(w http.ResponseWriter, r *http.Request) {
	releases, err := a.Editor.Store.Releases(r.Context())
	if err != nil {
		a.fail(w, err)
		return
	}
	if releases == nil {
		releases = []configstore.Release{}
	}
	a.write(w, http.StatusOK, releases)
}

func releaseID(r *http.Request) (int64, error) {
	id, err := strconv.ParseInt(r.PathValue("release"), 10, 64)
	if err != nil || id <= 0 {
		return 0, errs.Errorf(errs.CodeInvalidConfig, "at '/release': a release is numbered from 1")
	}
	return id, nil
}

func (a *API) readRelease(w http.ResponseWriter, r *http.Request) {
	id, err := releaseID(r)
	if err != nil {
		a.fail(w, err)
		return
	}
	rel, err := a.Editor.Store.Release(r.Context(), id)
	if err != nil {
		a.fail(w, err)
		return
	}
	a.write(w, http.StatusOK, rel)
}

func (a *API) publish(w http.ResponseWriter, r *http.Request) {
	who, err := authorOf(r)
	if err != nil {
		a.fail(w, err)
		return
	}
	rel, err := a.Editor.Publish(r.Context(), who.actor, who.note)
	if err != nil {
		a.fail(w, err)
		return
	}
	a.swap(r, rel)
	a.write(w, http.StatusCreated, rel)
}

func (a *API) rollback(w http.ResponseWriter, r *http.Request) {
	id, err := releaseID(r)
	if err != nil {
		a.fail(w, err)
		return
	}
	who, err := authorOf(r)
	if err != nil {
		a.fail(w, err)
		return
	}
	rel, err := a.Editor.Rollback(r.Context(), id, who.actor, who.note)
	if err != nil {
		a.fail(w, err)
		return
	}
	a.swap(r, rel)
	a.write(w, http.StatusCreated, rel)
}

func (a *API) swap(r *http.Request, rel configstore.Release) {
	if a.Live == nil {
		return
	}
	if err := a.Live.Refresh(r.Context()); err != nil {
		a.Log.Error("published release not swapped in here; the poller retries", "release", rel.ID, "error", err)
	}
}

func (a *API) history(w http.ResponseWriter, r *http.Request) {
	limit := 100
	if raw := r.URL.Query().Get("limit"); raw != "" {
		n, err := strconv.Atoi(raw)
		if err != nil || n < 1 || n > 1000 {
			a.fail(w, errs.Errorf(errs.CodeInvalidConfig, "at '/limit': is a whole number from 1 to 1000"))
			return
		}
		limit = n
	}
	changes, err := a.Editor.Store.History(r.Context(), limit)
	if err != nil {
		a.fail(w, err)
		return
	}
	if changes == nil {
		changes = []configstore.Change{}
	}
	a.write(w, http.StatusOK, changes)
}
