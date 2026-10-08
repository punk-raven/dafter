---
paths:
  - "docker-compose.yml"
  - "deploy/**"
  - "Makefile"
  - "scripts/**"
  - ".env.example"
  - "{go,python}/Dockerfile"
---

# Setup and dev stack

## First run

- `./scripts/setup.sh` once per clone. A fresh checkout does not compile until it runs.

## Dev stack

- `make dev` from a clean clone builds and starts: control plane, LiveKit SFU, Redis, MinIO
  (recordings), Jaeger (tracing), Prometheus, Grafana. The Cloudflare tunnel starts only when `.env`
  sets `COMPOSE_PROFILES=tunnel` with its token (`.env.example`).
- `make dev-down` tears it all down. `make dev-clean` also removes the stack's images (egress alone
  is 4.76 GB). The build cache is shared with other projects; `docker builder prune` stays manual.
- Config: `deploy/livekit.yaml`, `deploy/egress.yaml`. Dev credentials: `devkey`/`secret`.
- Control plane: `http://127.0.0.1:8080`.

### Recording storage (MinIO)

- Recording storage reaches the control plane as `DAFTER_EGRESS_S3_*` (set in `docker-compose.yml`
  to match `deploy/egress.yaml`). Without a bucket the control plane boots and refuses recording
  starts.
- The egress container is on the host network; it reaches the SFU and MinIO at `127.0.0.1`.
- `minio` and `minio-setup` run Chainguard's build (MinIO publishes no public images), pinned by
  digest in `x-minio-image` (the free tier publishes only `latest`).
- It runs as uid 65532: a `minio-data` volume written by the old root image fails with "Unable to
  write to the backend" until `make dev-down` drops it.

### Disk caps

- `minio-setup` expires recordings after 1 day and sets a 2 GiB hard bucket quota. MinIO counts
  usage in a background scan and can overshoot for about a minute; a recording that hits the quota
  fails.
- Prometheus keeps 2 days or 500 MB. Jaeger keeps 10000 traces in memory. Each container keeps two
  10 MB log files (`x-logging`).

### SFU

- The SFU advertises `--node-ip` (`LIVEKIT_NODE_IP`, default 127.0.0.1) on the one published UDP
  port. Anything else in `rtc` (a port range, `use_external_ip`) breaks local media; see the
  comments in `deploy/livekit.yaml`.
- LiveKit metrics are on port 6789, not 7880.

### Compose services: sip, scribe, agent

- `sip`: LiveKit's SIP bridge on the host network (`deploy/sip.yaml`, RTP 10000-10100), log level
  warn (at info it logs every dialed number).
- `scribe`: same image, `PACKAGE` build arg, health port picked by the OS.
- `agent`: the worker on the host network (like egress, to reach the SFU's advertised 127.0.0.1).
  Health port picked by the OS unless `DAFTER_AGENT_HTTP_PORT` names one (the framework's default,
  8081, is the admin listener). Reads `SARVAM_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY` and
  `GEMINI_API_KEY` from `.env`.
