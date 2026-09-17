package main

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"os/signal"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

func main() {
	target := flag.String("target", "http://127.0.0.1:8080", "control plane URL")
	users := flag.Int("users", 100, "concurrent simulated users")
	duration := flag.Duration("duration", 60*time.Second, "test duration")
	ramp := flag.Duration("ramp", 10*time.Second, "ramp-up period")
	joinsPerSession := flag.Int("joins-per-session", 3, "participants joining each session")
	tenant := flag.String("tenant", "t_9c21a4be", "tenant ID")
	language := flag.String("language", "en-IN", "language code")
	channel := flag.String("channel", "webrtc", "channel")
	flag.Parse()

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	cfg := &loadConfig{
		target:          strings.TrimRight(*target, "/"),
		users:           *users,
		duration:        *duration,
		ramp:            *ramp,
		joinsPerSession: *joinsPerSession,
		tenant:          *tenant,
		language:        *language,
		channel:         *channel,
	}

	stats := runLoad(ctx, cfg)
	printSummary(stats)
}

type loadConfig struct {
	target          string
	users           int
	duration        time.Duration
	ramp            time.Duration
	joinsPerSession int
	tenant          string
	language        string
	channel         string
}

type requestResult struct {
	op       string
	duration time.Duration
	err      error
	status   int
}

type stats struct {
	mu         sync.Mutex
	creates    []time.Duration
	joins      []time.Duration
	errors     int64
	success    int64
	errorCodes map[int]int64
	startTime  time.Time

	liveErrors  atomic.Int64
	liveSuccess atomic.Int64
}

func (s *stats) record(r requestResult) {
	if r.err != nil || r.status >= 400 {
		s.liveErrors.Add(1)
		s.mu.Lock()
		s.errors++
		if r.status > 0 {
			s.errorCodes[r.status]++
		} else {
			s.errorCodes[0]++
		}
		s.mu.Unlock()
		return
	}
	s.liveSuccess.Add(1)
	s.mu.Lock()
	s.success++
	switch r.op {
	case "create":
		s.creates = append(s.creates, r.duration)
	case "join":
		s.joins = append(s.joins, r.duration)
	}
	s.mu.Unlock()
}

func runLoad(ctx context.Context, cfg *loadConfig) *stats {
	client := &http.Client{
		Transport: &http.Transport{
			MaxIdleConnsPerHost: cfg.users * 2,
			MaxIdleConns:        cfg.users * 4,
			IdleConnTimeout:     30 * time.Second,
		},
		Timeout: 10 * time.Second,
	}

	st := &stats{
		errorCodes: make(map[int]int64),
		startTime:  time.Now(),
	}

	var wg sync.WaitGroup
	deadline := time.After(cfg.duration)

	// Reporter goroutine
	wg.Add(1)
	go func() {
		defer wg.Done()
		ticker := time.NewTicker(2 * time.Second)
		defer ticker.Stop()
		var lastSuccess, lastErrors int64
		lastTime := time.Now()
		for {
			select {
			case <-ticker.C:
				now := time.Now()
				curSuccess := st.liveSuccess.Load()
				curErrors := st.liveErrors.Load()
				dt := now.Sub(lastTime).Seconds()
				rps := float64(curSuccess-lastSuccess+curErrors-lastErrors) / dt
				lastSuccess = curSuccess
				lastErrors = curErrors
				lastTime = now

				st.mu.Lock()
				p50c := percentile(st.creates, 0.50)
				p95c := percentile(st.creates, 0.95)
				p99c := percentile(st.creates, 0.99)
				st.mu.Unlock()

				fmt.Fprintf(os.Stderr, "[%6.0fs] rps=%.0f  p50=%.1fms  p95=%.1fms  p99=%.1fms  ok=%d  err=%d\n",
					now.Sub(st.startTime).Seconds(), rps,
					p50c, p95c, p99c,
					curSuccess, curErrors)
			case <-ctx.Done():
				return
			case <-deadline:
				return
			}
		}
	}()

	// Ramp users
	rampInterval := cfg.ramp / time.Duration(cfg.users)
	if rampInterval < time.Millisecond {
		rampInterval = time.Millisecond
	}

	createBody, _ := json.Marshal(map[string]string{
		"tenantId": cfg.tenant,
		"language": cfg.language,
		"channel":  cfg.channel,
	})
	joinBody := []byte(`{"role":"participant"}`)

	userCtx, userCancel := context.WithCancel(ctx)

	for i := 0; i < cfg.users; i++ {
		select {
		case <-deadline:
			break
		case <-ctx.Done():
			break
		default:
		}

		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				select {
				case <-userCtx.Done():
					return
				case <-deadline:
					return
				default:
				}

				sessionID := doCreate(userCtx, client, cfg.target, createBody, st)
				if sessionID == "" {
					continue
				}

				var jwg sync.WaitGroup
				for j := 0; j < cfg.joinsPerSession; j++ {
					jwg.Add(1)
					go func() {
						defer jwg.Done()
						doJoin(userCtx, client, cfg.target, sessionID, joinBody, st)
					}()
				}
				jwg.Wait()
			}
		}()

		if i < cfg.users-1 {
			time.Sleep(rampInterval)
		}
	}

	select {
	case <-deadline:
	case <-ctx.Done():
	}
	userCancel()
	wg.Wait()
	return st
}

