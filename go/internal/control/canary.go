package control

import (
	"crypto/sha256"
	"encoding/binary"

	"github.com/punk-raven/dafter/go/internal/config"
)

const canaryBuckets = 10000

func canaryBucket(candidateVersion, caller string) uint64 {
	sum := sha256.Sum256([]byte(candidateVersion + "\x00" + caller))
	return binary.BigEndian.Uint64(sum[:8]) % canaryBuckets
}

func routeCanary(catalog *config.Catalog, req config.Request, caller string) config.Request {
	route, ok := catalog.CanaryFor(req)
	if !ok {
		return req
	}
	req.Candidate = canaryBucket(route.CandidateVersion, caller)*100 < uint64(route.Percent)*canaryBuckets
	return req
}
