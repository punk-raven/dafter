package turn

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

type ICEServer struct {
	URLs       []string `json:"urls"`
	Username   string   `json:"username"`
	Credential string   `json:"credential"`
}

const defaultBaseURL = "https://rtc.live.cloudflare.com"

type Fetcher struct {
	tokenID  string
	apiToken string
	client   *http.Client
	baseURL  string
}

func NewFetcher(tokenID, apiToken string) *Fetcher {
	return &Fetcher{
		tokenID:  tokenID,
		apiToken: apiToken,
		baseURL:  defaultBaseURL,
		client: &http.Client{
			Timeout: 5 * time.Second,
		},
	}
}

func NewFetcherWithClient(tokenID, apiToken string, client *http.Client) *Fetcher {
	return &Fetcher{
		tokenID:  tokenID,
		apiToken: apiToken,
		baseURL:  defaultBaseURL,
		client:   client,
	}
}

func (f *Fetcher) SetBaseURL(url string) {
	f.baseURL = url
}

func (f *Fetcher) Enabled() bool {
	return f.tokenID != "" && f.apiToken != ""
}

type generateRequest struct {
	TTL int `json:"ttl"`
}

type generateResponse struct {
	ICEServers []ICEServer `json:"iceServers"`
}

func (f *Fetcher) FetchCredentials(ctx context.Context) ([]ICEServer, error) {
	if !f.Enabled() {
		return nil, nil
	}

	body, err := json.Marshal(generateRequest{TTL: 86400})
	if err != nil {
		return nil, fmt.Errorf("marshal turn request: %w", err)
	}

	url := fmt.Sprintf(
		"%s/v1/turn/keys/%s/credentials/generate-ice-servers",
		f.baseURL, f.tokenID,
	)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("build turn request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+f.apiToken)
	req.Header.Set("Content-Type", "application/json")

	resp, err := f.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("turn api call: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()

	raw, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return nil, fmt.Errorf("read turn response: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("turn api returned %d: %s", resp.StatusCode, raw)
	}

	var out generateResponse
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, fmt.Errorf("decode turn response: %w", err)
	}

	return out.ICEServers, nil
}
