package control

import "sync"

type sessionLock struct {
	mu   sync.Mutex
	refs int
}

type sessionLocks struct {
	mu    sync.Mutex
	locks map[string]*sessionLock
}

func (l *sessionLocks) lock(sessionID string) func() {
	l.mu.Lock()
	if l.locks == nil {
		l.locks = map[string]*sessionLock{}
	}
	entry, ok := l.locks[sessionID]
	if !ok {
		entry = &sessionLock{}
		l.locks[sessionID] = entry
	}
	entry.refs++
	l.mu.Unlock()
	entry.mu.Lock()
	return func() {
		entry.mu.Unlock()
		l.mu.Lock()
		entry.refs--
		if entry.refs == 0 {
			delete(l.locks, sessionID)
		}
		l.mu.Unlock()
	}
}

func (l *sessionLocks) held() int {
	l.mu.Lock()
	defer l.mu.Unlock()
	return len(l.locks)
}

type claims struct {
	mu   sync.Mutex
	held map[string]bool
}

func (c *claims) claim(key string) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.held[key] {
		return false
	}
	if c.held == nil {
		c.held = map[string]bool{}
	}
	c.held[key] = true
	return true
}

func (c *claims) release(key string) {
	c.mu.Lock()
	delete(c.held, key)
	c.mu.Unlock()
}
