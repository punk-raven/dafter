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
- With `DAFTER_METRICS_PORT` set, the worker also serves them to Prometheus (`metrics.py`) through
  the framework's own server in multiprocess mode (turns run in job processes).
  - Scrape job `dafter-agent` at the compose network's pinned gateway 10.213.0.1;
    `host.docker.internal` is the desktop machine on Docker Desktop.
  - Labels: language, channel, stage, provider/model; never a session id.
  - A session creates every series it can produce at zero, or `rate()` loses its first observation.
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
