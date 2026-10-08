package control

import (
	"crypto/hmac"
	"crypto/sha256"
	"regexp"

	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/ids"
)

var devicePattern = regexp.MustCompile(`^[A-Za-z0-9_-]{22,64}$`)

func (s *Service) participantFor(sessionID, device string) (string, error) {
	if device == "" {
		id, err := ids.NewID(ids.PrefixParticipant)
		if err != nil {
			return "", errs.Wrap(errs.CodeInternal, err, "mint participant id")
		}
		return id, nil
	}
	if err := checkDevice(device); err != nil {
		return "", err
	}
	if len(s.IdentityKey) == 0 {
		return "", errs.Errorf(errs.CodeInternal, "no identity key, so a device cannot be given its participant id")
	}
	mac := hmac.New(sha256.New, s.IdentityKey)
	mac.Write([]byte(sessionID))
	mac.Write([]byte{0})
	mac.Write([]byte(device))
	id, err := ids.FromDigest(ids.PrefixParticipant, mac.Sum(nil))
	if err != nil {
		return "", errs.Wrap(errs.CodeInternal, err, "derive participant id")
	}
	return id, nil
}

func checkDevice(device string) error {
	if device == "" || devicePattern.MatchString(device) {
		return nil
	}
	return located(errs.CodeInvalidConfig, "/device", "is not a device key: 22 to 64 letters, digits, '-' or '_'")
}
