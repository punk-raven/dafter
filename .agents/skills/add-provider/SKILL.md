---
name: add-provider
description: >-
  The fixed checklist for adding a vendor provider (STT, TTS, LLM, MT, VAD,
  realtime, vision) to Dafter, as docs/dafter.md Phase 1 and Phase 1b demand.
  Use when adding or reviewing a provider adapter, and as the exit test that
  the provider seam has no hole. Steps are marked pending until the providers
  package lands.
---

# Adding a provider

The design (`docs/dafter.md`, "Provider abstraction" in section 3 and
Phase 1 / 1b in section 6) fixes the rule: a provider is added by following
this checklist and nothing else. **If adding one requires touching the
runtime, the control plane or the config schema, the abstraction has a hole,
and the hole gets fixed before the provider lands.** Phase 1b does not exit
until that holds for a second provider per stage.

## Status of this checklist

The providers package (`python/dafter_providers/` in the target layout,
section 5 of the design) does not exist on this branch yet. Phase 1 delivers
it with one provider per stage and the full contract test suite. Until then
the steps below are the design's requirements, not paths you can open:

- **Available now:** the config contract (step 4), the credential rule
  (step 5), the residency token (step 6), the error taxonomy (step 3), and
  the exit test (step 9).
- **Pending the providers package:** the subpackage location, the contract
  types, the contract test suite, the registry and the benchmark harness
  (steps 1, 2, 7, 8). When they land, replace the placeholders here with the
  real paths and remove this notice.

## Checklist

1. **New subpackage, one per vendor.** *(pending)* One directory per vendor
   under the providers package. Nothing outside that subpackage may import the
   vendor's SDK or know the vendor's name: the design enforces this with an
   import linter and a CI grep for vendor imports. The Go side already has the
   pattern in `go/.golangci.yml` (`no-vendor-sdks`).

2. **Implement the stage contract.** *(pending)* Implement the Protocol for
   each stage the vendor serves. The contract invariants, from the design:
   streaming (never buffer the full input), cancellable within 100 ms,
   transparent reconnect, backpressure aware, no global state, config
   validated at construction rather than first use, standard metrics emitted
   including the provider request id.

3. **Map errors to the Dafter taxonomy.** No vendor exception escapes. The
   codes are the `ErrorCode` enum generated from
   `schemas/errors/v1/error.schema.json`; the `stage` field of an error uses
   the `Stage` enum from the same file. If the taxonomy lacks a code you need,
   that is a schema change (`schema-change` skill), decided on its own merits,
   not smuggled in with the adapter. Messages must stay safe to log: no name,
   email, phone or transcript content.

4. **Register by name, so switching is a config edit.** The resolved config
   names a provider per stage at `agent.pipeline.<stage>.provider`
   (`vad`, `stt`, `llm`, `tts`, `mt`, `realtime`), a `ProviderRef` with
   `provider`, `model`, `region`, `credentialRef` and `options` (see
   `schemas/config/v1/resolved-session-config.schema.json`, `$defs/ProviderRef`).
   The name must match `^[a-z][a-z0-9_]{1,31}$`. Providers are named by
   variable and pinned by model version, so a vendor upgrading a model cannot
   silently change a consumer's agent. *(pending)* The registry the name
   resolves through lives in the providers package.

5. **Credentials only via `secret://` references.** `credentialRef` is a
   `SecretRef` (`schemas/common/v1/ids.schema.json`); an inline key fails
   validation. The adapter receives a reference and resolves it at
   construction; it never reads a key from config, an env file in the repo or
   a literal.

6. **Region token for residency.** `region` is an opaque, provider-scoped
   token, deliberately not matched against one cloud's naming (see the schema
   description and the commit body of `fix(schemas): correct region, hash,
   closure and event id width`). Residency filters providers to
   `residency.allowedRegions` at resolution time, so the adapter must declare
   which region tokens it serves.

7. **Declare capabilities.** *(pending)* Machine-readable: languages,
   streaming, partials, native endpointing, translate mode, timestamps,
   diarization, live reconfiguration, sample rates, encodings, regions, max
   stream duration. These resolve `turn.strategy: auto`, prune fallback
   ladders and filter by residency, so an undeclared capability is a
   capability the platform will not use.

8. **Pass the contract test suite unchanged, and add a latency benchmark.**
   *(pending)* The shared suite is what makes the seam real. An adapter that
   needs the suite edited to pass has found a hole in the suite or in the
   adapter, never a reason to special-case.

9. **Exit test: nothing else changed.** Before opening the PR:

   ```sh
   git diff --stat origin/main...HEAD -- schemas/ go/ python/dafter_core/
   ```

   must be empty, apart from files the providers package itself owns once it
   exists. Any change under `schemas/`, in the Go control plane, or in the
   runtime to accommodate the new provider is a hole in the abstraction. Fix
   the hole in its own commit, with its own reason in the commit body, then
   rebase the provider on top.

## Fallback rules the adapter must respect

From the design's "Fallback and the partial-output guard": TTS falls back at
utterance boundaries only, LLM only if no tokens were spoken, MT freely, and
STT never (a mid-utterance recognizer switch corrupts the transcript and turn
state). Fallbacks are capability-checked before use. An adapter reports where
it is in an utterance honestly so the runtime's commit point is correct.
