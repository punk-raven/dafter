package control

import (
	"crypto/rand"
	"encoding/hex"
	"sync"
	"time"

	"github.com/punk-raven/dafter/go/internal/carrier/vobiz"
	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/transport"
)

const (
	holdLifetime  = 10 * time.Minute
	nonceLifetime = 10 * time.Minute
	maxHeldCalls  = 1024
)

type heldCall struct {
	callUUID  string
	sessionID string
	room      string
	identity  string
	trunk     string
	token     string
	ringing   time.Duration
	limit     time.Duration
	dialed    bool
	bridged   bool
	expires   time.Time
}

type nonceUse struct {
	callUUID string
	expires  time.Time
}

type heldCalls struct {
	mu      sync.Mutex
	byCall  map[string]*heldCall
	byToken map[string]*heldCall
	nonces  map[string]nonceUse
}

func mintBridgeToken() (string, error) {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return "", errs.Wrap(errs.CodeInternal, err, "mint bridge token")
	}
	return hex.EncodeToString(b), nil
}

func (h *heldCalls) prune(now time.Time) {
	if h.byCall == nil {
		h.byCall, h.byToken, h.nonces = map[string]*heldCall{}, map[string]*heldCall{}, map[string]nonceUse{}
	}
	for uuid, c := range h.byCall {
		if now.After(c.expires) {
			delete(h.byCall, uuid)
			delete(h.byToken, c.token)
		}
	}
	for nonce, use := range h.nonces {
		if now.After(use.expires) {
			delete(h.nonces, nonce)
		}
	}
}

func (h *heldCalls) fresh(nonce, callUUID string) bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	now := time.Now()
	h.prune(now)
	if use, seen := h.nonces[nonce]; seen && use.callUUID != callUUID {
		return false
	}
	h.nonces[nonce] = nonceUse{callUUID: callUUID, expires: now.Add(nonceLifetime)}
	return true
}

func (h *heldCalls) answered(callUUID string) (heldCall, bool) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.prune(time.Now())
	c, ok := h.byCall[callUUID]
	if !ok {
		return heldCall{}, false
	}
	return *c, true
}

func (h *heldCalls) hold(c heldCall) error {
	h.mu.Lock()
	defer h.mu.Unlock()
	now := time.Now()
	h.prune(now)
	if len(h.byCall) >= maxHeldCalls {
		return errs.Errorf(errs.CodeRateLimited, "too many inbound calls are waiting for the agent")
	}
	c.expires = now.Add(holdLifetime)
	h.byCall[c.callUUID], h.byToken[c.token] = &c, &c
	return nil
}

func (h *heldCalls) dial(token, callUUID string) (heldCall, bool) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.prune(time.Now())
	c, ok := h.byToken[token]
	if !ok || c.callUUID != callUUID || c.dialed {
		return heldCall{}, false
	}
	c.dialed = true
	return *c, true
}

func (h *heldCalls) redial(token string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if c, ok := h.byToken[token]; ok {
		c.dialed = false
	}
}

func (h *heldCalls) bridge(token string) (heldCall, bool) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.prune(time.Now())
	c, ok := h.byToken[token]
	if !ok || !c.dialed || c.bridged {
		return heldCall{}, false
	}
	c.bridged = true
	return *c, true
}

func (h *heldCalls) known(token string) bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.prune(time.Now())
	_, ok := h.byToken[token]
	return ok
}

func (c heldCall) dialBack(t transport.Trunk) transport.PhoneCall {
	return transport.PhoneCall{
		Room:            c.room,
		Identity:        c.identity,
		SIPUser:         t.Inbound.BridgeUser,
		Headers:         map[string]string{vobiz.BridgeHeader: c.token},
		Trunk:           transport.Trunk{Provider: t.Provider, Address: t.Inbound.BridgeHost, Transport: t.Transport, Numbers: t.Numbers},
		RingingTimeout:  c.ringing,
		MaxCallDuration: c.limit,
	}
}
