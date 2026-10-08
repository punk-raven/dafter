---
paths:
  - "python/dafter_runtime/src/dafter_runtime/{plan,worker,personas,own_voice,addressing,called}.py"
  - "python/dafter_runtime/src/dafter_runtime/{listeners,barge_in,naming}.py"
  - "python/dafter_runtime/src/dafter_runtime/{timing,tools,toolbox,consent}.py"
  - "python/dafter_runtime/tests/test_{plan,worker,personas,own_voice,own_voice_session,naming}.py"
  - "python/dafter_runtime/tests/test_{called,called_timing}.py"
  - "python/dafter_runtime/tests/test_{listeners,barge_in,barge_in_resume,awake,tools}.py"
  - "testdata/addressing/**"
  - "go/internal/config/addressing_test.go"
  - "python/dafter_core/tests/test_addressing.py"
  - "go/cmd/dafter-control/agent-{addressing,refusal}.js"
---

# Rules enforced in code: agent worker

Identical on both halves (Go and Python) unless stated. Index:
`.agents/skills/project-context/SKILL.md`.

## Worker plan and refusals

- The worker refuses a job it cannot run before it joins (`plan`): pool mismatch; non-cascaded mode;
  e2ee without a worker credential or under a key model other than `server_shared`; an unregistered
  provider; a language a provider does not declare; a turn strategy it cannot run; a local VAD the
  pipeline does not name; an unknown persona; addressing `on_device`; language switching with an STT
  that cannot identify a language, or a listed language without a persona or provider.
- Every refusal, a stage that cannot be built after accepting (a missing provider key) and a
  withheld key after accepting are posted to `POST /sessions/{id}/agent/refusal` as an error
  document. `GET /sessions/{id}` returns it as `agentRefusal` until the next invite; the test client
  shows it (`agent-refusal.js`).
- Providers are constructed and TTS prewarmed before `ctx.connect`.
- The worker strips `lk.pii.*` fields (transcripts) from framework log records and exports traces
  with PII off.

## Agent identity

- Agent name: `agent.name` (catalog: Nivya); nothing else names the agent.
  - The worker joins under it; the tile, transcript and caption labels read the participant name.
  - `persona_for` puts it in every persona and greeting. A greeting uses the language's own script
    when `addressing.aliases` has that spelling (`spelled_for`).
  - The name matcher listens for it (`personas.py`, one `Script` per persona and language).
- The worker says the greeting only when `agent.greets` is true (default off).
- "Dafter" is the product name, never the agent's.
- Voice: Sarvam `priya` in every language (also the provider's fallback). Every line the agent
  speaks or is told to speak refers to the agent in the feminine (`FEMININE` in `personas.py`,
  pinned by `test_personas.py`).

## Own voice

- Every hearing path runs `OwnVoice.hearing`
  (`python/dafter_runtime/src/dafter_runtime/own_voice.py`) first. A microphone can return the
  agent's voice (speakerphone, phone line, meeting room: no echo cancellation on that path). A
  listening path that skips it makes the agent answer and interrupt itself.
  - A transcript whose speech began while the agent spoke and that is mostly the agent's recent
    words is dropped.
  - Once a participant has echoed, voice activity alone no longer pauses or cuts the agent, and a
    fragment over it needs three new words.
- `dafter-evals --echo <gain>` reproduces such a line.

## Addressing

- `agent.addressing` decides when the agent speaks (privacy consequences: `docs/dafter.md`,
  Recording, encryption, and governance). Catalog default: `transcript`. A session answers every
  turn only when it states `always` (the evals harness does: `session_overrides` in
  `dafter_evals/connect.py`).
- The agent listens for `agent.name`, other spellings in `addressing.aliases`. The Sarvam realtime
  STT takes the name as its `prompt`; it has no key term list.
- `always`: one AgentSession linked to one participant.
- `transcript`: a voice session (LLM and TTS, no audio in, manual turns, started first so it is the
  primary) plus one listener AgentSession per human (`listeners.py`, record=False, its Agent raises
  StopResponse); livekit-agents links a session to one participant.
  - The listeners take the turns with the session's turn handling and share the job's one VAD and
    turn detector (`listening` in `called.py`). The voice session keeps preemptive TTS.
  - `barge_in.py` pauses and resumes the voice session's room output (the framework cannot resume a
    reply across sessions): the caller's listener pauses the reply once the caller has talked for
    `interruption.minDurationMs`; a word heard (and `minWords`) cuts it; an acknowledgement that
    listener dropped resumes it at once; silence resumes it after `falseInterruptionTimeoutMs`.
    Others' speech never touches it.
  - The turn reaches the voice session as a user message carrying the listener's speech start and
    stop, end of turn and transcription delays (`heard` in `timing.py`), so `agent.turn_metrics`
    keeps every layer and `e2eLatencyMs`.
  - `called.py` wires them; `addressing.py` (Gate) decides dormant or awake; `naming.py` hears the
    name (vectors in `testdata/addressing/`).
- A called session's `agent.state_changed` carries `dormant`, `wokenBy` (participant id) and
  `wokenVia`. The test client's Wake button sends `{"action":"wake"}` on data topic `dafter.agent`;
  the sender is the packet's participant.
- Tools go through `tools.py` (Registry): speed and effect classes enforced in code; external and
  binding tools wait for the caller's own spoken yes (`consent.py`); binding tools also need an
  attested role, which the worker does not have yet; calls run one at a time.