type createResponse struct {
	SessionID string `json:"sessionId"`
}

func doCreate(ctx context.Context, client *http.Client, target string, body []byte, st *stats) string {
	reqCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(reqCtx, http.MethodPost, target+"/sessions", bytes.NewReader(body))
	if err != nil {
		st.record(requestResult{op: "create", err: err})
		return ""
	}
	req.Header.Set("Content-Type", "application/json")

	start := time.Now()
	resp, err := client.Do(req)
	elapsed := time.Since(start)
	if err != nil {
		st.record(requestResult{op: "create", duration: elapsed, err: err})
		return ""
	}
	defer resp.Body.Close()

	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		st.record(requestResult{op: "create", duration: elapsed, err: err, status: resp.StatusCode})
		return ""
	}

	st.record(requestResult{op: "create", duration: elapsed, status: resp.StatusCode})
	if resp.StatusCode != http.StatusCreated {
		return ""
	}

	var cr createResponse
	if err := json.Unmarshal(raw, &cr); err != nil {
		return ""
	}
	return cr.SessionID
}

func doJoin(ctx context.Context, client *http.Client, target, sessionID string, body []byte, st *stats) {
	reqCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()

	url := target + "/sessions/" + sessionID + "/join"
	req, err := http.NewRequestWithContext(reqCtx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		st.record(requestResult{op: "join", err: err})
		return
	}
	req.Header.Set("Content-Type", "application/json")

	start := time.Now()
	resp, err := client.Do(req)
	elapsed := time.Since(start)
	if err != nil {
		st.record(requestResult{op: "join", duration: elapsed, err: err})
		return
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, resp.Body)

	st.record(requestResult{op: "join", duration: elapsed, status: resp.StatusCode})
}

func percentile(durations []time.Duration, p float64) float64 {
	n := len(durations)
	if n == 0 {
		return 0
	}
	sorted := make([]time.Duration, n)
	copy(sorted, durations)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i] < sorted[j] })
	idx := int(math.Ceil(p*float64(n))) - 1
	if idx < 0 {
		idx = 0
	}
	if idx >= n {
		idx = n - 1
	}
	return float64(sorted[idx].Microseconds()) / 1000.0
}

func printSummary(st *stats) {
	elapsed := time.Since(st.startTime)
	total := st.success + st.errors

	fmt.Println()
	fmt.Println("=== Load Test Summary ===")
	fmt.Printf("Duration:     %s\n", elapsed.Truncate(time.Second))
	fmt.Printf("Total reqs:   %d\n", total)
	fmt.Printf("Success:      %d (%.1f%%)\n", st.success, pct(st.success, total))
	fmt.Printf("Errors:       %d (%.1f%%)\n", st.errors, pct(st.errors, total))
	fmt.Printf("Avg RPS:      %.1f\n", float64(total)/elapsed.Seconds())
	fmt.Println()

	printLatencies("Create", st.creates)
	printLatencies("Join", st.joins)

	if len(st.errorCodes) > 0 {
		fmt.Println("Errors by status:")
		for code, count := range st.errorCodes {
			if code == 0 {
				fmt.Printf("  network:  %d\n", count)
			} else {
				fmt.Printf("  %d:      %d\n", code, count)
			}
		}
		fmt.Println()
	}
}

func printLatencies(name string, durations []time.Duration) {
	if len(durations) == 0 {
		return
	}
	fmt.Printf("%s latency (n=%d):\n", name, len(durations))
	fmt.Printf("  p50:  %.1f ms\n", percentile(durations, 0.50))
	fmt.Printf("  p95:  %.1f ms\n", percentile(durations, 0.95))
	fmt.Printf("  p99:  %.1f ms\n", percentile(durations, 0.99))
	fmt.Println()
}

func pct(n, total int64) float64 {
	if total == 0 {
		return 0
	}
	return float64(n) / float64(total) * 100
}
