package control

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"time"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/events"
	"github.com/punk-raven/dafter/go/internal/state"
)

const (
	sourceURLTTL      = time.Hour
	maxTranscriptBody = 16 << 20
	transcriptHashKey = "transcriptHash"
)

type transcriptSource struct {
	RecordingID string      `json:"recordingId"`
	TrackID     string      `json:"trackId"`
	Speaker     speakerView `json:"speaker"`
	StartedAt   time.Time   `json:"startedAt"`
	StoppedAt   time.Time   `json:"stoppedAt"`
	URL         string      `json:"url"`
	ExpiresAt   time.Time   `json:"expiresAt"`
}

type heldBack struct {
	RecordingID string `json:"recordingId"`
	Reason      string `json:"reason"`
}

type sourcesResponse struct {
	SessionID  string             `json:"sessionId"`
	ConfigHash string             `json:"configHash"`
	Config     json.RawMessage    `json:"config"`
	Sources    []transcriptSource `json:"sources"`
	Pending    []heldBack         `json:"pending"`
	Skipped    []heldBack         `json:"skipped"`
}

type transcriptVersionView struct {
	Version        int       `json:"version"`
	TranscriptHash string    `json:"transcriptHash"`
	Provider       string    `json:"provider"`
	Model          string    `json:"model"`
	CreatedAt      time.Time `json:"createdAt"`
	StoredAt       time.Time `json:"storedAt"`
}

type transcriptListResponse struct {
	SessionID string                  `json:"sessionId"`
	Versions  []transcriptVersionView `json:"versions"`
}

type transcriptExport struct {
	SessionID  string          `json:"sessionId"`
	Version    int             `json:"version"`
	StoredAt   time.Time       `json:"storedAt"`
	Transcript json.RawMessage `json:"transcript"`
}

type submittedSpeaker struct {
	Kind          string `json:"kind"`
	ParticipantID string `json:"participantId"`
}

type submittedTranscript struct {
	TranscriptHash string `json:"transcriptHash"`
	Provenance     struct {
		Provider   string    `json:"provider"`
		Model      string    `json:"model"`
		ConfigHash string    `json:"configHash"`
		CreatedAt  time.Time `json:"createdAt"`
		Recordings []struct {
			RecordingID string           `json:"recordingId"`
			Speaker     submittedSpeaker `json:"speaker"`
			StartedAt   time.Time        `json:"startedAt"`
		} `json:"recordings"`
	} `json:"provenance"`
	Verbatim []transcriptLineRef `json:"verbatim"`
	Clean    []transcriptLineRef `json:"clean"`
}

type transcriptLineRef struct {
	RecordingID string           `json:"recordingId"`
	Speaker     submittedSpeaker `json:"speaker"`
}

func (s *Service) afterCallSession(w http.ResponseWriter, r *http.Request) (state.Session, bool) {
	sess, ok := s.storedSession(w, r)
	if !ok {
		return state.Session{}, false
	}
	cfg, err := config.Parse(sess.Config)
	if err != nil {
		s.fail(w, errs.Wrap(errs.CodeInternal, err, "stored session document"))
		return state.Session{}, false
	}
	if !cfg.TranscriptionMode().AfterCall() {
		s.fail(w, located(errs.CodeInvalidConfig, "/transcription/mode", "the transcript after the call is off in this session's stored config"))
		return state.Session{}, false
	}
	return sess, true
}

