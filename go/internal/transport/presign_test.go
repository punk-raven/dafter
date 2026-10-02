package transport_test

import (
	"testing"
	"time"

	"github.com/punk-raven/dafter/go/internal/transport"
)

func TestPresignMatchesTheReferenceSigner(t *testing.T) {
	t.Parallel()
	at := time.Date(2013, 5, 24, 0, 0, 0, 0, time.UTC)
	cases := []struct {
		name    string
		storage transport.EgressStorage
		key     string
		want    string
	}{
		{
			name: "virtual host, the AWS example",
			storage: transport.EgressStorage{
				Bucket: "examplebucket", Endpoint: "https://s3.amazonaws.com", Region: "us-east-1",
				AccessKey: "AKIAIOSFODNN7EXAMPLE", Secret: "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
			},
			key:  "test.txt",
			want: "https://examplebucket.s3.amazonaws.com/test.txt?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAIOSFODNN7EXAMPLE%2F20130524%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20130524T000000Z&X-Amz-Expires=86400&X-Amz-SignedHeaders=host&X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404",
		},
		{
			name: "path style on the dev stack's port",
			storage: transport.EgressStorage{
				Bucket: "dafter-recordings", Endpoint: "http://127.0.0.1:9000", Region: "us-east-1",
				AccessKey: "AKIAIOSFODNN7EXAMPLE", Secret: "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
				ForcePathStyle: true,
			},
			key:  "s_7f3a9c21/track-TR_AMabc123-20260924100001123.ogg",
			want: "http://127.0.0.1:9000/dafter-recordings/s_7f3a9c21/track-TR_AMabc123-20260924100001123.ogg?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAIOSFODNN7EXAMPLE%2F20130524%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20130524T000000Z&X-Amz-Expires=86400&X-Amz-SignedHeaders=host&X-Amz-Signature=bd471e39d3419957d29bcc1ae9243ed5d2a632c646ff57d677fbe38b7d7818c4",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			got, err := transport.PresignGet(tc.storage, tc.key, at, 24*time.Hour)
			if err != nil {
				t.Fatal(err)
			}
			if got != tc.want {
				t.Errorf("presigned\n %s\nwant\n %s", got, tc.want)
			}
		})
	}
}

func TestPresignRefusesWhatCannotBeSigned(t *testing.T) {
	t.Parallel()
	at := time.Date(2026, 9, 24, 10, 0, 0, 0, time.UTC)
	for name, call := range map[string]func() (string, error){
		"absolute key": func() (string, error) { return transport.PresignGet(devStorage, "/x.ogg", at, time.Hour) },
		"empty key":    func() (string, error) { return transport.PresignGet(devStorage, "", at, time.Hour) },
		"no lifetime":  func() (string, error) { return transport.PresignGet(devStorage, "x.ogg", at, 0) },
		"over a week":  func() (string, error) { return transport.PresignGet(devStorage, "x.ogg", at, 8*24*time.Hour) },
	} {
		if _, err := call(); err == nil {
			t.Errorf("%s: signed", name)
		}
	}
}
