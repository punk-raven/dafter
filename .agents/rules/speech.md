---
paths:
  - "python/dafter_providers/src/dafter_providers/sarvam/*.py"
  - "python/dafter_providers/src/dafter_providers/silero/*.py"
  - "python/dafter_providers/src/dafter_providers/{styled,endpointing}.py"
  - "python/dafter_providers/tests/test_{sarvam,sarvam_llm,realtime,sentences,silero,voices}.py"
  - "python/dafter_providers/tests/test_endpointing.py"
  - "python/dafter_runtime/src/dafter_runtime/{backchannel,lexicon,speech_plan,delivery}.py"
  - "python/dafter_runtime/src/dafter_runtime/spoken_*.py"
  - "python/dafter_runtime/tests/test_{backchannel,backchannel_session,speech_plan,delivery}.py"
  - "python/dafter_runtime/tests/test_spoken_languages.py"
  - "python/dafter_runtime/tests/test_{turn_detection,text}.py"
  - "testdata/speech/**"
  - "schemas/config/v1/backchannel.schema.json"
  - "go/internal/config/speech*.go"
  - "python/dafter_core/src/dafter_core/speech.py"
  - "python/dafter_core/tests/test_speech.py"
  - "go/cmd/dafter-control/agent-speech.js"
---

# Rules enforced in code: Sarvam and speech

Identical on both halves (Go and Python) unless stated. Index:
`.agents/skills/project-context/SKILL.md`.

## Sarvam

Code: `python/dafter_providers/src/dafter_providers/sarvam/`.

- LLM: `https://api.sarvam.ai/v1` (the plugin's default `/v2` is beta-gated), `reasoning_effort`
  null (with thinking on, the whole token budget goes to reasoning and nothing is said).
- `FinalFirstSTT` holds end of speech until the final transcript (Sarvam sends `vad.speech_end`
  first; the framework would commit a stale transcript). It releases end of speech with the final
  and waits `finalGraceMs` (STT option) only for a late final.
- `SentenceTTS` ends sentences at the danda, so TTS starts on the first sentence.
- bulbul:v3 takes pace (0.5 to 2), temperature (0.01 to 1 on the websocket) and a pronunciation
  dictionary's `dict_id` (TTS options `pace`, `temperature`, `styles` per situation,
  `dictionaryId`); no pitch or loudness.
- TTS option `firstSentenceAlone` sends a reply's first sentence to synthesis on its own, flushed at
  once however short. Without it Sarvam holds text under `minBufferSize` and the tokenizer holds a
  sentence under 20 characters until the next one ends.
  - livekit-agents drops text pushed after a stream's `flush()`, so `FirstSentenceAlone` in
    `sentences.py` sends the sentinel itself and ends only that segment on Sarvam's first `final`.
- Text preprocessing is always on.
- Catalog turn strategy: `semantic` for en-IN and hi; `provider_endpointing` (Sarvam's server VAD
  ends the turn, the local Silero VAD in `dafter_providers/silero` only catches interruptions) for
  mr, te and kn. `semantic` runs `turn.detector` (`TURN_DETECTORS`, `registry.py`) where it covers
  the language: `livekit` (default, `v1-mini`, `LOCAL_LANGUAGES`: en, hi) or `smart_turn` (Smart
  Turn v3.2 trial for mr, off unless a session states it; weights pinned by revision and SHA-256,
  fetched only under `DAFTER_SMART_TURN_PREWARM=1`: `providers.md`; a worker without them refuses
  the job at `/turn/detector`). No detector covers te or kn.
- Local VAD: Silero options (`activationThreshold` 0.5, `minSilenceMs` 250, `prefixPaddingMs` 500,
  the installed `inference.VAD` defaults) per language in catalog `tuning`; change one only on
  `vadscore` sweep data. Licence unchecked: MIT in repo metadata, CC BY-NC 4.0 in the README.
- STT option `mode`: `transcribe` or `codemix` (English words kept in Latin script, finals only).
  The catalog sets codemix for every Indic language.
- The plugin accepts only `saaras:v3-realtime` and its own list of 30 bulbul:v3 speakers.

## Speech (stage 2)

- Config, never constants: `turn.interruption.backchannel` and `agent.speech` in the schema, values
  in the catalog's defaults layer (a session may override them), TTS voice options (`pace`,
  `temperature`, `styles`) in each language's overlay beside its pipeline.
- Acknowledgements:
  - The local VAD still pauses the agent at once.
  - `backchannel.py` sieves the recognizer's events in the agent's `stt_node` (the listeners' too)
    and drops an utterance of listed phrases said while a reply is pending or playing; it never
    becomes a turn or history.
  - `lexicon.py`: affirmatives `backchannel.words`, `backchannel.negatives` (`reviewed` all false).
    A negative ("no no", "ruko", "नहीं") is never an acknowledgement. `cue`: silent, affirmative,
    negative, continued (an affirmative then real words) or speech; negative and continued yield.
  - `acknowledged(..., cuts=True)` cuts the playing reply on a yielding transcript, below
    `minWords`. The agent's `stt_node` (always mode, web and phone) sets it; listeners do not
    (`barge_in.py` cuts there).
  - livekit-agents resumes the paused reply after `falseInterruptionTimeoutMs` (always mode; its
    default `aec_warmup_duration` also ignores barge-in for 3 s from the first time the agent speaks
    in a session).
  - Answer exception: when the reply then plays to its end as a question (text ends in `?`) within
    `backchannel.answerWithinMs` of played audio and the caller says nothing more, the held
    utterance is released into the same recognizer stream as their turn. `SessionFloor` counts
    played time from `agent_state_changed`; a paused reply's silence does not count.
- Text: `speech_plan.py` is the last `tts_text_transforms` entry, after the framework's markdown and
  emoji filters. It holds text to sentence ends, strips list numbering, applies `substitutions`, and
  under normalization `platform` applies the language's rules (`NORMALIZERS`: en, hi, kn, mr, te,
  in `spoken_<language>.py` on shared `spoken_rules.py`; vectors in `testdata/speech/`, the en, kn,
  mr and te files `reviewed: false` until a native speaker checks them; Hindi personas write digits,
  the others words). `provider` leaves digits to TTS.
- Voice: `delivery.py` classifies the caller's last turn (concern, greeting, neutral; the opening is
  a greeting); the agent's `tts_node` calls `style()` on a TTS implementing
  `dafter_providers.Styled`.
- Fillers:
  - After `fillers.afterMs` of `thinking` with no reply audio ready, the reply's `tts_node` plays a
    phrase synthesized at session start ahead of the reply, never while anyone in the call is
    speaking (`Filler.hears` follows the session and every listener).
  - The registry's slow tools use the same phrases and delay.
  - Stored filler frames carry no synthesis stamp. On a filler turn `agent.turn_metrics` says
    `filler: true`; e2eLatencyMs and replyGapMs count to the filler; ttsNodeTtfbMs and llmNodeTtfsMs
    are the reply's own (`Filler.reply_layers`).
- `agent.speech.expressive` is passed to `AgentSession(expressive=...)`; livekit-agents 1.8.3 keeps
  it off for any TTS without a markup dialect, Sarvam included.
