# Commands

First run (`./scripts/setup.sh`) and the dev stack (`make dev`) are in `.agents/rules/stack.md`.

## Build and test

- `make check`: the Go, test client and Python checks CI runs.
- Go: `make build vet lint test tidy`.
- Python: `make py-lint py-test` (ruff, mypy strict, pytest via `uv run --frozen` from `python/`).
- Test client: `make js-test` (Node 20+, Node's built-in runner `node --test` over
  `go/cmd/dafter-control/jstest/`, which loads the browser scripts into a `vm` context with stub
  rooms; no packages).

## Evals and offline scoring

These spend provider credits; run them only when asked.

- `uv run dafter-evals`: scripted live-agent harness; spends provider credits.
  - One run per config change: `--overrides` for the session, `--out` for the report, `--baseline`
    an earlier report to diff against.
  - `--scenarios` adds `paused` to the default scenarios: each `script.paused` sentence is said in
    two halves with a 300 ms hesitation.
  - `early_endpoints` counts turns the agent took before the caller's last word.
  - The report names the clock behind every number:
    - `caller`: the probe's clock, from the caller's last word as sent: gap to the first agent
      audio, end of turn to the `thinking` state, the reply after it, and the barge-in stop from
      agent state events.
    - `worker`: p50/p95 of every `agent.turn_metrics` layer, anchored at the end of speech the STT
      reported. Sarvam reports no speech end time; there it is the event's arrival, and the
      end-of-turn and transcription layers read near 0.
  - The go/no-go against the session's `budgets` judges the reply on the caller gap, never on the
    worker's `e2eLatencyMs`.
- `uv run dafter-wer`: scores a transcript against a known clip offline (`testdata/wer/`).
- `uv run dafter-asr`: scores the STT a job vector names on the pinned public samples in
  `testdata/asr/` (Kathbath for hi, kn, mr, te; Svarah for en-IN, gated behind `HF_TOKEN`). Audio is
  fetched by HTTP range into the git-ignored `testdata/asr/audio/`. Spends STT credits, capped by
  `--max-inr`.
- `uv run dafter-scorecard`: folds `dafter-asr` and `dafter-evals --out` reports (and human
  naturalness ratings) into one card per language and stack, listing what is still pending.
- `uv run dafter-batch <session>`: the transcript after the call; spends batch provider credits.
- `uv run dafter-scribe start`: runs the scribe; spends LLM credits.

## Load tests

- `make loadtest`: HTTP only, token minting.
- `make loadtest-media`: real WebRTC participants through `lk load-test`; knobs and pass criteria in
  `scripts/loadtest-media.sh --help`.
- `lk` must be the release binary `make tools` fetches into `go/bin`. A go-installed `lk` embeds Git
  LFS pointers instead of video and publishes nothing.
- Load tests create sessions with the agent off.

## Grafana dashboards

- `deploy/grafana/dashboards/dafter.json` (uid `dafter`): control plane and SFU.
- `dafter-agent-latency.json`, `dafter-agent-cost.json`: the worker.
- A uid change requires recreating the grafana container.
- Worker metrics go in new dashboards, not panels in `dafter.json` (already past the 500-line cap).
