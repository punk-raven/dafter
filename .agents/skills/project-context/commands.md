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
    worker's `e2eLatencyMs`. Exits 0 on go, 1 on no-go, 2 when refused.
  - `--sweep --gate --baseline <file>`: exits 1 when a metric misses its threshold
    (`{"thresholds": {lang: {condition: {"metric.path": {"max"|"min": n}}}}}`); ungated sweeps
    only report. `--report-only` keeps a no-go at 0 on either path.
- `uv run dafter-wer`: scores a transcript against a known clip offline (`testdata/wer/`): WER,
  CER and OIWER (orthography-aware, reads the clip's `alternates`). Calls no provider.
- `uv run dafter-asr`: scores the STT a job vector names; details in `testdata/asr/README.md`.
  - `pin <dataset> --language`: kathbath, svarah, indicvoices, lahaja, mucs2021 (8 kHz hi, mr),
    mucs2021-codeswitch (Hinglish). Svarah, IndicVoices and Lahaja are gated behind `HF_TOKEN`.
  - `fetch`: clips into the git-ignored `testdata/asr/audio/`; Kathbath by HTTP range, MUCS via
    its whole tarball cached in `audio/archives/`.
  - `run <manifest>` or `run --set golden --language <lang>` (`testdata/golden/`): WER, CER,
    OIWER and entity accuracy; gates the primary metric (WER en/hi/mr, CER te/kn) against
    `testdata/asr/baselines.json`, exits 1 on a regression; `--record-baseline` stores it.
  - Spends STT credits, capped by `--max-inr`.
- `uv run dafter-tts`: TTS round trip over `testdata/speech/*-normalization.json`; details in
  `testdata/speech/README.md`.
  - `run --job <vector>`: synthesizes each `spoken` form, sends it through an 8 kHz G.711 line,
    transcribes it back; CER and entity accuracy. Audio goes to the git-ignored
    `testdata/speech/audio/` (`--audio-dir`).
  - Gates CER against `testdata/speech/tts-baselines.json` (`--baseline`), exits 1 on a
    regression; `--record-baseline` stores it. Spends TTS and STT credits, capped by `--max-inr`.
  - `sheet <a> <b> --sheet-dir --key` and `tally <csvs> --key`: blind A/B listening; no credits.
- `uv run dafter-scenarios --out <dir>`: tau2-style scenario suite (`testdata/scenarios/`, see its
  README). Text mode, simulated caller, final-state checks and the catalog judge; each task runs
  `--k` times and is gated on pass^k, tool success and read-back accuracy, exiting 1 on a miss.
  Filter with `--set`/`--language`/`--task`; use `--job` for TTS faults and `DAFTER_INJECT_FAULT`
  for failover. Spends LLM credits, capped by `--max-inr`.
- `uv run dafter-screen --out <dir>`: retries 429/5xx/timeouts (`--retries`, `--backoff`). Exits 1
  when a picked candidate had a call refused outright, missed a tool probe or drew a judge `fail`;
  2 when refused, or incomplete (a candidate not screened or a call unanswered after retries);
  `--report-only` keeps a miss or an incomplete run at 0.
- `uv run dafter-scorecard`: folds `dafter-asr` and `dafter-evals --out` reports (and human
  naturalness ratings) into one card per language and stack, listing what is still pending.
- Make targets (reports in the git-ignored `build/evals/`, `EVALS_OUT`; languages in
  `EVALS_LANGUAGES`, space-separated BCP-47; budgets per target, see the `Makefile`):
  - `make evals-offline`: the eval package tests; no credits.
  - `make evals-text`: `dafter-screen` plus `dafter-scenarios` (`TEXT_MAX_INR`, `SCENARIOS_K`).
  - `make evals-asr` (`ASR_SET`, `ASR_MAX_INR`), `make evals-tts` (`TTS_MAX_INR`): exit 1 on a
    baseline regression.
  - `make evals-live`: `dafter-evals` per language plus `--sweep` against a running stack
    (`LIVE_CONTROL`, `LIVE_TURNS`, `LIVE_SWEEPS`, `LIVE_CONDITIONS`).
  - `make evals-redteam`: promptfoo over `testdata/redteam/`, fails under `REDTEAM_PASS_RATE` (99).
  - `make evals-scorecard`: folds the asr, live and tts reports in `EVALS_OUT`; no credits.
  - CI: `.github/workflows/evals.yml`. Label `run-evals` (or dispatch, or nightly) runs text,
    red-team and asr; nightly adds live and tts. Paid jobs skip without `SARVAM_API_KEY`,
    `GEMINI_API_KEY`, `HF_TOKEN` secrets.
- `uv run dafter-batch <session>`: the transcript after the call; spends batch provider credits.
- `uv run dafter-scribe start`: runs the scribe; spends LLM credits.
- `uv run dafter-scribe-export {golden|bank} --queue <dir> --language <base>`: the scribe's failed
  turns into golden candidates or bank regressions; loop in `testdata/golden/README.md`.

## Load tests

- `make loadtest`: HTTP only, token minting.
- `make loadtest-media`: real WebRTC participants through `lk load-test`; knobs and pass criteria in
  `scripts/loadtest-media.sh --help`.
- `lk` must be the release binary `make tools` fetches into `go/bin`. A go-installed `lk` embeds Git
  LFS pointers instead of video and publishes nothing.
- Load tests create sessions with the agent off.

## Grafana dashboards

- `deploy/grafana/dashboards/dafter.json` (uid `dafter`): control plane and SFU.
- `dafter-agent-latency.json`, `dafter-agent-turns.json`, `dafter-agent-cost.json`: the worker.
- `dafter-agent-quality.json`: the scribe's judge on sampled production turns (pass and fail share
  by language, criterion, version and arm), scrape job `dafter-scribe` on port 9465.
- A uid change requires recreating the grafana container.
- Worker metrics go in new dashboards, not panels in `dafter.json` (already past the 500-line cap).
