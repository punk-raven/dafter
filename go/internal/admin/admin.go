package admin

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"strings"
	"unicode"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/configstore"
	"github.com/punk-raven/dafter/go/internal/errs"
)

const (
	ActorHeader  = "X-Dafter-Actor"
	NoteHeader   = "X-Dafter-Note"
	defaultActor = "admin"
	maxActor     = 64
	maxNote      = 500
	maxBody      = 1 << 20
)

type Releases interface {
	Snapshot() config.Snapshot
	Refresh(ctx context.Context) error
}

type API struct {
	Editor *configstore.Editor
	Live   Releases
	Log    *slog.Logger

	token [sha256.Size]byte
}

func New(editor *configstore.Editor, live Releases, token string, log *slog.Logger) (*API, error) {
	if token == "" {
		return nil, errs.Errorf(errs.CodeInvalidConfig, "the admin API needs an admin token")
	}
	if log == nil {
		log = slog.Default()
	}
	return &API{Editor: editor, Live: live, Log: log, token: sha256.Sum256([]byte(token))}, nil
}

func (a *API) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /admin/v1/diff", a.diff)
	mux.HandleFunc("POST /admin/v1/preview", a.preview)
	mux.HandleFunc("GET /admin/v1/history", a.history)
	mux.HandleFunc("GET /admin/v1/releases", a.listReleases)
	mux.HandleFunc("POST /admin/v1/releases", a.publish)
	mux.HandleFunc("GET /admin/v1/releases/{release}", a.readRelease)
	mux.HandleFunc("POST /admin/v1/releases/{release}/rollback", a.rollback)
	mux.HandleFunc("GET /admin/v1/removed/{kind}", a.listRemoved)
	mux.HandleFunc("GET /admin/v1/{kind}", a.listDocuments)
	mux.HandleFunc("GET /admin/v1/{kind}/{name}/revisions", a.listRevisions)
	mux.HandleFunc("POST /admin/v1/{kind}/{name}/restore", a.restoreDocument)
	mux.HandleFunc("GET /admin/v1/{kind}/{name}", a.readDocument)
	mux.HandleFunc("PUT /admin/v1/{kind}/{name}", a.putDocument)
	mux.HandleFunc("DELETE /admin/v1/{kind}/{name}", a.deleteDocument)
	return a.authenticated(mux)
}

func (a *API) authenticated(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		presented, ok := strings.CutPrefix(r.Header.Get("Authorization"), "Bearer ")
		if !ok || presented == "" {
			a.fail(w, errs.Errorf(errs.CodeAuthenticationFailed, "the admin API needs the admin token as a bearer credential"))
			return
		}
		digest := sha256.Sum256([]byte(presented))
		if subtle.ConstantTimeCompare(digest[:], a.token[:]) != 1 {
			a.fail(w, errs.Errorf(errs.CodeAuthenticationFailed, "the bearer credential is not the admin token"))
			return
		}
		next.ServeHTTP(w, r)
	})
}

type author struct {
	actor, note string
}

func authorOf(r *http.Request) (author, error) {
	actor := strings.TrimSpace(r.Header.Get(ActorHeader))
	if actor == "" {
		actor = defaultActor
	}
	note := strings.TrimSpace(r.Header.Get(NoteHeader))
	if len(actor) > maxActor || strings.ContainsFunc(actor, unicode.IsControl) {
		return author{}, errs.Errorf(errs.CodeInvalidConfig, "the %s header is a free-form name of at most %d bytes", ActorHeader, maxActor)
	}
	if len(note) > maxNote || strings.ContainsFunc(note, unicode.IsControl) {
		return author{}, errs.Errorf(errs.CodeInvalidConfig, "the %s header is one line of at most %d bytes", NoteHeader, maxNote)
	}
	return author{actor: actor, note: note}, nil
}

func readBody(w http.ResponseWriter, r *http.Request) ([]byte, error) {
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxBody))
	if err != nil {
		return nil, errs.Wrap(errs.CodeInvalidConfig, err, "read request body")
	}
	return raw, nil
}

func decodeStrict(raw []byte, into any) error {
	if len(bytes.TrimSpace(raw)) == 0 {
		raw = []byte("{}")
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if err := d.Decode(into); err != nil {
		return errs.Wrap(errs.CodeInvalidConfig, err, "decode request body")
	}
	return nil
}

func (a *API) write(w http.ResponseWriter, status int, body any) {
	var buf bytes.Buffer
	if err := json.NewEncoder(&buf).Encode(body); err != nil {
		a.Log.Error("encode admin response", "error", err)
		http.Error(w, `{"code":"internal","message":"encode response","retryable":false}`, http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if _, err := w.Write(buf.Bytes()); err != nil {
		a.Log.Error("write admin response", "error", err)
	}
}

func (a *API) fail(w http.ResponseWriter, err error) {
	status := http.StatusInternalServerError
	var de *errs.Error
	switch {
	case errors.Is(err, configstore.ErrNotFound), errors.Is(err, configstore.ErrNoRelease):
		de, status = errs.Wrap(errs.CodeInvalidConfig, err, "no such release or document"), http.StatusNotFound
	case errors.As(err, &de):
		status = statusFor(de.Code)
	default:
		de = errs.Wrap(errs.CodeInternal, err, "request failed")
	}
	a.Log.Warn("admin request rejected", "code", de.Code, "details", de.Details)
	a.write(w, status, de)
}

func statusFor(code errs.ErrorCode) int {
	switch code {
	case errs.CodeInvalidConfig, errs.CodeUnsupportedCapability, errs.CodeConsentRequired,
		errs.CodePrivacyModeForbids, errs.CodeResidencyViolation:
		return http.StatusBadRequest
	case errs.CodeAuthenticationFailed:
		return http.StatusUnauthorized
	default:
		return http.StatusInternalServerError
	}
}
