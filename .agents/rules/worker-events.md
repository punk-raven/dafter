---
paths:
  - "python/dafter_runtime/src/dafter_runtime/{events,metrics,timing,cost,configured,telemetry}.py"
  - "python/dafter_runtime/src/dafter_runtime/stages.py"
  - "python/dafter_runtime/src/dafter_runtime/prices.csv"
  - "python/dafter_runtime/tests/test_{events,metrics,timing,cost,configured,serial,endpoint}.py"
  - "python/dafter_runtime/tests/test_failures.py"
  - "deploy/prometheus.yml"
  - "deploy/grafana/**"
  - "python/dafter_providers/src/dafter_providers/endpointing.py"
  - "go/cmd/dafter-control/agent-{metrics,turns}.js"
  - "schemas/events/v1/envelope.schema.json"
---

# Worker events and metrics

## Worker events and metrics

- Envelopes on the data channel topic `dafter.events`:
  - `agent.state_changed`: agent state.
  - `agent.turn_metrics`: each turn's latency by layer.
  - `session.usage`: the session's running INR cost. Prices come from
    `python/dafter_runtime/src/dafter_runtime/prices.csv`, verified rows only; an unlisted item is
    sent unpriced.
  - `agent.configured`: what the worker runs (LLM and effective fillers, backchannel and
    normalization), once at session start.
  - `provider.degraded`: every stage failure with its error document (retried or given up).
  - `agent.configured`, `agent.turn_metrics` and the scribe's `agent.turn_scored` carry
    `configVersion` (`id`, `arm`) when the session config states a `version`, stamped in
    `SessionEvents.envelope` (`events.py`).
- With `DAFTER_METRICS_PORT` set, the worker also serves them to Prometheus (`metrics.py`) through
  the framework's own server in multiprocess mode (turns run in job processes).
  - Scrape job `dafter-agent` at the compose network's pinned gateway 10.213.0.1;
    `host.docker.internal` is the desktop machine on Docker Desktop.
  - Labels: language, channel, version (`version.id`, `none` without one), stage, provider/model;
    never a session id.
  - A session creates every series it can produce at zero, or `rate()` loses its first observation.
  - Quality counters (language, channel): `dafter_agent_interruptions` (user cut a reply off,
    never `programmatic`), `_false_interruptions`, `_backchannels_suppressed`,
    `_backchannel_answers` (held back, then released as an answer), `_filler_plays`; and
    `dafter_agent_provider_errors` (plus component, vendor) for every `provider.degraded`;
    `dafter_agent_provider_switches` when an LLM or TTS stage moves along its fallback chain.
  - Code outside the worker counts through `metrics.counted(Moment.X)`: it lands on the process's
    serving `SessionMetrics` (one session per job process). Do not add lines to `worker.py`.
  - Quality counters and reply gap p95 by language and version: dashboard
    `deploy/grafana/dashboards/dafter-agent-turns.json`; layer timings: `dafter-agent-latency.json`.
  - Alert rules live in `deploy/grafana/provisioning/alerting/`; reply gap p95 over 1500 ms per
    language for 5 min, linked to the turns dashboard.
- Endpointing comes before every framework layer (`FinalFirstSTT` holds end of speech until the
  final). An STT adapter that holds it exposes `take_endpoint()`
  (`dafter_providers/endpointing.py`), and the row adds `endpointMs` and `replyGapMs`, both from the
  user's last voiced audio.
- livekit-agents keeps no assistant message for a reply stopped before its first synchronized word;
  the worker still reports that turn from `speech_created`.
- Serial turn: the first sentence reached TTS over 500 ms after the LLM's first token while that
  sentence was under 80% of the reply. `agent.turn_metrics` reports it in `serial` (rule in
  `timing.py`); the test client marks the row and Prometheus counts it.
- The framework publishes user and agent transcripts on the text stream topic `lk.transcription`.
