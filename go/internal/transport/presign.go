package transport

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/punk-raven/dafter/go/internal/errs"
)

const (
	sigV4Algorithm = "AWS4-HMAC-SHA256"
	sigV4Service   = "s3"
	sigV4Terminal  = "aws4_request"
	sigV4Date      = "20060102"
	sigV4Time      = "20060102T150405Z"
	defaultRegion  = "us-east-1"
	maxPresignTTL  = 7 * 24 * time.Hour
)

func PresignGet(s EgressStorage, key string, at time.Time, ttl time.Duration) (string, error) {
	if key == "" || strings.HasPrefix(key, "/") {
		return "", errs.Errorf(errs.CodeInvalidConfig, "a recording's object key is relative to its bucket")
	}
	if ttl <= 0 || ttl > maxPresignTTL {
		return "", errs.Errorf(errs.CodeInvalidConfig, "a presigned url lives between a second and seven days")
	}
	region := s.Region
	if region == "" {
		region = defaultRegion
	}
	endpoint := s.Endpoint
	if endpoint == "" {
		endpoint = "https://s3." + region + ".amazonaws.com"
	}
	base, err := url.Parse(endpoint)
	if err != nil || base.Host == "" {
		return "", errs.Errorf(errs.CodeInvalidConfig, "egress storage endpoint is not a url")
	}
	host, path := base.Host, "/"+s.Bucket+"/"+uriEncode(key, false)
	if !s.ForcePathStyle {
		host, path = s.Bucket+"."+base.Host, "/"+uriEncode(key, false)
	}

	at = at.UTC()
	scope := strings.Join([]string{at.Format(sigV4Date), region, sigV4Service, sigV4Terminal}, "/")
	query := map[string]string{
		"X-Amz-Algorithm":     sigV4Algorithm,
		"X-Amz-Credential":    s.AccessKey + "/" + scope,
		"X-Amz-Date":          at.Format(sigV4Time),
		"X-Amz-Expires":       strconv.Itoa(int(ttl / time.Second)),
		"X-Amz-SignedHeaders": "host",
	}
	canonicalQuery := canonical(query)
	request := strings.Join([]string{
		"GET", path, canonicalQuery, "host:" + host + "\n", "host", "UNSIGNED-PAYLOAD",
	}, "\n")
	digest := sha256.Sum256([]byte(request))
	toSign := strings.Join([]string{sigV4Algorithm, at.Format(sigV4Time), scope, hex.EncodeToString(digest[:])}, "\n")

	signingKey := []byte("AWS4" + s.Secret)
	for _, part := range []string{at.Format(sigV4Date), region, sigV4Service, sigV4Terminal} {
		signingKey = hmacSHA256(signingKey, part)
	}
	signature := hex.EncodeToString(hmacSHA256(signingKey, toSign))
	return base.Scheme + "://" + host + path + "?" + canonicalQuery + "&X-Amz-Signature=" + signature, nil
}

func canonical(query map[string]string) string {
	keys := make([]string, 0, len(query))
	for k := range query {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, uriEncode(k, true)+"="+uriEncode(query[k], true))
	}
	return strings.Join(parts, "&")
}

func uriEncode(s string, encodeSlash bool) string {
	var b strings.Builder
	for _, c := range []byte(s) {
		switch {
		case c >= 'A' && c <= 'Z', c >= 'a' && c <= 'z', c >= '0' && c <= '9',
			c == '-', c == '_', c == '.', c == '~':
			b.WriteByte(c)
		case c == '/' && !encodeSlash:
			b.WriteByte(c)
		default:
			b.WriteString("%" + strings.ToUpper(hex.EncodeToString([]byte{c})))
		}
	}
	return b.String()
}

func hmacSHA256(key []byte, data string) []byte {
	mac := hmac.New(sha256.New, key)
	mac.Write([]byte(data))
	return mac.Sum(nil)
}
