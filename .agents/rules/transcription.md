---
paths:
  - "python/dafter_runtime/src/dafter_runtime/{captions,transcribing,scribing}.py"
  - "python/dafter_runtime/tests/test_{captions,transcribing,scribing}.py"
  - "python/dafter_batch/**/*.py"
  - "python/dafter_scribe/**/*.py"
  - "python/dafter_providers/src/dafter_providers/batch.py"
  - "python/dafter_providers/src/dafter_providers/sarvam/batch.py"
  - "go/internal/control/{transcripts,transcripts_test,transcript_versions_test,minutes}.go"
  - "go/internal/control/{minutes_test,attribution_test}.go"
  - "go/internal/transport/{presign,presign_test,recordings,recordings_test}.go"
  - "go/internal/state/{transcripts,transcripts_test,minutes}.go"
  - "testdata/{transcription,scribe,sarvam-batch}/**"
  - "schemas/events/v1/{transcript,scribe}.schema.json"
  - "schemas/config/v1/scribe.schema.json"
  - "go/cmd/dafter-control/client-{captions,scribe}.js"
---

# Rules enforced in code: transcription and scribe

Identical on both halves (Go and Python) unless stated. Index:
`.agents/skills/project-context/SKILL.md`.

## Transcription

`transcription.mode`: `off`, `live`, `after_call`, `both`. Two passes that never mix.

- Live captions: the realtime pass, never stored.
  - `captions.py` follows each stage 3 listener session's `user_input_transcribed` and the voice
    session's text output (`TextOutputOptions.next_in_chain`, released in step with the audio).
  - Called mode goes through `Called`; always mode through `Transcribing` in `transcribing.py`,
    which runs the same listeners for every human without touching addressing (the linked caller is
    heard by two STT streams).
  - Publishes `transcript.partial`/`transcript.final` on `dafter.events` with an opaque segment id,
    the speaker (participant id or agent) and the recognizer. Text is allowed in these events; never
    log it.
- Transcript of record: made after the call from each speaker's own track.
  - `recording/start` for a track looks up its publisher (`RoomService/ListParticipants`,
    `recordings.go` in transport) and stores the speaker on the egress.
  - `GET /sessions/{id}/transcription/sources` (worker credential) returns finished attributed audio
    tracks as S3 SigV4 presigned URLs (`presign.go`, object key from `Egress/ListEgress`
    `file_results`) and lists the rest as pending or skipped.
  - A track egress ends on its own when its speaker leaves, with no media server webhook. The
    sources call asks `Egress/ListEgress` for every attributed audio track and stores an ended one
    as stopped at its `ended_at`:
    - Source: `EGRESS_COMPLETE`, or `EGRESS_LIMIT_REACHED` with a file.
    - Skipped with the reason: `EGRESS_FAILED`, `EGRESS_ABORTED`, a limit with no file, or an egress
      the media server no longer knows.
    - Pending: only one still running.
  - `recording/stop` treats an egress that already ended (stop refused with `failed_precondition`)
    the same way; a stop naming an already stopped one answers it unchanged.
  - `dafter_batch` runs the verbatim (Sarvam mode `verbatim`) and clean (`transcribe`) renderings
    through `batch_for` (`dafter_providers/batch.py`, Sarvam in `sarvam/batch.py`, vectors in
    `testdata/sarvam-batch/`) and posts a `transcript.version_created` envelope to
    `POST /sessions/{id}/transcripts`.
  - That endpoint checks the envelope, its `transcriptHash` (RFC 8785 and SHA-256 with that field
    stripped, `HashWithout`/`hash_document(raw, field)`), the session's `configHash` and every
    recording and speaker against the store, then numbers it. A re-run is a new version; a retry of
    the same hash is the same version.
  - `GET /sessions/{id}/transcripts` lists versions; `.../transcripts/{version}` exports one with
    its hash. Both require the worker credential, the only credential the control plane verifies. A
    participant's media token never grants a transcript read.
  - Tenant API keys (`docs/dafter.md`, tenancy) replace the worker credential for reads once they
    exist.

## Scribe

- `python/dafter_scribe` (`docs/dafter.md` Context and memory): a second AgentServer, hidden,
  publishing no track (`PERMISSIONS` in `worker.py`), subscribing to nothing.
- Reads `transcript.final`, `agent.note_taken` and `session.usage` from `dafter.events`, only from a
  worker (`is_worker` in `listeners.py`: an agent participant or a hidden one; the control plane
  never makes a person hidden) and only for its session.
- Publishes `scribe.notes` every `summaryIntervalMs` (also each rewrite's timeout; a failure keeps
  the last notes), `agent.turn_scored` (stage 4 `Judge`) and `scribe.minutes`.
- Minutes so far on request: `{"action":"minutes"}` on data topic `dafter.scribe`.
- When everyone leaves, `on_session_end` (`after_call`, bounded by `afterCallTimeoutSeconds`) writes
  the final minutes, stores them (`POST /sessions/{id}/minutes`, worker credential both ways,
  `minutes.go`), logs one `session cost` line and runs the stage 7 batch pass once no track
  recording is pending.
- The agent worker keeps the latest `scribe.notes` (`scribing.py`), re-briefs the agent with them
  between turns, and offers `summarize_call`, `take_note`, `list_notes` only when the session has a
  scribe.
- Scribe LLM: Sarvam by default; profiles `scribe-gemini` and `scribe-nvidia` switch to the `google`
  and `nvidia` endpoint vendors. The default refs state only fields every vendor accepts (a merge
  cannot delete a key).
- Both workers number their own envelopes from 0.
