package control

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"time"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/ids"
	"github.com/punk-raven/dafter/go/internal/state"
	"github.com/punk-raven/dafter/go/internal/transport"
)

var (
	trackIDPattern  = regexp.MustCompile(`^TR_[A-Za-z0-9]{1,64}$`)
	egressIDPattern = regexp.MustCompile(`^EG_[A-Za-z0-9]{1,64}$`)
)

type startRecordingRequest struct {
	AudioTrackID string `json:"audioTrackId,omitempty"`
	VideoTrackID string `json:"videoTrackId,omitempty"`
	TrackID      string `json:"trackId,omitempty"`
}

type stopRecordingRequest struct {
	EgressID string `json:"egressId,omitempty"`
}

type recordingView struct {
	EgressID  string     `json:"egressId"`
	Layout    string     `json:"layout"`
	Status    string     `json:"status,omitempty"`
	StartedAt time.Time  `json:"startedAt"`
	StoppedAt *time.Time `json:"stoppedAt,omitempty"`
}

type recordingResponse struct {
	SessionID  string          `json:"sessionId"`
	Recordings []recordingView `json:"recordings"`
}

type sessionView struct {
	SessionID  string          `json:"sessionId"`
	Room       string          `json:"room"`
	ConfigHash string          `json:"configHash"`
	Config     json.RawMessage `json:"config"`
	CreatedAt  time.Time       `json:"createdAt"`
	Recordings []recordingView `json:"recordings"`

	AgentRefusal json.RawMessage `json:"agentRefusal,omitempty"`
}

func (s *Service) startRecording(w http.ResponseWriter, r *http.Request) {
	sess, cfg, ok := s.recordableSession(w, r)
	if !ok {
		return
	}
	var req startRecordingRequest
	if !s.decodeOptional(w, r, &req, "decode recording start request") {
		return
	}
	if err := trackIDsFor(cfg.Recording.Layout, req); err != nil {
		s.fail(w, err)
		return
	}

	info, err := s.startEgress(r.Context(), sess, cfg, req, false)
	if err != nil {
		s.fail(w, err)
		return
	}
	s.write(w, http.StatusCreated, recordingResponse{
		SessionID:  sess.SessionID,
		Recordings: []recordingView{{EgressID: info.EgressID, Layout: string(cfg.Recording.Layout), Status: info.Status, StartedAt: info.StartedAt}},
	})
}

func (s *Service) startEgress(ctx context.Context, sess state.Session, cfg *config.ResolvedSessionConfig, req startRecordingRequest, beforeFirstJoin bool) (transport.EgressInfo, error) {
	layout := cfg.Recording.Layout
	if layout == "" {
		layout = config.LayoutTrack
	}
	info, err := s.Transport.StartEgress(ctx, transport.EgressRequest{
		Room:         sess.Room,
		SessionID:    sess.SessionID,
		Layout:       layout,
		AudioOnly:    !cfg.VideoEnabled(),
		CreateRoom:   beforeFirstJoin,
		Encoding:     cfg.Egress(),
		AudioTrackID: req.AudioTrackID,
		VideoTrackID: req.VideoTrackID,
		TrackID:      req.TrackID,
	})
	if err != nil {
		return transport.EgressInfo{}, err
	}
	startedAt := info.StartedAt
	if startedAt.IsZero() {
		startedAt = time.Now().UTC()
	}
	if err := s.Store.AddEgress(ctx, state.Egress{
		EgressID: info.EgressID, SessionID: sess.SessionID, Layout: string(layout), StartedAt: startedAt,
	}); err != nil {
		if _, stopErr := s.Transport.StopEgress(ctx, info.EgressID); stopErr != nil {
			s.log().Error("egress running but not recorded; stop failed", "egress", info.EgressID, "error", stopErr)
		}
		return transport.EgressInfo{}, err
	}
	info.StartedAt = startedAt
	return info, nil
}

func (s *Service) stopRecording(w http.ResponseWriter, r *http.Request) {
	sess, _, ok := s.recordableSession(w, r)
	if !ok {
		return
	}
	var req stopRecordingRequest
	if !s.decodeOptional(w, r, &req, "decode recording stop request") {
		return
	}
	if req.EgressID != "" && !egressIDPattern.MatchString(req.EgressID) {
		s.fail(w, located(errs.CodeInvalidConfig, "/egressId", "is not an egress id the media server could have minted"))
		return
	}

	running, err := s.Store.Egresses(r.Context(), sess.SessionID)
	if err != nil {
		s.fail(w, err)
		return
	}
	var targets []state.Egress
	for _, e := range running {
		if e.Active() && (req.EgressID == "" || e.EgressID == req.EgressID) {
			targets = append(targets, e)
		}
	}
	if len(targets) == 0 {
		s.fail(w, located(errs.CodeInvalidConfig, "/egressId", "no recording of this session is running under that id"))
		return
	}

	out := recordingResponse{SessionID: sess.SessionID, Recordings: []recordingView{}}
	for _, e := range targets {
		info, err := s.Transport.StopEgress(r.Context(), e.EgressID)
		if err != nil {
			s.fail(w, err)
			return
		}
		stoppedAt := info.EndedAt
		if stoppedAt.IsZero() {
			stoppedAt = time.Now().UTC()
		}
		if err := s.Store.StopEgress(r.Context(), e.EgressID, stoppedAt); err != nil {
			s.fail(w, err)
			return
		}
		out.Recordings = append(out.Recordings, recordingView{
			EgressID: e.EgressID, Layout: e.Layout, Status: info.Status, StartedAt: e.StartedAt, StoppedAt: &stoppedAt,
		})
	}
	s.write(w, http.StatusOK, out)
}

