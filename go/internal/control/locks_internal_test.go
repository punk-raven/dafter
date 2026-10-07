package control

import (
	"sync"
	"testing"
	"time"
)

func TestOneCallsRecordingWorkNeverWaitsForAnothersAndLeavesNothingBehind(t *testing.T) {
	t.Parallel()
	var l sessionLocks
	slow := l.lock("s_00000001")
	done := make(chan struct{})
	go func() {
		l.lock("s_00000002")()
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("a second call waited for the first call's lock")
	}

	var wg sync.WaitGroup
	order := make(chan int, 2)
	for i := range 2 {
		wg.Go(func() {
			l.lock("s_00000001")()
			order <- i
		})
	}
	time.Sleep(50 * time.Millisecond)
	if len(order) != 0 {
		t.Fatal("the same call's work ran while its lock was held")
	}
	slow()
	wg.Wait()
	if n := l.held(); n != 0 {
		t.Errorf("%d locks are kept after every holder released", n)
	}
}