func (s *Service) transcriptionSources(w http.ResponseWriter, r *http.Request) {
	if !s.authenticWorker(w, r) {
		return
	}
	sess, ok := s.afterCallSession(w, r)
	if !ok {
		return
	}
	egresses, err := s.Store.Egresses(r.Context(), sess.SessionID)
	if err != nil {
		s.fail(w, err)
		return
	}
	out := sourcesResponse{
		SessionID: sess.SessionID, ConfigHash: sess.ConfigHash, Config: sess.Config,
		Sources: []transcriptSource{}, Pending: []heldBack{}, Skipped: []heldBack{},
	}
	for _, e := range egresses {
		switch {
		case e.Layout != string(config.LayoutTrack):
			continue
		case !e.Audio:
			out.Skipped = append(out.Skipped, heldBack{e.EgressID, "not an audio track"})
			continue
		case e.SpeakerKind == "":
			out.Skipped = append(out.Skipped, heldBack{e.EgressID, "its publisher is not a participant the control plane minted"})
			continue
		case e.Active():
			out.Pending = append(out.Pending, heldBack{e.EgressID, "still recording"})
			continue
		}
		file, err := s.Transport.RecordingFile(r.Context(), e.EgressID, sourceURLTTL)
		if err != nil {
			s.fail(w, err)
			return
		}
		if !file.Complete {
			out.Pending = append(out.Pending, heldBack{e.EgressID, "the media server reports " + file.Status})
			continue
		}
		out.Sources = append(out.Sources, transcriptSource{
			RecordingID: e.EgressID, TrackID: e.TrackID,
			Speaker:   speakerView{Kind: e.SpeakerKind, ParticipantID: e.ParticipantID},
			StartedAt: e.StartedAt, StoppedAt: e.StoppedAt, URL: file.URL, ExpiresAt: file.ExpiresAt,
		})
	}
	s.log().Info("transcription sources handed out", "session", sess.SessionID,
		"sources", len(out.Sources), "pending", len(out.Pending), "skipped", len(out.Skipped))
	s.write(w, http.StatusOK, out)
}

func (s *Service) storeTranscript(w http.ResponseWriter, r *http.Request) {
	if !s.authenticWorker(w, r) {
		return
	}
	sess, ok := s.afterCallSession(w, r)
	if !ok {
		return
	}
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxTranscriptBody))
	if err != nil {
		s.fail(w, errs.Wrap(errs.CodeInvalidConfig, err, "read transcript version"))
		return
	}
	payload, submitted, err := parseTranscriptEvent(raw, sess)
	if err != nil {
		s.fail(w, err)
		return
	}
	egresses, err := s.Store.Egresses(r.Context(), sess.SessionID)
	if err != nil {
		s.fail(w, err)
		return
	}
	if err := checkProvenance(submitted, sess, egresses); err != nil {
		s.fail(w, err)
		return
	}
	stored, created, err := s.Store.AddTranscript(r.Context(), state.Transcript{
		SessionID: sess.SessionID, TranscriptHash: submitted.TranscriptHash,
		Provider: submitted.Provenance.Provider, Model: submitted.Provenance.Model,
		CreatedAt: submitted.Provenance.CreatedAt, StoredAt: time.Now().UTC(), Document: payload,
	})
	if err != nil {
		s.fail(w, err)
		return
	}
	status := http.StatusOK
	if created {
		status = http.StatusCreated
		s.log().Info("transcript version stored", "session", sess.SessionID, "version", stored.Version, "hash", stored.TranscriptHash)
	}
	s.write(w, status, versionView(stored))
}

func parseTranscriptEvent(raw []byte, sess state.Session) (json.RawMessage, submittedTranscript, error) {
	event, err := events.Parse(raw)
	if err != nil {
		return nil, submittedTranscript{}, relabel(err)
	}
	if event.Type != events.EventTranscriptVersionCreated {
		return nil, submittedTranscript{}, located(errs.CodeInvalidConfig, "/type", "a transcript version arrives as transcript.version_created")
	}
	if event.SessionID != sess.SessionID || event.TenantID != sess.TenantID {
		return nil, submittedTranscript{}, located(errs.CodeInvalidConfig, "/sessionId", "the event names another session or tenant")
	}
	var envelope struct {
		Payload json.RawMessage `json:"payload"`
	}
	if err := json.Unmarshal(raw, &envelope); err != nil {
		return nil, submittedTranscript{}, errs.Wrap(errs.CodeInvalidConfig, err, "decode transcript version")
	}
	var submitted submittedTranscript
	if err := json.Unmarshal(envelope.Payload, &submitted); err != nil {
		return nil, submittedTranscript{}, errs.Wrap(errs.CodeInvalidConfig, err, "decode transcript version")
	}
	hash, err := config.HashWithout(envelope.Payload, transcriptHashKey)
	if err != nil {
		return nil, submittedTranscript{}, err
	}
	if hash != submitted.TranscriptHash {
		return nil, submittedTranscript{}, located(errs.CodeInvalidConfig, "/payload/transcriptHash", "recomputed over RFC 8785 and does not match")
	}
	canonical, err := config.Canonicalize(envelope.Payload)
	if err != nil {
		return nil, submittedTranscript{}, err
	}
	return canonical, submitted, nil
}

