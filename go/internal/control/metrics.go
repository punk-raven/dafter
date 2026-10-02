package control

import (
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"

	"github.com/punk-raven/dafter/go/internal/errs"
)

var (
	httpRequestsTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "http_requests_total",
		Help: "Total HTTP requests.",
	}, []string{"method", "path", "status_code"})

	httpRequestDuration = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "http_request_duration_seconds",
		Help:    "HTTP request latency.",
		Buckets: []float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5},
	}, []string{"method", "path"})

	sessionsCreatedTotal = promauto.NewCounter(prometheus.CounterOpts{
		Name: "dafter_sessions_created_total",
		Help: "Total sessions created.",
	})

	sessionsJoinedTotal = promauto.NewCounter(prometheus.CounterOpts{
		Name: "dafter_sessions_joined_total",
		Help: "Total session joins.",
	})

	sessionCreateDuration = promauto.NewHistogram(prometheus.HistogramOpts{
		Name:    "dafter_session_create_duration_seconds",
		Help:    "Session creation latency.",
		Buckets: []float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5},
	})

	sessionJoinDuration = promauto.NewHistogram(prometheus.HistogramOpts{
		Name:    "dafter_session_join_duration_seconds",
		Help:    "Session join latency.",
		Buckets: []float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5},
	})

	agentDispatchesTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "dafter_agent_dispatches_total",
		Help: "Agent dispatches by outcome.",
	}, []string{"outcome"})

	errorsTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "dafter_errors_total",
		Help: "Total errors by code.",
	}, []string{"code"})
)

func incDispatch(ok bool) {
	outcome := "failed"
	if ok {
		outcome = "dispatched"
	}
	agentDispatchesTotal.WithLabelValues(outcome).Inc()
}

func incRecall(n int) {
	agentDispatchesTotal.WithLabelValues("recalled").Add(float64(n))
}

func incError(code errs.ErrorCode) {
	errorsTotal.WithLabelValues(string(code)).Inc()
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (r *statusRecorder) WriteHeader(code int) {
	r.status = code
	r.ResponseWriter.WriteHeader(code)
}

func (s *Service) MetricsHandler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /sessions", s.metricsCreateSession)
	mux.HandleFunc("GET /sessions/{sessionID}", s.readSession)
	mux.HandleFunc("POST /sessions/{sessionID}/join", s.metricsJoinSession)
	mux.HandleFunc("POST /sessions/{sessionID}/recording/start", s.startRecording)
	mux.HandleFunc("POST /sessions/{sessionID}/recording/stop", s.stopRecording)
	mux.HandleFunc("POST /sessions/{sessionID}/agent/start", s.inviteAgent)
	mux.HandleFunc("POST /sessions/{sessionID}/agent/stop", s.removeAgent)
	mux.HandleFunc("POST /sessions/{sessionID}/agent/key", s.agentKey)
	mux.HandleFunc("POST /sessions/{sessionID}/agent/refusal", s.agentRefusal)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		start := time.Now()
		mux.ServeHTTP(rec, r)
		elapsed := time.Since(start).Seconds()
		path := normalizePath(r.URL.Path)
		httpRequestsTotal.WithLabelValues(r.Method, path, strconv.Itoa(rec.status)).Inc()
		httpRequestDuration.WithLabelValues(r.Method, path).Observe(elapsed)
	})
}

func normalizePath(p string) string {
	if p == "/sessions" {
		return "/sessions"
	}
	if !strings.HasPrefix(p, "/sessions/") {
		return p
	}
	for _, suffix := range []string{"/join", "/recording/start", "/recording/stop", "/agent/start", "/agent/stop", "/agent/key", "/agent/refusal"} {
		if strings.HasSuffix(p, suffix) {
			return "/sessions/{id}" + suffix
		}
	}
	return "/sessions/{id}"
}

func (s *Service) metricsCreateSession(w http.ResponseWriter, r *http.Request) {
	rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
	start := time.Now()
	s.createSession(rec, r)
	sessionCreateDuration.Observe(time.Since(start).Seconds())
	if rec.status == http.StatusCreated {
		sessionsCreatedTotal.Inc()
	}
}

func (s *Service) metricsJoinSession(w http.ResponseWriter, r *http.Request) {
	rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
	start := time.Now()
	s.joinSession(rec, r)
	sessionJoinDuration.Observe(time.Since(start).Seconds())
	if rec.status == http.StatusOK {
		sessionsJoinedTotal.Inc()
	}
}
