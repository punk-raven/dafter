package transport

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
)

// The egress service is driven over the media server's Twirp JSON API rather
// than through its SDK, which stays out of this module by design. Field
// names below are the protobuf JSON names from livekit_egress.proto at
// protocol v1.50.1, the version the dev stack's egress v1.14.1 is built
// against, and are pinned in testdata/egress.

const (
	twirpPrefix           = "/twirp/livekit.Egress/"
	twirpRoomPrefix       = "/twirp/livekit.RoomService/"
	methodCreateRoom      = "CreateRoom"
	methodRoomComposite   = "StartRoomCompositeEgress"
	methodTrackComposite  = "StartTrackCompositeEgress"
	methodTrack           = "StartTrackEgress"
	methodStop            = "StopEgress"
	serviceTokenTTL       = time.Minute
	fileTypeMP4           = "MP4"
	filenameTimeToken     = "{utc}" // expanded by the egress service at write time
	filenameLayoutDivider = "-"
)

// A service token is minted per API call, lives for one minute and is never
// returned to anything. It is the only token in the system that carries
// roomRecord or roomCreate, each for the one call that needs it, and it is a
// different type from the participant grant so that MintToken cannot be
// talked into issuing it.
type serviceGrant struct {
	RoomRecord bool   `json:"roomRecord,omitempty"`
	RoomCreate bool   `json:"roomCreate,omitempty"`
	Room       string `json:"room,omitempty"`
}

type createRoomRequest struct {
	Name string `json:"name"`
}

type serviceClaims struct {
	jwt.RegisteredClaims
	Video serviceGrant `json:"video"`
}

type s3Upload struct {
	AccessKey      string `json:"accessKey"`
	Secret         string `json:"secret"`
	Region         string `json:"region,omitempty"`
	Endpoint       string `json:"endpoint,omitempty"`
	Bucket         string `json:"bucket"`
	ForcePathStyle bool   `json:"forcePathStyle,omitempty"`
}

type encodedFileOutput struct {
	FileType string    `json:"fileType,omitempty"`
	Filepath string    `json:"filepath"`
	S3       *s3Upload `json:"s3"`
}

type directFileOutput struct {
	Filepath string    `json:"filepath"`
	S3       *s3Upload `json:"s3"`
}

type encodingOptions struct {
	Width        int    `json:"width,omitempty"`
	Height       int    `json:"height,omitempty"`
	Framerate    int    `json:"framerate,omitempty"`
	AudioBitrate int    `json:"audioBitrate,omitempty"`
	VideoCodec   string `json:"videoCodec,omitempty"`
	VideoBitrate int    `json:"videoBitrate,omitempty"`
}

type roomCompositeEgressRequest struct {
	RoomName    string              `json:"roomName"`
	AudioOnly   bool                `json:"audioOnly,omitempty"`
	Preset      string              `json:"preset,omitempty"`
	Advanced    *encodingOptions    `json:"advanced,omitempty"`
	FileOutputs []encodedFileOutput `json:"fileOutputs"`
}

type trackCompositeEgressRequest struct {
	RoomName     string              `json:"roomName"`
	AudioTrackID string              `json:"audioTrackId,omitempty"`
	VideoTrackID string              `json:"videoTrackId,omitempty"`
	Preset       string              `json:"preset,omitempty"`
	Advanced     *encodingOptions    `json:"advanced,omitempty"`
	FileOutputs  []encodedFileOutput `json:"fileOutputs"`
}

type trackEgressRequest struct {
	RoomName string            `json:"roomName"`
	TrackID  string            `json:"trackId"`
	File     *directFileOutput `json:"file"`
}

type stopEgressRequest struct {
	EgressID string `json:"egressId"`
}

// protojson writes int64 as a JSON string; a hand-written server may write
// a number. Both are accepted so the parse does not depend on which side of
// that rule the server is on.
type int64JSON int64

func (n *int64JSON) UnmarshalJSON(b []byte) error {
	s := strings.Trim(string(b), `"`)
	if s == "" || s == "null" {
		*n = 0
		return nil
	}
	v, err := strconv.ParseInt(s, 10, 64)
	if err != nil {
		return err
	}
	*n = int64JSON(v)
	return nil
}

// The media server answers with the proto field names (egress_id), which
// protojson permits alongside the lowerCamel names it accepts on input; the
// SFU at v1.13.7 does the former. Both spellings are read so a server built
// the other way still parses.
type egressInfoJSON struct {
	EgressID       string    `json:"egress_id"`
	EgressIDCamel  string    `json:"egressId"`
	RoomName       string    `json:"room_name"`
	RoomNameCamel  string    `json:"roomName"`
	Status         string    `json:"status"`
	StartedAt      int64JSON `json:"started_at"`
	StartedAtCamel int64JSON `json:"startedAt"`
	EndedAt        int64JSON `json:"ended_at"`
	EndedAtCamel   int64JSON `json:"endedAt"`
	Error          string    `json:"error"`
}