func relabel(err error) error {
	var de *errs.Error
	if errors.As(err, &de) && de.Code == errs.CodeInternal {
		out := errs.Errorf(errs.CodeInvalidConfig, "the transcript version is not a valid event")
		out.Details = de.Details
		return out
	}
	return err
}

func checkProvenance(t submittedTranscript, sess state.Session, egresses []state.Egress) error {
	var problems []string
	if t.Provenance.ConfigHash != sess.ConfigHash {
		problems = append(problems, "at '/payload/provenance/configHash': is not the hash of this session's stored document")
	}
	recorded := map[string]state.Egress{}
	for _, e := range egresses {
		if e.Layout == string(config.LayoutTrack) && e.SpeakerKind != "" && !e.Active() {
			recorded[e.EgressID] = e
		}
	}
	declared := map[string]submittedSpeaker{}
	for i, rec := range t.Provenance.Recordings {
		e, ok := recorded[rec.RecordingID]
		pointer := fmt.Sprintf("/payload/provenance/recordings/%d", i)
		switch {
		case !ok:
			problems = append(problems, "at '"+pointer+"/recordingId': is not a finished, attributed track recording of this session")
		case rec.Speaker != (submittedSpeaker{e.SpeakerKind, e.ParticipantID}):
			problems = append(problems, "at '"+pointer+"/speaker': is not whose track the recording is")
		case !rec.StartedAt.Equal(e.StartedAt):
			problems = append(problems, "at '"+pointer+"/startedAt': is not when the recording started")
		}
		declared[rec.RecordingID] = rec.Speaker
	}
	for rendering, lines := range map[string][]transcriptLineRef{"verbatim": t.Verbatim, "clean": t.Clean} {
		for i, line := range lines {
			if speaker, ok := declared[line.RecordingID]; !ok || speaker != line.Speaker {
				problems = append(problems, fmt.Sprintf("at '/payload/%s/%d': is not attributed to a recording in its provenance", rendering, i))
			}
		}
	}
	if len(problems) == 0 {
		return nil
	}
	e := errs.Errorf(errs.CodeInvalidConfig, "%d problem(s) with the transcript version's provenance", len(problems))
	e.Details = problems
	return e
}

func versionView(t state.Transcript) transcriptVersionView {
	return transcriptVersionView{
		Version: t.Version, TranscriptHash: t.TranscriptHash, Provider: t.Provider, Model: t.Model,
		CreatedAt: t.CreatedAt, StoredAt: t.StoredAt,
	}
}

func (s *Service) listTranscripts(w http.ResponseWriter, r *http.Request) {
	if !s.authenticWorker(w, r) {
		return
	}
	sess, ok := s.storedSession(w, r)
	if !ok {
		return
	}
	versions, err := s.Store.Transcripts(r.Context(), sess.SessionID)
	if err != nil {
		s.fail(w, err)
		return
	}
	out := transcriptListResponse{SessionID: sess.SessionID, Versions: make([]transcriptVersionView, 0, len(versions))}
	for _, v := range versions {
		out.Versions = append(out.Versions, versionView(v))
	}
	s.write(w, http.StatusOK, out)
}

func (s *Service) exportTranscript(w http.ResponseWriter, r *http.Request) {
	if !s.authenticWorker(w, r) {
		return
	}
	sess, ok := s.storedSession(w, r)
	if !ok {
		return
	}
	version, err := strconv.Atoi(r.PathValue("version"))
	if err != nil || version < 1 {
		s.fail(w, located(errs.CodeInvalidConfig, "/version", "is a transcript version number, counted from 1"))
		return
	}
	t, err := s.Store.Transcript(r.Context(), sess.SessionID, version)
	if errors.Is(err, state.ErrNoTranscript) {
		s.fail(w, located(errs.CodeInvalidConfig, "/version", "this session has no transcript version of that number"))
		return
	}
	if err != nil {
		s.fail(w, err)
		return
	}
	s.write(w, http.StatusOK, transcriptExport{
		SessionID: sess.SessionID, Version: t.Version, StoredAt: t.StoredAt, Transcript: t.Document,
	})
}
