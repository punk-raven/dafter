package transport

import (
	"time"

	"github.com/punk-raven/dafter/go/internal/config"
)

type Grant struct {
	Room     string
	Identity string
	Role     config.Role
	TTL      time.Duration
}

type Token struct {
	JWT       string
	URL       string
	ExpiresAt time.Time
}

type Transport interface {
	MintToken(Grant) (Token, error)
}
