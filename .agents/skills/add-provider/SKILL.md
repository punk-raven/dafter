---
name: add-provider
description: >-
  Fixed checklist for adding a vendor provider (STT, TTS, LLM, MT, VAD, realtime, vision)
  per docs/dafter.md Phase 1 / 1b, plus the exit test that the provider seam has no hole.
---

# Adding a provider

Rule (`docs/dafter.md` section 3 "Provider abstraction", section 6 Phase 1b): a provider is
added by this checklist and nothing else. Touching runtime, control plane or config schema means
the abstraction has a hole; fix the hole first, in its own commit.

Status: the providers package (`python/dafter_providers/` in the section 5 layout) does not
exist yet. Steps marked *(pending)* name design requirements, not paths; fill them in when it lands.

1. *(pending)* New subpackage, one per vendor. Nothing outside it imports the vendor SDK or
   names the vendor (Go precedent: `no-vendor-sdks` in `go/.golangci.yml`).

2. *(pending)* Implement the stage contract. Invariants: streaming, cancellable within 100 ms,
   transparent reconnect, backpressure aware, no global state, config validated at construction.

3. Map errors to the taxonomy: `ErrorCode` and `Stage` from
   `schemas/errors/v1/error.schema.json`. A missing code is a `schema-change`, decided
   separately. Messages safe to log.

4. Register by name so switching is a config edit: `agent.pipeline.<stage>.provider`
   (`vad|stt|llm|tts|mt|realtime`), a `ProviderRef` in
   `schemas/config/v1/resolved-session-config.schema.json`; name matches
   `^[a-z][a-z0-9_]{1,31}$`, model pinned. *(pending)* Registry lives in the providers package.

5. Credentials only as `secret://` references (`SecretRef` in
   `schemas/common/v1/ids.schema.json`), resolved at construction. Never a literal.

6. `region` is an opaque provider-scoped token (why: commit body `fix(schemas): correct
   region, hash, closure and event id width`); declare the tokens served, residency filters
   on `residency.allowedRegions`.

7. *(pending)* Declare machine-readable capabilities (languages, streaming, partials, endpointing,
   translate mode, timestamps, diarization, live reconfig, sample rates, encodings, regions).

8. *(pending)* Pass the shared contract test suite unchanged; add a latency benchmark.

9. Exit test, must be empty apart from files the providers package owns:

   ```sh
   git diff --stat origin/main...HEAD -- schemas/ go/ python/dafter_core/
   ```

Fallback rules the adapter must respect: `docs/dafter.md`, "Fallback and the partial-output guard".