func (j egressInfoJSON) info() EgressInfo {
	return EgressInfo{
		EgressID:  first(j.EgressID, j.EgressIDCamel),
		Room:      first(j.RoomName, j.RoomNameCamel),
		Status:    j.Status,
		StartedAt: nanos(int64(max(j.StartedAt, j.StartedAtCamel))),
		EndedAt:   nanos(int64(max(j.EndedAt, j.EndedAtCamel))),
		Error:     j.Error,
	}
}

func first(a, b string) string {
	if a != "" {
		return a
	}
	return b
}

type twirpError struct {
	Code string `json:"code"`
	Msg  string `json:"msg"`
}

var egressVideoCodecNames = map[config.EgressVideoCodec]string{
	config.EgressCodecH264Baseline: "H264_BASELINE",
	config.EgressCodecH264Main:     "H264_MAIN",
	config.EgressCodecH264High:     "H264_HIGH",
}

func (l *LiveKit) StartEgress(ctx context.Context, req EgressRequest) (EgressInfo, error) {
	if l.storage == nil {
		return EgressInfo{}, errs.Errorf(errs.CodeInvalidConfig, "egress storage is not configured, so a recording has nowhere to land")
	}
	if req.Room == "" || req.SessionID == "" {
		return EgressInfo{}, errs.Errorf(errs.CodeInvalidConfig, "an egress needs a room and a session id")
	}
	if req.Encoding != nil && req.Encoding.Preset != "" && req.Encoding.StatesExplicitFields() {
		return EgressInfo{}, errs.Errorf(errs.CodeInvalidConfig, "an egress profile names a preset or explicit fields, not both")
	}

	method, body, err := l.egressRequest(req)
	if err != nil {
		return EgressInfo{}, err
	}
	if req.CreateRoom {
		if req.Layout != config.LayoutRoomComposite {
			return EgressInfo{}, errs.Errorf(errs.CodeInvalidConfig, "only a room composite can be started before the room has a participant")
		}
		if err := l.createRoom(ctx, req.Room); err != nil {
			return EgressInfo{}, err
		}
	}
	return l.egressCall(ctx, method, req.Room, body)
}

// createRoom is idempotent on the media server: an existing room of that
// name is returned unchanged, so a create raced by the first participant
// costs nothing.
func (l *LiveKit) createRoom(ctx context.Context, room string) error {
	token, err := l.serviceToken(serviceGrant{RoomCreate: true})
	if err != nil {
		return err
	}
	_, err = l.call(ctx, twirpRoomPrefix+methodCreateRoom, token, createRoomRequest{Name: room})
	return err
}

func (l *LiveKit) StopEgress(ctx context.Context, egressID string) (EgressInfo, error) {
	if egressID == "" {
		return EgressInfo{}, errs.Errorf(errs.CodeInvalidConfig, "a stop needs an egress id")
	}
	return l.egressCall(ctx, methodStop, "", stopEgressRequest{EgressID: egressID})
}

func (l *LiveKit) egressRequest(req EgressRequest) (string, any, error) {
	upload := l.upload()
	switch req.Layout {
	case config.LayoutRoomComposite:
		preset, advanced := encoding(req.Encoding)
		return methodRoomComposite, roomCompositeEgressRequest{
			RoomName:    req.Room,
			AudioOnly:   req.AudioOnly,
			Preset:      preset,
			Advanced:    advanced,
			FileOutputs: []encodedFileOutput{encodedFile(req, upload)},
		}, nil

	case config.LayoutTrackComposite:
		if req.AudioTrackID == "" && req.VideoTrackID == "" {
			return "", nil, errs.Errorf(errs.CodeInvalidConfig, "a track composite needs an audio track id, a video track id or both")
		}
		preset, advanced := encoding(req.Encoding)
		return methodTrackComposite, trackCompositeEgressRequest{
			RoomName:     req.Room,
			AudioTrackID: req.AudioTrackID,
			VideoTrackID: req.VideoTrackID,
			Preset:       preset,
			Advanced:     advanced,
			FileOutputs:  []encodedFileOutput{encodedFile(req, upload)},
		}, nil

	case config.LayoutTrack:
		if req.TrackID == "" {
			return "", nil, errs.Errorf(errs.CodeInvalidConfig, "a track egress needs a track id")
		}
		// No encode: the published bytes are written as they arrive and the
		// service picks the container from the track's own codec.
		return methodTrack, trackEgressRequest{
			RoomName: req.Room,
			TrackID:  req.TrackID,
			File: &directFileOutput{
				Filepath: filename(req, req.TrackID),
				S3:       upload,
			},
		}, nil

	default:
		return "", nil, errs.Errorf(errs.CodeInvalidConfig, "no egress request is defined for this layout")
	}
}

func encoding(p *config.EgressProfile) (preset string, advanced *encodingOptions) {
	if p == nil {
		return "", nil
	}
	if p.Preset != "" {
		return strings.ToUpper(string(p.Preset)), nil
	}
	if !p.StatesExplicitFields() {
		return "", nil
	}
	return "", &encodingOptions{
		Width:        p.Width,
		Height:       p.Height,
		Framerate:    p.Framerate,
		AudioBitrate: p.AudioBitrate,
		VideoCodec:   egressVideoCodecNames[p.VideoCodec],
		VideoBitrate: p.VideoBitrate,
	}
}