func (s *Service) readSession(w http.ResponseWriter, r *http.Request) {
	sess, ok := s.storedSession(w, r)
	if !ok {
		return
	}
	egresses, err := s.Store.Egresses(r.Context(), sess.SessionID)
	if err != nil {
		s.fail(w, err)
		return
	}
	views := make([]recordingView, 0, len(egresses))
	for _, e := range egresses {
		v := recordingView{EgressID: e.EgressID, Layout: e.Layout, StartedAt: e.StartedAt}
		if !e.Active() {
			stopped := e.StoppedAt
			v.StoppedAt = &stopped
		}
		views = append(views, v)
	}
	s.write(w, http.StatusOK, sessionView{
		SessionID: sess.SessionID, Room: sess.Room, ConfigHash: sess.ConfigHash,
		Config: sess.Config, CreatedAt: sess.CreatedAt, Recordings: views,
		AgentRefusal: sess.AgentRefusal,
	})
}

func (s *Service) storedSession(w http.ResponseWriter, r *http.Request) (state.Session, bool) {
	sessionID := r.PathValue("sessionID")
	if err := ids.ValidateID(ids.PrefixSession, sessionID); err != nil {
		s.fail(w, errs.Wrap(errs.CodeInvalidConfig, err, "invalid session id"))
		return state.Session{}, false
	}
	sess, err := s.Store.Session(r.Context(), sessionID)
	if err != nil {
		s.fail(w, errs.Wrap(errs.CodeInvalidConfig, err, "session not found"))
		return state.Session{}, false
	}
	return sess, true
}

func (s *Service) recordableSession(w http.ResponseWriter, r *http.Request) (state.Session, *config.ResolvedSessionConfig, bool) {
	sess, ok := s.storedSession(w, r)
	if !ok {
		return state.Session{}, nil, false
	}
	cfg, err := config.Parse(sess.Config)
	if err != nil {
		s.fail(w, err)
		return state.Session{}, nil, false
	}
	if !cfg.Recording.Enabled {
		s.fail(w, located(errs.CodeInvalidConfig, "/recording/enabled", "recording is not enabled for this session"))
		return state.Session{}, nil, false
	}
	if cfg.Recording.ConsentArtifactID == "" {
		s.fail(w, located(errs.CodeConsentRequired, "/recording/consentArtifactId", "recording cannot proceed without a consent artifact"))
		return state.Session{}, nil, false
	}
	return sess, cfg, true
}

func trackIDsFor(layout config.EgressLayout, req startRecordingRequest) error {
	var problems []string
	check := func(pointer, id string, wanted bool) {
		switch {
		case id != "" && !wanted:
			problems = append(problems, fmt.Sprintf("at '%s': a %s recording does not take this track id", pointer, layout))
		case id != "" && !trackIDPattern.MatchString(id):
			problems = append(problems, fmt.Sprintf("at '%s': is not a track id the media server could have minted", pointer))
		}
	}
	check("/trackId", req.TrackID, layout == config.LayoutTrack)
	check("/audioTrackId", req.AudioTrackID, layout == config.LayoutTrackComposite)
	check("/videoTrackId", req.VideoTrackID, layout == config.LayoutTrackComposite)
	switch layout {
	case config.LayoutTrack:
		if req.TrackID == "" {
			problems = append(problems, "at '/trackId': a track recording attaches to one published track and needs its id")
		}
	case config.LayoutTrackComposite:
		if req.AudioTrackID == "" && req.VideoTrackID == "" {
			problems = append(problems, "at '/audioTrackId': a track composite needs an audio track id, a video track id or both")
		}
	}
	if len(problems) == 0 {
		return nil
	}
	e := errs.Errorf(errs.CodeInvalidConfig, "%d problem(s) with the recording request", len(problems))
	e.Details = problems
	return e
}

func located(code errs.ErrorCode, pointer, because string) *errs.Error {
	e := errs.Errorf(code, "1 problem with the request")
	e.Details = []string{fmt.Sprintf("at '%s': %s", pointer, because)}
	return e
}

func (s *Service) decodeOptional(w http.ResponseWriter, r *http.Request, into any, what string) bool {
	d := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20))
	d.DisallowUnknownFields()
	if err := d.Decode(into); err != nil && !errors.Is(err, io.EOF) {
		s.fail(w, errs.Wrap(errs.CodeInvalidConfig, err, "%s", what))
		return false
	}
	return true
}
