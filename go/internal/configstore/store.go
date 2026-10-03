package configstore

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/punk-raven/dafter/go/internal/config"
)

var (
	ErrNotFound  = errors.New("configstore: no such revision or release")
	ErrNoRelease = errors.New("configstore: nothing has been published")
)

type Action string

const (
	ActionPut      Action = "put"
	ActionDelete   Action = "delete"
	ActionRestore  Action = "restore"
	ActionImport   Action = "import"
	ActionSeed     Action = "seed"
	ActionPublish  Action = "publish"
	ActionRollback Action = "rollback"
)

type Write struct {
	Kind     config.Kind
	Name     string
	Document json.RawMessage
	Action   Action
	Actor    string
	Note     string
}

type Revision struct {
	ID        int64           `json:"revision"`
	Kind      config.Kind     `json:"kind"`
	Name      string          `json:"name"`
	Document  json.RawMessage `json:"document,omitempty"`
	Deleted   bool            `json:"deleted,omitempty"`
	Actor     string          `json:"actor"`
	Note      string          `json:"note,omitempty"`
	CreatedAt time.Time       `json:"createdAt"`
}

type Publication struct {
	Revisions      []int64
	Action         Action
	Actor          string
	Note           string
	RolledBackFrom int64
}

type Release struct {
	ID             int64      `json:"release"`
	Actor          string     `json:"actor"`
	Note           string     `json:"note,omitempty"`
	CreatedAt      time.Time  `json:"createdAt"`
	RolledBackFrom int64      `json:"rolledBackFrom,omitempty"`
	Live           bool       `json:"live"`
	Revisions      []Revision `json:"revisions,omitempty"`
}

type Change struct {
	ID       int64       `json:"id"`
	At       time.Time   `json:"at"`
	Actor    string      `json:"actor"`
	Action   Action      `json:"action"`
	Kind     config.Kind `json:"kind,omitempty"`
	Name     string      `json:"name,omitempty"`
	Revision int64       `json:"revision,omitempty"`
	Release  int64       `json:"release,omitempty"`
	Note     string      `json:"note,omitempty"`
}

type Store interface {
	Put(ctx context.Context, w Write) (Revision, error)
	Heads(ctx context.Context) ([]Revision, error)
	Revisions(ctx context.Context, kind config.Kind) ([]Revision, error)
	Publish(ctx context.Context, p Publication) (Release, error)
	LiveReleaseID(ctx context.Context) (int64, error)
	LiveRelease(ctx context.Context) (Release, error)
	Release(ctx context.Context, id int64) (Release, error)
	Releases(ctx context.Context) ([]Release, error)
	History(ctx context.Context, limit int) ([]Change, error)
	Close() error
}

func Documents(revisions []Revision) config.Documents {
	docs := config.Documents{}
	for _, r := range revisions {
		if !r.Deleted {
			docs.Set(r.Kind, r.Name, r.Document)
		}
	}
	return docs
}
