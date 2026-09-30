package control

import (
	"net/http"
	"slices"
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

	scribeDispatchesTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "dafter_scribe_dispatches_total",
		Help: "Scribe dispatches by outcome. A failed one leaves the call without notes and never fails it.",
	}, []string{"outcome"})

	phoneCallsTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "dafter_phone_calls_total",
		Help: "Outbound phone calls by outcome: placed when the media server took the call, failed when it refused it, hung_up when the control plane ended it.",
	}, []string{"outcome"})

	inboundCallsTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "dafter_inbound_calls_total",
		Help: "Carrier webhooks for inbound calls by outcome: held, dialed, bridged, failed, hung_up or refused.",
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

func incScribeDispatch(ok bool) {
	outcome := "failed"
	if ok {
		outcome = "dispatched"
	}
	scribeDispatchesTotal.WithLabelValues(outcome).Inc()
}

func incCall(ok bool) {
	outcome := "failed"
	if ok {
		outcome = "placed"
	}
	phoneCallsTotal.WithLabelValues(outcome).Inc()
}

func incHangUp() {
	phoneCallsTotal.WithLabelValues("hung_up").Inc()
}

func incInbound(outcome string) {
	inboundCallsTotal.WithLabelValues(outcome).Inc()
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
	mux.HandleFunc("POST /sessions/{sessionID}/call/start", s.startCall)
	mux.HandleFunc("POST /sessions/{sessionID}/call/{participantID}/stop", s.stopCall)
	mux.HandleFunc("POST /telephony/{trunk}/answer", s.answerCall)
	mux.HandleFunc("POST /telephony/{trunk}/held/{token}", s.callHeld)
	mux.HandleFunc("POST /telephony/{trunk}/bridge", s.bridgeCall)
	mux.HandleFunc("POST /sessions/{sessionID}/scribe/key", s.scribeKey)
	mux.HandleFunc("POST /sessions/{sessionID}/scribe/refusal", s.scribeRefusal)
	mux.HandleFunc("GET /sessions/{sessionID}/transcription/sources", s.transcriptionSources)
	mux.HandleFunc("POST /sessions/{sessionID}/transcripts", s.storeTranscript)
	mux.HandleFunc("GET /sessions/{sessionID}/transcripts", s.listTranscripts)
	mux.HandleFunc("GET /sessions/{sessionID}/transcripts/{version}", s.exportTranscript)
	mux.HandleFunc("POST /sessions/{sessionID}/minutes", s.storeMinutes)
	mux.HandleFunc("GET /sessions/{sessionID}/minutes", s.readMinutes)
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
	if strings.HasPrefix(p, "/telephony/") {
		if parts := strings.Split(p, "/"); len(parts) > 3 && slices.Contains([]string{"answer", "held", "bridge"}, parts[3]) {
			return "/telephony/{trunk}/" + parts[3]
		}
		return "/telephony"
	}
	if !strings.HasPrefix(p, "/sessions/") {
		return p
	}
	if strings.Contains(p, "/call/") && strings.HasSuffix(p, "/stop") {
		return "/sessions/{id}/call/{participant}/stop"
	}
	if strings.Contains(p, "/transcripts/") {
		return "/sessions/{id}/transcripts/{version}"
	}
	for _, suffix := range []string{"/join", "/recording/start", "/recording/stop", "/agent/start", "/agent/stop", "/agent/key", "/agent/refusal", "/call/start", "/scribe/key", "/scribe/refusal", "/transcription/sources", "/transcripts", "/minutes"} {
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