func encodedFile(req EgressRequest, upload *s3Upload) encodedFileOutput {
	out := encodedFileOutput{Filepath: filename(req, ""), S3: upload}
	// Audio-only is left to the service, which picks the container from the
	// audio codec; naming MP4 there would transcode the audio to AAC for no
	// reason.
	if !req.AudioOnly {
		out.FileType = fileTypeMP4
	}
	return out
}

// filename is {sessionId}/{layout}[-{discriminator}]-{utc}; the service
// expands {utc} and appends the extension the container implies, so one
// session's recordings share a prefix and each object says its layout.
func filename(req EgressRequest, discriminator string) string {
	parts := []string{string(req.Layout)}
	if discriminator != "" {
		parts = append(parts, discriminator)
	}
	parts = append(parts, filenameTimeToken)
	return req.SessionID + "/" + strings.Join(parts, filenameLayoutDivider)
}

func (l *LiveKit) upload() *s3Upload {
	return &s3Upload{
		AccessKey:      l.storage.AccessKey,
		Secret:         l.storage.Secret,
		Region:         l.storage.Region,
		Endpoint:       l.storage.Endpoint,
		Bucket:         l.storage.Bucket,
		ForcePathStyle: l.storage.ForcePathStyle,
	}
}

func (l *LiveKit) serviceToken(grant serviceGrant) (string, error) {
	issued := l.now()
	signed, err := jwt.NewWithClaims(jwt.SigningMethodHS256, &serviceClaims{
		RegisteredClaims: jwt.RegisteredClaims{
			Issuer:    l.key,
			IssuedAt:  jwt.NewNumericDate(issued),
			NotBefore: jwt.NewNumericDate(issued),
			ExpiresAt: jwt.NewNumericDate(issued.Add(serviceTokenTTL)),
		},
		Video: grant,
	}).SignedString([]byte(l.secret))
	if err != nil {
		return "", errs.Wrap(errs.CodeInternal, err, "mint service token")
	}
	return signed, nil
}

// call posts one Twirp request and returns the raw reply, or the service's
// refusal mapped onto the platform taxonomy.
func (l *LiveKit) call(ctx context.Context, path, token string, body any) ([]byte, error) {
	method := path[strings.LastIndex(path, "/")+1:]
	payload, err := json.Marshal(body)
	if err != nil {
		return nil, errs.Wrap(errs.CodeInternal, err, "encode %s request", method)
	}
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, l.httpURL+path, bytes.NewReader(payload))
	if err != nil {
		return nil, errs.Wrap(errs.CodeInternal, err, "build %s request", method)
	}
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Authorization", "Bearer "+token)

	resp, err := l.client.Do(httpReq)
	if err != nil {
		return nil, errs.Wrap(errs.CodeProviderUnavailable, err, "media server unreachable for %s", method)
	}
	defer func() { _ = resp.Body.Close() }()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return nil, errs.Wrap(errs.CodeProviderUnavailable, err, "read %s response", method)
	}
	if resp.StatusCode != http.StatusOK {
		return nil, twirpFailure(method, resp.StatusCode, raw)
	}
	return raw, nil
}

func (l *LiveKit) egressCall(ctx context.Context, method, room string, body any) (EgressInfo, error) {
	token, err := l.serviceToken(serviceGrant{RoomRecord: true, Room: room})
	if err != nil {
		return EgressInfo{}, err
	}
	raw, err := l.call(ctx, twirpPrefix+method, token, body)
	if err != nil {
		return EgressInfo{}, err
	}

	var parsed egressInfoJSON
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return EgressInfo{}, errs.Wrap(errs.CodeInternal, err, "decode egress response for %s", method)
	}
	info := parsed.info()
	if info.EgressID == "" {
		return EgressInfo{}, errs.Errorf(errs.CodeInternal, "egress service answered %s without an egress id", method)
	}
	return info, nil
}

func nanos(n int64) time.Time {
	if n == 0 {
		return time.Time{}
	}
	return time.Unix(0, n).UTC()
}

// Twirp error codes map onto the platform taxonomy; the service's message
// goes into the details, where an operator reads it, and the top-level
// message stays a fixed string.
func twirpFailure(method string, status int, raw []byte) *errs.Error {
	var te twirpError
	_ = json.Unmarshal(raw, &te)
	code := errs.CodeInternal
	switch te.Code {
	case "unauthenticated", "permission_denied":
		code = errs.CodeAuthenticationFailed
	case "invalid_argument", "not_found", "failed_precondition", "malformed":
		code = errs.CodeInvalidConfig
	case "resource_exhausted":
		code = errs.CodeQuotaExceeded
	case "unavailable", "deadline_exceeded":
		code = errs.CodeProviderUnavailable
	}
	e := errs.Errorf(code, "media server refused %s (http %d, %s)", method, status, te.Code)
	if te.Msg != "" {
		e.Details = []string{te.Msg}
	}
	return e
}
