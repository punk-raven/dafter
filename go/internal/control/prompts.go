package control

import (
	"sync"
	"time"

	"github.com/punk-raven/dafter/go/internal/errs"
)

type pinPrompt struct {
	attempts int
	expires  time.Time
}

type pinPrompts struct {
	mu     sync.Mutex
	byCall map[string]*pinPrompt
}

func (p *pinPrompts) prune(now time.Time) {
	if p.byCall == nil {
		p.byCall = map[string]*pinPrompt{}
	}
	for uuid, prompt := range p.byCall {
		if now.After(prompt.expires) {
			delete(p.byCall, uuid)
		}
	}
}

func (p *pinPrompts) begin(callUUID string) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	now := time.Now()
	p.prune(now)
	if _, ok := p.byCall[callUUID]; ok {
		return nil
	}
	if len(p.byCall) >= maxHeldCalls {
		return errs.Errorf(errs.CodeRateLimited, "too many callers are keying in a meeting PIN")
	}
	p.byCall[callUUID] = &pinPrompt{expires: now.Add(holdLifetime)}
	return nil
}

func (p *pinPrompts) retry(callUUID string, allowed int) bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	now := time.Now()
	p.prune(now)
	prompt, ok := p.byCall[callUUID]
	if !ok {
		if len(p.byCall) >= maxHeldCalls {
			return false
		}
		prompt = &pinPrompt{expires: now.Add(holdLifetime)}
		p.byCall[callUUID] = prompt
	}
	prompt.attempts++
	if prompt.attempts < allowed {
		return true
	}
	delete(p.byCall, callUUID)
	return false
}

func (p *pinPrompts) done(callUUID string) {
	p.mu.Lock()
	defer p.mu.Unlock()
	delete(p.byCall, callUUID)
}
