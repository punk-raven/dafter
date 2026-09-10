# Dafter - Plan

**A reusable realtime AI media toolkit.** Gives any application realtime audio/video sessions with AI participants: talking agents, live translation, live transcription, secure recording. Applications configure it. They do not implement pipelines, talk to model vendors, or touch the media server.

Dafter is the product. Applications are consumers. No single consumer's requirements shape the core.

> **Single self-contained context file.** This bundles the full Dafter design (problem, definition, architecture, features), the concrete infrastructure and engineering standards, and the phased delivery plan into one document, intended as AI-agent context for the Dafter repository.

---

## 1. Problem statement

Without a platform, every application that wants an AI voice agent in a video call rebuilds the same stack: token minting, room lifecycle, agent worker deployment, an STT/LLM/TTS pipeline, voice activity detection (VAD), turn detection tuning, interruption handling, transcription publishing, translation fan-out, recording orchestration, consent capture, retention, provider failover, latency instrumentation, and evals. That is months of work, repeated per application, diverging immediately.

With a platform: create a session via one API, join with one client SDK, subscribe to typed events, and control behaviour with a config document.

```
   app A          app B          app C
     └──────────────┴──────┬───────┘   one API, one SDK, one config model
                    ┌──────▼──────┐
                    │   DAFTER    │
                    └──────┬──────┘
        ┌─────┬─────┬──────┼───────┬──────────┐
    media server VAD  STT    TTS   LLM/MT    storage
    (SFU, TURN)
```

**Everything that varies between applications is configuration or an extension point. Nothing is a fork.**

The design is validated against several consumer shapes, not one: 1:1 consultation (long sessions, high-value transcript, strict recording), multi-party meeting with interpretation (translation fan-out), inbound support (telephony, high concurrency, cost-sensitive), outbound calling (programmatic initiation, consent), tutoring (vision, no recording), field capture (poor networks), and embedded assistant (agent only, no room). **If a decision only makes sense for one shape, it is a config option, not a default.**

---

## 2. Definition

Dafter is a platform, not per-app code. Consumers see one product: one API, one SDK, one config model. Everything a consumer needs to vary is expressed as configuration or an extension point.

### Key decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | **Cascaded STT -> LLM -> TTS is the default** | Native transcript for audit, best-of-breed per language, per-stage failover, debuggable |
| 2 | **Half-cascade and speech-to-speech are provider modes** | A realtime provider replaces STT+LLM+TTS (or STT+LLM only). Switching is config, not a rewrite |
| 3 | **AI agents are session participants** | They inherit media plumbing, permissions, and observability for free |
| 4 | **Four planes: control, media, agent, processing** | They scale on four different signals. Conflating any two causes waste or outage |
| 5 | **Control plane never sits on the realtime path** | Every DB query or service hop per turn is latency the budget cannot absorb |
| 6 | **Every vendor behind a Protocol, enforced in CI** | Swapping a provider is a config edit. The control plane never imports a vendor SDK |
| 7 | **Language and channel are first-class config axes** | Turn strategy, VAD constants, voices, providers all vary by both. Overlays compose |
| 8 | **VAD is a first-class, provider-agnostic stage** | Any VAD LiveKit supports is usable and configurable, independent of the STT/LLM/TTS providers |
| 9 | **The SFU sits behind a thin seam, not a portable abstraction** | Control-side operations only, so the platform is testable without a live media server. Deliberately not wide enough to swap SFUs: a seam hiding LiveKit's room, track, dispatch, and egress-layout model would either leak it or be unimplementable elsewhere. **LiveKit is a load-bearing dependency** |
| 10 | **Multi-tenant from line one** | Retrofitting tenancy is a rewrite |
| 11 | **Raw artifacts immutable, transcripts derived** | A better model can regenerate the canonical transcript later without falsifying the original record |
| 12 | **Self-hosted LiveKit OSS is the reference topology; where it runs is the operator's choice** | No third party **terminates** media; a managed relay may forward it, and forwards ciphertext only. Not tied to any region or cloud - every external dependency is a configured resource, so relocating a deployment is a config change, not a rewrite. Managed LiveKit Cloud stays a supported deployment of the same media server, which is not a claim that another SFU could be dropped in |
| 13 | **Recording and governance ship before the voice agent** | A recorded, consented, evidence-chained call is the first thing a consumer can launch on. The agent is valuable but not the gate |
| 14 | **Recordings are sealed by the platform, keys are configurable per tenant** | Egress cannot apply per-object envelope encryption itself, so sealing is an explicit pipeline stage, not an assumption |
| 15 | **Two channels: a live main channel and a parallel background channel** | The conversational turn runs on the main channel under the latency budget; everything that can run in parallel - egress and sealing, the batch transcript, translation fan-out, persistence, metrics, and VAD/audio analysis - runs on the background channel as separate workers, so the conversation never blocks on background work and a slow background worker degrades an artifact, never the call |

### Cascaded vs half-cascade vs speech-to-speech

| | Cascaded | Half-cascade | Speech-to-speech |
|---|---|---|---|
| Shape | audio -> STT -> LLM -> TTS | audio -> realtime model -> **text** -> your TTS | audio -> one model -> audio |
| Swapping | Per stage | Understanding locked, voice free | All or nothing |
| Native transcript | Yes | Output only | No |
| Prosody into reasoning | **Lost** (text has no tone) | Preserved | Preserved |
| Voice control | Full | **Full** | Vendor voices only |
| Tool calling | Mature | Model dependent | Weaker |

Half-cascade is the useful middle ground: it recovers the one thing cascaded loses (the agent cannot hear that a user sounded upset or hesitant) while keeping one consistent voice across languages under our cost control. It stays a first-class mode.

### Voice activity detection (VAD)

VAD is a **first-class stage of the pipeline and is not tied to any provider**. Dafter supports every VAD that LiveKit supports (for example Silero), selected and tuned through config. A provider's own server-side VAD is one option among several, never a requirement: the core conversational loop is

```
USER -> VAD -> AUDIO -> STT -> LLM -> TTS
```

VAD gates the user's audio into the recognition path, so it belongs to the platform pipeline rather than to any single STT/LLM/TTS vendor. Provider-specific VAD tuning (for example turning a local VAD off when a provider's server VAD is authoritative) is a configuration detail of that provider, covered in the infrastructure document, not a property of the pipeline.

VAD is first-class rather than an STT afterthought because it drives interruption handling, which is what makes a voice agent feel human. Concretely it delivers:

- **Faster interrupt identification** - the agent stops talking the instant the user genuinely takes the floor, not a beat later.
- **Clarity between interruptions and background voices** - a colleague, TV, or street noise does not derail the turn.
- **A real interrupt versus backchannel and fillers** - "mm-hm", "right", "okay", "uh" are the user affirming or thinking, not taking the turn; the agent keeps talking through them instead of stopping on every affirming noise.
- **Speech disfluencies** - false starts, repetitions, and hesitations are recognised as one continuing turn, so endpointing and turn detection do not cut the user off mid-thought.

These are why VAD is tuned before endpointing (they stack) and why it sits ahead of STT and the turn detector in the pipeline.

---

## 3. Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│ CONSUMER APPLICATIONS   client SDK · admin SDK · REST/WS           │
└──────────────────────────┬─────────────────────────────────────────┘
                           │ Dafter API key, tenant-scoped
┌──────────────────────────▼─────────────────────────────────────────┐
│ CONTROL PLANE                          (stateless, horizontal)     │
│  sessions · tokens · dispatch · recording · consent · config       │
│  policy enforcement · webhooks · metering · capability lookup      │
└──────┬──────────────────────────┬────────────────────┬─────────────┘
       │  hot state · events · queues · locks           │
┌──────▼────────────┐  ┌──────────▼─────────┐  ┌────────▼──────────┐
│ MEDIA PLANE       │  │ AGENT PLANE        │  │ MEDIA PROCESSING  │
│ SFU · TURN · SIP  │◄─┤ agent servers      │  │ egress · ingress  │
│                   │  │ job subprocesses   │  │ post-processing   │
│                   │  │  pipeline          │  └────────┬──────────┘
│                   │  │  behaviors · tools │           ▼
└───────────────────┘  └──────────┬─────────┘     object storage
                                  │
                       ┌──────────▼──────────┐
                       │ PROVIDER LAYER      │  in-process library,
                       │ STT·TTS·LLM·MT      │  strict interface boundary
                       │ Realtime·Vision·VAD │
                       └─────────────────────┘
```

| Plane | Scales on | Blast radius |
|---|---|---|
| Control | Request rate | API down, live sessions unaffected |
| Media | Bandwidth, connections | Live media drops |
| Agent | Concurrent AI sessions (CPU + memory) | Agents drop, media continues |
| Processing | Job queue depth | Recordings delayed |

The four planes exist because they scale on four different signals. Conflating any two causes either waste or an outage.

### The hot path rule

**The control plane may configure the media and agent planes. It must never sit inside them.**

```
✗  audio -> API gateway -> database -> service -> queue -> model
✓  audio -> agent worker (in-process pipeline) -> model
                  └─ async, off the hot path ─► events, metrics, persistence
```

Config is resolved once at session start and held in worker memory. Policy is decided before the session or between turns. Persistence is fire-and-forget into the event stream. Live changes arrive as **events the worker applies between turns**, never lookups it performs during one. The provider layer is a **library, not a service**, because a network hop per stage per turn would blow the latency budget. It is still a strict boundary, enforced in CI.

### Main channel and background channel

Work splits into two channels by one test: **is the user waiting on it to hear the next word?**

**Main channel** - the conversational hot path, bound by the latency budget: audio in, VAD, STT, turn detection, LLM, speech planning, TTS, audio out. Nothing here waits on a database, a disk write, or a job queue.

**Background channel** - everything that can run in parallel or after the fact, as **separate workers** off the hot path: egress/recording and the seal stage, the accurate batch transcript, translation fan-out, persistence and event publishing, metrics and traces, and retrieval from slow sources (bounded by a hard deadline). A slow or failed background worker degrades an artifact, never the conversation - the recording is delayed, the call is not.

**VAD is the boundary case.** It runs continuously and **in parallel** with the turn - it is not a step the request/response blocks on - but its interrupt signal has to reach the loop within the barge-in budget, so it runs alongside the main channel and feeds it events, rather than behind a job queue like egress. The principle is: parallelise everything that can be parallelised, push heavy or deferrable work (egress, batch transcription, translation, persistence) onto background workers, and keep only the strict turn on the main channel.

### The agent pipeline

Core conversational loop:

```
USER -> VAD -> AUDIO -> STT -> LLM -> TTS
```

Assembled from config, with the full set of stages:

```
AudioIn -> [AEC] -> [VAD] -> STT -> TurnDetector -> [PreemptiveGate]
   -> ContextBuilder -> LLM -> [ToolExecutor] -> SpeechPlanner -> TTS -> AudioOut
```

Every stage is **streaming, cancellable, instrumented, and replaceable**. With a realtime provider the STT/LLM/TTS stages collapse; the rest is unchanged. VAD is a first-class stage here (see Definition), independent of which STT/LLM/TTS providers are configured: it drives interrupt detection - identifying interrupts faster, separating real interrupts from background voices, backchannels, and fillers, and recognising speech disfluencies - and feeds the turn detector and preemptive gate downstream.

### Provider abstraction

Every vendor sits behind a contract. Nothing outside a provider's own subpackage knows a vendor name. The contract types cover STT, TTS, LLM, MT, a realtime provider (for speech-to-speech and half-cascade), vision, and VAD.

**The media transport is a different kind of seam.** It covers session creation, token minting, agent dispatch, egress control, and webhook ingest - enough to test the platform without a live media server, and not a portability claim (key decision 9).

**Contract invariants**, verified by a shared test suite every adapter must pass unchanged: streaming (never buffer the full input), cancellable within 100ms, vendor errors mapped to the Dafter taxonomy so no vendor exception escapes, transparent reconnect, backpressure aware, emits standard metrics including provider request IDs, no global state, and config validated at construction rather than first use.

**Adding a provider is a fixed checklist:** new subpackage, implement the contracts, declare capabilities, map errors, register, pass the contract tests, add a latency benchmark. Nothing else changes anywhere. **If adding a provider requires touching the runtime, control plane, or config schema, the abstraction has a hole and the hole gets fixed.**

**Capability negotiation.** Each provider declares machine-readable capabilities: languages, streaming, partials, native endpointing, translate mode, timestamps, diarization, live reconfiguration, sample rates, encodings, regions, and max stream duration. These resolve `strategy: auto` per language, collapse STT+MT where a provider supports translate mode, filter candidates by residency policy, prune fallback ladders to compatible members, and plan reconnects for long sessions. Capabilities are exposed to consumers so they plan their UI instead of guessing.

**Fallback and the partial-output guard.** Providers are health-checked per region with a circuit breaker and half-open probe. Fallbacks are capability-checked before use, since falling back to a provider that lacks the current language is worse than failing cleanly. Mid-session: TTS falls back at utterance boundaries only, LLM only if no tokens were spoken, MT freely, and **STT never** (switching recognizers mid-utterance corrupts the transcript and turn state, so STT failure escalates to degradation).

| State at failure | Action |
|---|---|
| No output emitted | Switch silently |
| At a clean boundary | Switch, optionally with a short bridge |
| Mid-utterance | Do **not** switch mid-stream. Complete or cleanly abandon, then switch for the next |

**Naive failover is worse than the failure it hides.** A TTS provider dying mid-utterance and switching instantly gives the user a voice change mid-word, which is more alarming than a two-second gap. The runtime tracks a **commit point** (how much output the user actually heard); it is the same mechanism interruption handling uses.

### Configuration model

The config model is the heart of the SDK. Resolution is **layered and deterministic**: Dafter defaults, then tenant config, then a named profile, then session overrides. Same inputs, same output. The resolved document is stored with the session and hashed, so any session is explainable and replayable.

**Composition axes prevent config explosion:**

```
resolved = base ⊕ language_overlay[lang] ⊕ channel_overlay[channel] ⊕ role_overlay[role]
```

A Hindi agent on telephony is a product of two overlays, not a fifth copy-pasted document. Adding a language is one overlay. Adding a channel is one overlay. Adding both is zero extra work.

What matters: every knob affecting perceived quality is expressible and nothing important is hardcoded; providers are named by variable so config is portable; **secrets are references, never values**, so config documents are safe to store, diff, log, and hand to a consumer; `turn.strategy: auto` resolves through capabilities so consumers need not know which strategy suits which language; budgets live in config so alerting is automatic; and handoff emits an event, because Dafter has no opinion on what escalation means in a consumer's product.

Config is **validated three times:** on write (rejected at edit time), on resolution (a capability check, so asking a provider for an unsupported language is an error now), and at worker boot (bad config kills the worker at startup, never mid-session).

### State, events, and API

**Storage rule:** if losing it would matter tomorrow, it goes in the relational store; if it only matters for the next thirty seconds, it goes in the hot store. The hot store holds live session state, presence, locks, queues, circuit-breaker state, rate limits, and event streams. The relational store is the system of record for sessions, transcripts, consent, audit, config, and usage.

**Events are streams, not pub/sub.** Fire-and-forget pub/sub silently drops messages when a consumer is down, which is unacceptable for `recording.completed` or `transcript.final`. The event system is durable within a retention window, with consumer groups, at-least-once delivery and explicit acknowledgement, visibility into stuck consumers, replay, and ordering within a session partition. It sits behind an interface so the backend can move to a dedicated log service later.

Event families: `session.*`, `session.signal`, `connection.*`, `agent.*`, `transcript.*`, `translation.*`, `recording.*`, `provider.*`, `policy.*`, and `budget.*`. **`agent.state_changed` (`idle | listening | thinking | speaking`) is the most important event for user experience:** a user who can see "thinking" waits, while a user staring at silence talks over the agent and breaks the conversation. Delivery is websocket in-session and signed webhooks server-side (HMAC, timestamped, replay-protected, backoff, dead-lettered, idempotency keys).

**API surface (conceptual).** A REST and websocket surface covers session lifecycle (create, get, delete, end, signal), role-scoped short-TTL tokens, agent management (add, update, remove, say, interrupt, context), live language switch, recording (start requires a consent artifact), consent capture, transcript retrieval (realtime or batch, versioned), audit, recording metadata and manifests, legal hold, config resolution and validation, capability and provider introspection, usage, and a websocket event stream. **Token minting is control-plane only and grants derive from role** (`participant`, `presenter`, `observer`, `agent`, `recorder`), never from the client request; room-admin grants are never issued to a client.

---

## 4. Features

### Latency

Human conversation has a turn gap around **200ms**. That is the bar.

| Percentile | Target (to first agent audio) | Experience |
|---|---|---|
| p50 | < 800ms | Natural |
| p95 | < 1.5s | Acceptable |
| p95 | > 2.5s | Users talk over the agent; conversation collapses |

**Track two latencies separately.** *Platform turn gap* (speech end to first audio produced) is what the pipeline controls; *mouth-to-ear* (speech end to first audio heard) adds network, jitter buffer, and device output. Users experience the second, engineers optimize the first, and reporting only one is how a system that looks fine in dashboards feels slow.

**Rules.** Measure p50 and p95, never means, since an average hides the one call in twenty that lost the user. Variance matters as much as median. Tune VAD before endpointing because they stack. Prewarm connections at worker start. Match codec and sample rate end to end so there are no conversion hops. Cache the prompt prefix. Declare budgets in config so violations alert automatically instead of regressing silently.

**Latency hiding**, all configurable behaviours rather than hardcoded: preemptive generation (start the LLM on the partial transcript, discard if the user continues), filler utterance during slow tools, speculative tool calls for predictable intents, two-tier model routing, clause-level TTS chunking, and async handoff for genuinely long work.

**Parallel over serial - but only where the data actually allows.** The default is to overlap and parallelise; a stage runs serially only where it genuinely needs the previous stage's output. Two distinct mechanisms, and conflating them ships bugs:

- **Streaming overlap** is how the *dependent* chain is made fast. `STT -> turn detection -> LLM -> TTS` is a true data dependency - the LLM cannot run before it has text, TTS cannot speak before it has tokens - so it is never truly parallel. Instead each stage streams its partial output downstream the instant it exists: the LLM starts on the stable partial transcript, and TTS synthesises the first clause while the LLM is still generating the rest. This overlap is the single largest latency lever - on the order of ~1.2-1.4s down to ~500-650ms p95 in LiveKit's own measurements - which is why streaming STT, token-streaming into TTS, and preemptive generation are defaults, not add-ons.
- **True parallelism** is for genuinely *independent* work, run concurrently and merged under a hard deadline: retrieval across multiple sources, translation fan-out (one worker per language), per-track transcription, speculative tool calls for predictable intents, connection prewarming, and provider health probes. This is background-channel work and never blocks the turn.

**What must stay serial** and must not be faked into parallel, because reordering corrupts state: switching the STT recogniser mid-utterance, committing a binding tool call before its confirmation turn, and compacting memory inside a turn rather than between turns. Parallelising these trades a small latency win for a correctness bug - the wrong trade in an evidence-grade product.

### Turn-taking

Every bad voice demo fails here, not on the LLM. Four problems: **endpointing** (finished, or thinking?), **barge-in**, **backchannels** ("mm-hmm" means keep going, not stop), and **echo** (agent hears itself).

| Strategy | Basis | Fits |
|---|---|---|
| VAD | Acoustic only | Baseline. Cannot tell a pause from an ending. First-class and provider-agnostic |
| Provider endpointing | Recognizer's acoustic + linguistic cues | Best where the STT has strong native endpointing, especially outside English |
| Semantic turn detection | Small transformer over the partial transcript | Best for languages it was trained on, usually English |
| Realtime model server VAD | The speech-to-speech model owns it | Opaque, minimally tunable |
| Manual | Client sends boundaries | Push-to-talk, accessibility, kiosk |

**Semantic turn detectors are English-trained and degrade on other languages and code-mixed speech.** So turn strategy resolves **per language** through the capability matrix, not globally. This single fact is why the config model needs a language axis.

Tuning varies by channel (WebRTC, telephony, long-form): silence window, minimum speech, onset threshold, endpointing delay, interruption minimum words, and echo cancellation all differ. **This is why channel overlays exist:** nobody should hand-tune six numbers per application. **The telephony channel is designed for in Phase 1 even though SIP ships in Phase 7**, because it forces narrowband mulaw, diarization instead of per-track attribution, and its own turn constants; an axis shaped only around WebRTC gets redesigned rather than extended. Interruption controls include `enabled`, `mode`, `min_duration`, `min_words`, `false_interruption_timeout`, `resume_false_interruption`, and user-side turn limits. **Non-interruptible utterances are a requirement** for disclosures, consent statements, and one-time codes.

### Cancellation

The most under-designed part of most voice pipelines. On barge-in the TTS cancels and buffered audio is discarded, LLM generation cancels, in-flight tools cancel where they can, and **the chat context is repaired** so the next turn sees coherent history. That last step is the cause of the classic "agent repeats itself after an interruption" bug: the runtime must record how much of the utterance was actually heard and truncate the assistant message to exactly that. This is the same **commit point** the fallback guard reads - one mechanism, two problems.

### Context and memory

Resending the whole conversation every turn is the chat default and wrong for voice: prompt length drives time-to-first-token linearly, so long sessions get slower exactly when the user is most invested. Context is two layers: a short-term window (the last N turns, verbatim, rolling off) plus **session memory that is structured, not prose**. Prose ("the user discussed a scheduling problem") loses what the agent needs to act on; structured memory keeps the goal, confirmed facts and entities, open questions, decisions, and commitments as fields.

**Confirmed entities are marked so the agent stops re-asking** - one of the most trust-destroying behaviours - and commitments become durable obligations rather than sentences that scroll out of context. Structured memory caches with the prompt prefix, survives agent handoff as state, and is inspectable when the agent misbehaves. Compaction runs **between turns, never inside one**. **Retrieval** sits after turn confirmation and before generation, always parallel across sources (four sources at 150ms each run sequentially is 600ms of budget spent on plumbing) and always bounded by a hard deadline, with slow sources dropped from the merge and the omission recorded: a slow index degrades the answer, never the conversation.

### Agent behaviours

The things that make an agent feel human are cross-cutting, so they are composable middleware rather than a monolith inside the agent class.

| Behaviour | Does |
|---|---|
| `Backchannel` | Short acknowledgement on a low-priority path during long user turns. Rate-limited |
| `FillerOnLatency` | Latency-hiding phrase when an operation exceeds a threshold. Rotated |
| `EntityConfirmation` | Detects low-confidence numbers, names, dates; injects a confirmation turn |
| `SilenceRecovery` | Escalating prompts on dead air, then a polite exit |
| `Disclosure` | Required statements spoken, non-interruptible, logged as artifacts |
| `OpeningVariation` | Rotates response openings (identical openings are the strongest robot tell) |
| `HandoffDetection` | Detects escalation triggers, emits an event. Never decides what escalation means |
| `BrevityEnforcement` | Mechanically caps length rather than trusting the prompt |
| `MarkdownStripping` | Removes formatting before TTS reads asterisks aloud |

**Backchannel invariant:** a backchannel must not end the user's turn, reset the endpointing timer, enter the agent-speaking state, or appear in chat history. It is audio on a parallel path from a deterministic policy.

### Speech planner

The last stage before TTS, and **Dafter owns it**, because it is deterministic, high-leverage, and every consumer would otherwise reimplement it badly. *Normalization* turns text into speakable form per language (numbers, currency, dates, phone numbers, codes digit by digit, abbreviations, units, URLs); LLMs are unreliable at this and it is fully deterministic. *Chunking* decides where to cut for TTS: cut too late and you add the whole generation time, cut wrong and prosody breaks. It **must not split** structured references like `12(1)(b)`, decimals, abbreviations, currency and unit pairs, quoted speech, parentheticals, enumerated items, or names with internal punctuation. Separation of concerns: the LLM decides *what* to say, the planner decides *how it is spoken*.

### Tools

Tools carry two classifications. **`latency_class`** governs delay hiding: `fast` (<300ms) runs inline, `slow` engages filler and keeps the floor, `async` acknowledges and completes off-session with an event. **`effect_class`** governs authorization, enforced in code and never left to the prompt: `read` and `draft` run automatically, `external` requires an explicit confirmation turn, and `binding` requires confirmation plus a role check. An effect-class gate is a guarantee where a prompt instruction is only a suggestion, and it is also the injection defence: a user saying "ignore your instructions and send it" still hits a code path that requires confirmation.

**Design tools for voice, not chat.** Voice punishes every round trip, so prefer one coarse, purpose-built call over a chain of fine-grained ones, and return the minimum (`{status, next_appointment}`), not the whole record.

### Language capabilities

**STT is three classes, not one.** Assuming one configuration serves every purpose is a common and expensive mistake.

| Class | Optimized for | Tolerates | Feeds |
|---|---|---|---|
| **Agent** | Lowest first-partial latency, reliable endpointing | Slightly higher error rate | The agent pipeline |
| **Caption** | Recall, stable interim updates that do not flicker | Higher latency | Captions, translation |
| **Batch** | Maximum accuracy, punctuation, diarization, terminology | Seconds to minutes | Canonical transcript |

**Evaluate on our audio, not public benchmarks.** Top providers cluster on clean English; differences appear on accented, noisy, code-mixed, and narrowband audio. Format-invariant accuracy (getting "4471", not "forty four seventy one") matters more than raw word error rate for agents that act on numbers.

**Terminology service.** One Dafter component layers tenant glossary, session terms, and discovered entities, deduped and ranked by expected impact (providers cap the biasing list), and compiled per provider from the capability matrix, with a post-processing correction dictionary where a provider supports no biasing. It **feeds TTS pronunciation too**: one source, two consumers.

### Translation

**Captions (text).** One stream per target language, not per listener, so cost is O(languages) not O(participants). Show untranslated partials for feedback; translate finals only. Collapse stages where the STT supports speech-to-translated-text. Cache recurring phrases.

**Spoken (audio).** One translator agent per target language publishing one track; subscribers select. Duck, do not mute, the original so listeners keep prosody and turn cues. Accept ~1.5-3s lag and set UI expectations. **Never translate the agent's own speech** - generate it natively in the target language.

**Semantic preservation, not fluency.** A fluent translation that inverts a modal is worse than an awkward correct one. Modality, negation, conditionals, quantities, dates, proper nouns, defined terms, structured references, quoted speech, and hedges must all survive. The mechanism is a deterministic post-translation verification pass that extracts these from source and target, compares, and flags mismatches in the transcript rather than shipping them silently, with no extra model call on the hot path. Four timestamps per segment (`t_speech_end`, `t_transcript_final`, `t_translation`, `t_audio_out`) decompose the lag so "translation feels slow" becomes actionable.

### Transcription

**Two-pass.** Realtime STT is measurably less accurate than batch. A realtime pass during the session serves the agent and captions; a batch pass afterward produces the canonical transcript. Store both, mark which is which, and **never present the realtime transcript as final**.

**Speaker attribution.** Prefer per-track transcription whenever participants publish their own audio track: attribution is exact and free. Diarization is only for mixed or telephony audio, selected automatically with an override.

**Raw artifacts are the source of truth; transcripts are derived.** Recorded audio, raw provider events, and provider/model metadata are write-once. Transcripts, translations, summaries, and redactions are regenerable derivatives. When a better model ships later, reprocess the original audio and produce a **new** canonical transcript version with its own provenance, without overwriting the original. Three schema details are easy to skip and expensive to add later: **language at word level** (code-switching is normal), **`normalized_text` alongside `text`** (neither is reliably derivable from the other afterward), and **`source` provenance per segment** (a session that fell back mid-call has segments from two providers).

### Recording, encryption, and governance

| Egress layout | Cost | Best for |
|---|---|---|
| **Track** (raw, no transcode) | Lowest | Audio archives, per-speaker transcription |
| **Track composite** | Low | Per-participant review |
| **Room composite** (headless browser + transcode) | Highest | A single shareable video |

**Default is per-track audio** plus optional composite video. Recording runs as a separate scalable worker service with a job queue.

**A track egress attaches to a published track, so it cannot start before the participant publishes.** That collides with the coverage guarantee, so the layout is chosen per retention class, not globally:

| Coverage need | Layout | Guarantee | Cost |
|---|---|---|---|
| Evidence-grade, no gap tolerated | Room-level egress at session creation | Live before anyone can publish. Leading silence is itself evidence nothing was cut | Higher - a composite pipeline runs the whole session, including while empty |
| Ordinary review and transcription | Per-track, on publish | Capture begins within a bounded delay of the first published track | Lowest - no transcode, per-speaker attribution free |

**The guarantee is a measured number, not a promise.** The manifest records `t_capture_start` alongside `t_session_start` and `t_first_publish`, so any gap is visible in the artifact rather than assumed absent. Phase 0 fills the number in.

**Three encryption layers, do not confuse them:** transport (always on, but the SFU sees plaintext), end-to-end (SFU handles ciphertext only), and at rest (our storage and keys).

**The end-to-end-encryption / AI tension is the most important security fact in the system.** An agent that transcribes or responds must decrypt the audio, so if end-to-end encryption is on and an agent is present, the agent is inside the trust boundary. You cannot have both "only the humans can hear this" and "the AI processes it". This is made explicit as a session mode, enforced at the control plane:

| Mode | End-to-end encryption | Agents | Recording |
|---|---|---|---|
| `open` | Transport only | Allowed | Server-side allowed |
| `sealed` | Full | **Refused at the API** | Client-side only, or none |
| `trusted_agent` | Full, agent holds a key | Allowed, disclosed | Consumer-held key |

**Envelope encryption.** Each recording gets a unique data key (fast, local); the data key is wrapped by the tenant master key and stored beside the object. This buys per-object isolation, cheap bulk crypto, revocation by disabling the master key, and rotation without re-encrypting media.

**The seal stage.** Egress writes to object storage directly and cannot apply the envelope itself, so sealing is an explicit stage in the processing plane:

```
egress ──► landing prefix        restricted access policy, bucket-level encryption,
           │                     short lifecycle as a backstop only
           ▼
        seal worker              triggered by egress completion, not by lifecycle:
           │                     hash the plaintext media, generate a data key,
           │                     encrypt chunked, write to the evidence prefix with
           │                     the wrapped key and nonce as object metadata,
           │                     delete the landing copy
           ▼
        recording.sealed / recording.completed
```

Three details decide whether this holds up: **trigger on egress completion, not on the lifecycle rule** (the lifecycle is a backstop for a failed seal, so the unsealed window must be seconds, not hours); **the manifest hash is over the plaintext media bytes**, which is what a consumer can independently re-hash; and **encrypt chunked and streaming**, never loading a whole recording into memory, with the chunking scheme recorded in metadata. The consumer re-hashes on receipt: two independent hashes of the same bytes is the entire point.

**The recording manifest** is the boundary object between platform and consumer. Per track it carries the object key, version id, size, plaintext hash, wrapped data key reference, codec, timestamps, and the **opaque** participant id. The consumer holds the mapping from opaque id to a real person; the platform never stores a name, email, or phone number as an identifier.

**Immutability.** Where retention requires that recordings cannot be altered before expiry, use storage-enforced write-once retention, not application checks. The two modes are not equivalent: **compliance** mode is genuinely irreversible (no principal can shorten it) and is chosen only when the term is certain; **governance** mode is bypassable with a single permission and is only defensible if the deployment splits writer and deleter roles and ships an audit trail to a separate locked account. Retention **cannot be shortened** once applied, and a legal hold extends it indefinitely until an audited release.

**Policy engine.** Dafter provides enforceable mechanisms; consumers configure the policy.

| Mechanism | Dafter enforces | Consumer decides |
|---|---|---|
| Consent | Recording and storage cannot proceed without a valid artifact | What valid consent is, wording, which operations need it |
| Retention | Automatic deletion at expiry; legal hold suspends | Class definitions and durations |
| Residency | Providers and storage filtered to allowed regions; violations rejected at config resolution | Which regions |
| Redaction | Detects configured entity types, produces redacted derivatives, original stays sealed | Which types, which artifacts |
| Privacy mode | Agent dispatch and recording refused in `sealed` | When to use it |
| Disclosure | Statements spoken, non-interruptible, logged | Wording, when required |
| Audit | Every access logged immutably | Audit retention |

**Consent is a gate, not a flag**, enforced in the control plane so a consumer that forgets to check cannot proceed. **Audit must be queryable under time pressure**, indexed by actor, artifact, tenant, and time, retained independently of the data it describes, and immutable: deleting a recording must not delete the record that it existed. **Prompt injection** is handled structurally: transcript content and retrieved documents are untrusted input, tool arguments are always schema-validated, effect-class gates hold regardless of model compliance, retrieved content is delimited data rather than instructions, and adversarial probes are a standing regression test. **Identifiers must be opaque** (`s_7f3a9c21`, `p_4b81e0d7`), never names or emails, because they propagate into logs, traces, metric labels, and vendor dashboards; the control plane generates them and rejects consumer-supplied ones.

### Client SDK and extension points

One object, one mental model. The consumer sees Dafter, not a media SDK plus a control plane SDK plus an event stream. Non-negotiables: **agent state surfaced** (the single most important UI affordance in a voice product); **reconnection is the SDK's job** since mobile drops constantly, so it rejoins, restores the transcript from the server, and resumes without the consumer writing a line; **device management and a pre-call check**, because device problems are a large share of support volume; a **degradation path** to audio-only then text-only; and the SDK **never holds platform or provider credentials**. **Token refresh is a consumer hook**, because only the consumer knows whether the user is still permitted to be in the call.

SDK tiers: `client-core`, `client-react`, `client-react-native`, native Swift/Kotlin, `admin`, and **`testkit`**. The testkit ships an **in-bundle fake session behind a flag**, so a consumer's end-to-end suite drives their real screens with no media server and no vendor spend. Shipping that is what separates an SDK from a service with a client library.

**Extension points**, cheapest first, are the mechanism that prevents forking: config, prompt/persona references, tool registration, custom behaviour, custom pipeline node, custom provider, custom agent type, event consumers, and post-processing stage. Consumer-side extensions run in the consumer's process to contain blast radius; latency-critical ones run in-process but are timeout- and resource-sandboxed, so a slow custom node degrades to bypass rather than stalling the session. **A missing extension point is a roadmap item, not a reason to fork.**

### Observability and evals

**One trace per turn, not per session**, spanning STT, turn detection, context build, LLM generation (with nested tool calls), speech planning, TTS, and playout. **Provider request IDs on spans are what make vendor support tickets actionable.** The **turn timeline** uses absolute timestamps, not just durations, because durations say a stage was slow while a timeline says which stage was *waiting*.

Per-turn metrics: platform turn gap, mouth-to-ear, transcription delay, endpointing hold, LLM time-to-first-token, tool duration, TTS time-to-first-byte, playout, and dropped frames. **Conversation-quality metrics** are where most real problems show up and where latency metrics see nothing: agent talk ratio versus the user, interruptions per session, false interruption rate, backchannel misclassification, TTS cancellation rate, mean agent utterance length, and re-ask rate.

**Evals are five layers:** behavioural unit tests on every commit; component evals on held-out audio when a provider or model version changes; scenario regression suites every release; load tests to find worker saturation before real traffic does; and production scoring on sampled live sessions. **The scenario catalog must include fault injection**, because provider failure is a normal operating condition. **The golden dataset is the real asset:** real audio from real users on real networks with real accents and noise, verified, and collected with consent from day one.

### Data management, tenancy, metering, versioning

**Data classes** have different owners, stores, and loss tolerances: **evidential** (sealed recordings, manifests, consent artifacts, certified transcripts, access audit) is unrecoverable if lost and is the class the whole governance design exists for; **operational** (sessions, opaque participants, resolved config, transcript versions, raw provider events, usage) is recoverable from backup; **ephemeral** (live state, presence, locks, queues) is tolerated by design; **telemetry** (traces, metrics, logs, opaque identifiers) is tolerated. **Backpressure**: every queue is bounded, audio drops while text blocks, playout is the clock, drops are metrics rather than silence, and admission control at the edge rejects new sessions with a retryable error rather than queueing them into a timeout.

**Tenancy is enforced at four layers:** API (tenant derives from the API key only, never a request body), data (row-level security plus tenant scoping in every query), storage (per-tenant prefix and key), and hot store (per-tenant key namespace). Per tenant: pooled or bring-your-own credentials, quotas on concurrency and cost, config isolation, optional dedicated workers, residency filtering, and exportable audit. **Metering from day one**: media, TURN relay, STT, TTS (so agent verbosity is directly a cost line), LLM (tokens, cached versus fresh), recording, and storage, each usage record carrying cost attribution.

**Versioning:** HTTP API by path, config by `apiVersion`, events by per-event version, SDKs by semver. Additive by default, deprecate then remove, and **never change behaviour silently** - a default change happens on a major boundary and appears in the migration guide. **Provider model versions are pinned in config**, so a vendor silently upgrading a model cannot silently change a consumer's agent.

---

## 5. Infrastructure

Every concrete choice here sits behind an abstraction, so switching is a config edit, not a rewrite. That is the whole point of the provider layer.

### Deployment posture

**Dafter is deployment- and region-agnostic - it runs wherever the operator chooses.** It has no home region and prescribes none; a deployment targets any region and cloud the underlying stack and LiveKit OSS support. Co-location within a region matters more than which region: client, SFU, agent workers, and model endpoints should sit together wherever the deployment runs. Because every external dependency (media endpoint, TURN, storage, provider endpoints, event bus) is a configured resource, moving or adding a region is a deployment change, not a code change.

### Initial stack

| Layer | Now | Behind |
|---|---|---|
| Media server | LiveKit OSS, self-hosted, any region the stack supports | Thin `MediaTransport` seam - control-side operations only, **not SFU portability** (decision 9) |
| TURN | Managed TURN (Cloudflare), with LiveKit embedded TURN as the fallback | Transport config |
| Indic STT / TTS / LLM / MT | Sarvam | `STTProvider` / `TTSProvider` / `LLMProvider` / `MTProvider` |
| English + fallback | Second provider per stage, picked by benchmark on our audio | Same contracts |
| VAD | Any VAD LiveKit supports (for example Silero) | `VADProvider`, provider-agnostic |
| Compute, hot state, platform data | Operator's cloud: container compute, managed Redis, managed Postgres | Deployment config |
| Recording storage | Object storage with envelope encryption, bucket and key configurable per tenant | Storage config |
| Edge | Cloudflare (DNS, WAF, DDoS, TLS) | - |

#### LiveKit, self-hosted

Self-hosted OSS is the reference, keeping decryption out of a third party's hands (and letting an operator satisfy its own residency needs when it has them). **Where it runs is a deployment choice, not part of the design.** Managed LiveKit Cloud stays reachable from the same artifacts because the transport seam covers control-side operations - but that is portability *between LiveKit deployments*, not portability between media servers. Moving to a different SFU would be a rewrite of the transport package and parts of the runtime, and this document does not pretend otherwise.

**What self-hosting actually requires**, listed so it is costed rather than assumed:

- An autoscaling group of SFU nodes with host networking and either a UDP port range or a single UDP mux port.
- A Redis node bus so rooms work across multiple nodes.
- Signalling behind a TLS load balancer, with media bypassing it entirely.
- A **scale-in drain guard** that holds a terminating node until its rooms empty. Without it, scale-in drops live calls and triggers reconnect storms.
- A separately scaled egress service.
- Prometheus scrape into a metrics backend.
- Certificates and a 443 listener if the embedded TURN fallback is ever active.

#### VAD

VAD is a **first-class, provider-agnostic pipeline stage** (see the architecture document). Any VAD LiveKit supports is usable and configurable, independent of the STT/LLM/TTS providers. A provider's own server-side VAD is one option, not a requirement.

The only provider-specific VAD tuning worth encoding once: **when a provider's server VAD is authoritative, turn the local VAD off.** A second local VAD sees the same audio as the provider's recognizer and fights it. This is a per-provider config detail, not a property of the pipeline.

#### Sarvam

A language specialist, and the reason the config model has a per-language axis at all. General-purpose providers trail specialists on accented, code-mixed, and telephony-band audio.

**What we use:** `saaras:v3-realtime` streaming STT over websocket (true partial transcripts, millisecond VAD tuning, live reconfiguration without reconnect), `bulbul:v3` TTS (30+ voices, code-switching handled at model level so a mixed-language utterance comes out in one pass), `sarvam-105b` LLM, and Sarvam Translate. `saaras` also has a **translate mode** that goes source-language speech to English text in one hop, collapsing STT and MT into a single stage.

**Non-obvious production settings**, all deviating from framework defaults and all expressible in the config model:

| Setting | Value | Why |
|---|---|---|
| STT class | Streaming class, not the legacy one | Legacy has no real partials and no live reconfiguration |
| Local VAD | **Off** | Sarvam's server VAD sees the same audio as the recognizer. A second local VAD fights it |
| Turn strategy | `provider_endpointing` | The framework's semantic turn detector is English-trained; trust Sarvam's own end-of-speech events for Indic |
| Chunk profile | `fast` (500ms) | The 1000ms default adds up to a full second before VAD even begins. **Single biggest latency knob** |
| TTS codec | Raw PCM | Skips a decode pass per chunk. Use mulaw at 8kHz for telephony |
| TTS connection | Prewarmed at worker start | Time-to-first-byte is dominated by TLS handshake, not the model |

**Operational notes worth encoding once in the adapter:** construct providers in worker setup rather than per session, so bad config kills the worker at boot instead of mid-call; websocket close code `1003` (auth/quota) is **not** retryable and should page someone, while `1013` is transient; and the true TTS request ID appears only in the tracing span, not in client metrics, so tracing is mandatory for vendor support to debug a latency complaint.

**Not a hard dependency.** Provider superiority is not universal: the best Indic conversational model is not automatically the best for English legal terminology, noisy telephony, or diarization. Routing is per language and per use case, resolved through the capability matrix.

#### Cloudflare TURN

**Needed, not optional, and mobile networks are the reason.** Carrier-grade NAT on mobile networks frequently prevents a direct peer path, so media must be relayed. Corporate firewalls, restrictive guest wifi, and VPNs add to it. Expect **10-20% of connections** to need TURN, skewing higher on mobile.

**Managed TURN (Cloudflare) is the primary relay.** It terminates at an edge point of presence near the user instead of relaying a distant user's media across the world and back, and operating a relay fleet carries no product differentiation for us.

**What the relay can see, stated rather than implied.** A relay forwards packets and holds no DTLS keys, so it sees ciphertext, never audio or video. It does see connection metadata: client and server addresses, packet timing, and volume. That is the honest version of "no third party in the media path" and the version a security review should get.

**LiveKit's embedded TURN is the configured fallback.** It runs inside the SFU process, so it costs a config block, a certificate, and a 443 listener rather than a separate fleet. The transport layer selects it automatically when managed TURN is not configured for the deployment, which keeps a self-hosted or air-gapped install working with no code change. Because it runs on the SFU nodes, expect worse paths for distant users than the edge network gives.

**TLS on 443 is mandatory on both paths.** Many networks block all UDP and non-standard ports; TURN over TLS/443 is indistinguishable from HTTPS to a firewall and is often the only path that works. A UDP-only deployment silently fails for exactly the users who most need it. Offer UDP, then TCP, then TLS/443. Relay bandwidth is metered per tenant and doubles as a diagnostic: a rising relay share means the consumer's network changed, not our system.

#### coturn (future)

A dedicated self-hosted relay fleet, added only if the embedded fallback proves insufficient at scale or a consumer needs relay capacity managed independently of the SFU. It brings its own scaling, certificates, wide UDP port range, and short-lived HMAC credentials. Adding it is a deployment change, not an application change, because TURN sits behind the transport config.

#### Cloud resources

Start in a single region. Co-location matters more than anything else for latency.

| Component | Service class | Notes |
|---|---|---|
| Control plane | Serverless containers | Stateless, small, many replicas, behind a load balancer |
| Agent workers | Container on VM or Kubernetes | CPU and memory bound, needs predictable capacity and a warm pool |
| Egress workers | Container on VM or Kubernetes | CPU-heavy, bursty, scale to zero |
| System of record | Managed Postgres, multi-AZ, point-in-time recovery | Sessions, transcripts, consent, audit, config, usage |
| Hot state | Managed Redis, multi-AZ | Session state, locks, queues, event streams |
| Media storage | Object storage | Per-tenant prefix, envelope encryption, write-once retention for immutability |
| Keys | Managed key service | Per-tenant master key wrapping per-object data keys |
| Secrets | Managed secrets store | Provider credentials, rotated. Never in config documents |
| Telemetry | Metrics/tracing backend | Per-turn traces |

### Deployment and scaling

**Self-hosted is the reference topology.** Two others stay reachable from the same artifacts, with no topology-specific code paths: **managed** (one operator runs everything, including the media plane) and **hybrid** (a shared control plane with the consumer's own media and storage). Both remain possible because every external dependency is a configured resource rather than an assumption. Neither is built until a consumer needs it.

| Component | Runs on | Scales on |
|---|---|---|
| Control plane | Serverless containers | Request rate |
| SFU fleet | VM autoscaling group, host networking, Redis node bus | Bandwidth and connections, with a **drain guard on scale-in** |
| TURN | Managed edge; embedded on the SFU nodes when it is the fallback | Relayed bandwidth. TLS/443 either way |
| Agent workers | Container on VM or Kubernetes, warm pool | **Pending dispatch depth first**, then CPU and memory |
| Egress and seal workers | Container on VM or Kubernetes, scale to zero | Queue depth. CPU-heavy and bursty |

**Single-metric autoscaling on agent workers is the standard failure mode.** CPU-only misses memory pressure from concurrent pipeline state; connection-count misses dispatch queue buildup. Pending dispatch depth is the leading indicator.

Co-locate SFU, workers, and model endpoints. Route by proximity with a latency-probe fallback; residency pins override proximity. Scale vertically before horizontally.

**Edge caution: do not run two competing media stacks.** Several edge providers now offer their own realtime media products. Adopting one *in addition to* a chosen media server means two connection models, two sets of primitives, and two failure modes for no gain. Use the edge for what it is unambiguously good at (DNS, WAF, DDoS, TLS, TURN, cheap object egress) and keep one media stack. Also verify storage residency claims specifically: a provider operating in a country is not the same as its storage product offering that country as a data-residency jurisdiction.

### Recovery and data durability

Managed Postgres multi-AZ with point-in-time recovery, versioned buckets, a quarterly restore drill that is actually run, and expand-and-contract migrations. The hot store (Redis) is not durable by design and nothing depends on it being so. Usage is derived from the event stream, so any bill is re-derivable rather than asserted. Audit is two correlated trails, platform-side and consumer-side, joined by session id, both immutable, both indexed by actor, artifact, tenant, and time.

### Language per plane

**The planes do not share one answer, and forcing one costs more than the split does.**

| Plane | Language | Why |
|---|---|---|
| Control, state, egress and seal | **Go** | Stateless HTTP at request-rate scale and queue-depth workers. No vendor SDK exists here by design, so nothing is given up. Streaming chunked encryption over large media is natural rather than careful. A small static binary keeps the control plane low-privilege and makes warm-pool cold start fast |
| Agent runtime, providers, batch pass, evals | **Python** | Where the realtime agent framework, the model plugins, and the audio and scoring ecosystem actually live. Rebuilding turn detection, cancellation, tool plumbing, and every provider adapter in a language with no agent framework is the largest avoidable cost in this plan |
| Media plane | **LiveKit OSS (upstream Go)** | Operated, not written |
| Clients | **TypeScript, Swift, Kotlin** | The browser and the devices. No decision to make |

**The two halves touch in exactly two places, and both are generated:** the resolved config document and the event schema. Config resolves once in the control plane and the worker receives a finished, hashed document, so overlay logic is never written twice.

**Provider isolation becomes structural.** The provider layer exists only in Python, because only the agent and batch planes need it. So "the control plane never imports a provider" stops being a linter rule - there is no Go provider to import.

**Agent workers are addressed by pool name, not by language.** A worker registers under an `agent_name` and dispatch carries no language information, so the pool name is a **config field, not a constant**. If one language or tenant ever outgrows the Python pool's density, a second pool serves that slice alone - a routing change, not a rewrite.

### Package layout

```
dafter/
├── schemas/                    # JSON Schema: config, events, API. Versioned.
│                               # Source of truth for codegen in EVERY language
├── api/                        # generated OpenAPI + protobuf. Checked in, never hand-edited
│
├── go/                         # ---- the platform ----
│   ├── cmd/
│   │   ├── dafter-control/     # control plane API server
│   │   ├── dafter-sealer/      # egress orchestration, seal stage, manifests
│   │   └── dafter-ctl/         # operator CLI
│   └── internal/
│       ├── core/               # config schema + resolution, event and error taxonomy, telemetry
│       ├── transport/          # media-transport seam + LiveKit adapter (thin; see decision 9)
│       ├── control/            # sessions, tokens, dispatch, policy, consent, tenancy,
│       │                       #   metering, webhooks
│       ├── state/              # postgres (system of record) + redis (hot state, streams)
│       └── media/              # egress orchestration, plaintext hashing, envelope seal
│
├── python/                     # ---- the agent and ML side ----
│   ├── dafter_core/            # generated types, event client, resolved-config reader, telemetry
│   ├── dafter_providers/       # per-vendor subpackages + registry + contract test suite
│   ├── dafter_runtime/         # agent worker: pipeline, turn detection, behaviors,
│   │                           #   speech planner, tools. Registers as pool "dafter-py"
│   ├── dafter_batch/           # batch transcript, diarization, redaction (needs providers)
│   ├── dafter_evals/           # scenario runner, judges, golden dataset, load generator
│   └── dafter_testkit/         # what CONSUMERS use: fakes, fixtures, local harness
│
├── packages/                   # TypeScript: types (generated), client-core, client-react,
│                               #   client-react-native, admin, testkit
├── clients/                    # swift, kotlin
├── infra/                      # Terraform, own lifecycle: LiveKit SFU autoscaling group +
│                               #   Redis node bus + drain guard, egress fleet, Postgres,
│                               #   object storage, KMS, Cloudflare DNS/WAF/TURN, TLS 443
└── examples/  docs/
```

Infrastructure keeps its own module and Terraform lifecycle: the self-hosted media stack is an operational workstream that releases on a different clock from the platform.

#### Dependency rules, enforced by the toolchain

```
Go side                                  Python side
  core      -> nothing                     dafter_core      -> nothing (generated types only)
  transport -> core                        dafter_providers -> dafter_core
  state     -> core                        dafter_runtime   -> dafter_core, dafter_providers
  control   -> core, state, transport      dafter_batch     -> dafter_core, dafter_providers
  media     -> core, state                 dafter_testkit   -> dafter_core   (NEVER providers)
```

Three rules carry the weight. **The control plane never imports a provider**, so it needs no vendor SDKs or credentials and stays a small, low-privilege service - structural here rather than aspirational. **No vendor import outside its own provider subpackage**, enforced by an import linter with a CI grep as backstop. **The seal stage needs no provider at all**, which is why egress and sealing sit on the Go side and the batch transcript sits on the Python side.

**Schema-first codegen**: one definition of an event, ever, from which Go types, Python types, TypeScript types, native models, and docs are generated.

### Engineering standards

- **Architecture enforced by the toolchain, not in review comments.** `internal/` visibility and a forbidden-import linter on the Go side, an import linter on the Python side, a grep for vendor imports outside their subpackage, and a schema compatibility check across all three generated targets. One short ADR per decision.
- **Schemas first.** Types and docs are generated, the API description is published every release, changes are additive by default, deprecate then remove, and provider model versions are pinned in config.
- **Tests by layer, and no merge without a test at the layer being changed.** Unit for core; the contract suite per adapter with real providers run nightly; integration against real LiveKit, Postgres, and Redis rather than datastore mocks; scenario and fault tests for the runtime; browser and device end-to-end for the SDKs; a load test and restore drill for deployment.
- **Observability from the first commit.** A feature is done when its dashboard panel and its alert exist, not when the code merges.
- **Security lives in code paths, not in a document.** Secrets as references, effect-class gates, HMAC webhooks, opaque identifiers, least-privilege access with writer and deleter split, and prompt-injection regression tests.
- **Trunk-based** with PR gates for lint, types, unit, contract, integration, and schema. Staging is the consumer's integration target from Phase 1.
- **Docs are deliverables**: a quickstart a consumer can follow with no help, a runbook per plane, and an incident playbook that contains the actual audit queries.

---

## 6. Delivery plan

### How to read this plan

Phases are ordered by **dependency**, not by calendar. Each phase is independently shippable and each has an explicit **deliverable** that is its milestone: the phase is done when a consumer or an operator can do the stated thing, not when the code merges.

**Duration is deliberately not estimated.** Sequence is a design decision; duration is not something to guess at this stage. Phases are not releases: a consumer integrates against staging from Phase 1 onward, so the interface is exercised by someone other than us long before any of it is finished.

### Critical path

```
Phase 0  ─►  Phase 1  ─►  Phase 2  ─────────►  Phase 4
                 │            │
                 │            └─ Phase 3 runs alongside Phase 2
                 └─ Phase 1b runs alongside Phase 2, off the critical path

Phase 5 (agent runtime) is off the critical path.
Phase 6 hardens for scale. Phase 7 is optional, behind config flags.
```

A recorded, consented, evidence-chained call is what a consumer can launch on. The agent is value added on top of it, which is why Phase 5 is deliberately not on the critical path.

---

### Phase 0 - Validate the risky assumptions

Prove the hard parts before designing around them.

**Milestones:**
- A bare multi-participant session on a self-hosted node, web client, audio and video.
- The real latency distribution measured on the target networks, in the target languages and accents, decomposed by stage. This number determines whether everything else is achievable.
- The TURN relay share measured on real mobile connections, over managed TURN and over the embedded fallback, on TLS/443. It sizes relay cost and the connection-failure support load.
- The seal stage proven end to end: egress to landing, seal, evidence prefix, and an independent re-hash on the other side that matches.
- Egress start latency measured for both layouts, resolving when recording actually starts. Per-track cannot begin before its participant publishes, so a no-gap guarantee needs room-level egress at session creation; measure what each costs.
- One agent worker on a full cascaded pipeline, enough to validate the seams rather than to ship an agent.
- Provider live-reconfiguration tested for mid-session language switching.
- Per-track audio egress into a test bucket, with cost per call-hour per layout measured.
- End-to-end encryption tested with an agent present; the trust boundary confirmed to behave as documented.
- Long-session behaviour tested against provider stream duration limits.
- The React Native SDK verified on the consumer's actual Expo and React Native versions, in a development build.

**Deliverable:** a measured baseline and a written record of which vendor claims held. Vendor latency numbers are marketing until measured on our path.

**Exit criterion (define before starting):** what measured p50 and p95, on what network, in what language, justifies building the rest.

---

### Phase 1 - Foundations

Core, schemas, providers, transport, state, and control.

**Scope:** the core package (config schema and resolution, event and error taxonomies, telemetry conventions); schemas with codegen to Go, Python, and TypeScript; the providers package with **one provider per stage** plus **the full contract test suite**; the transport package behind the media-transport seam; the state package (hot store, event streams, relational schema and migrations); the control package (session create, token minting, agent dispatch, event delivery, tenancy enforcement); and CI enforcing dependency boundaries, contract tests, config validation, and schema compatibility.

**One provider per stage, deliberately.** Fourteen adapters before anything ships validates the abstraction by volume rather than by design. The contract suite is what makes the seam real, so it ships here in full; the second provider goes to Phase 1b, where adding it *is* the test.

**The `channel` overlay axis is designed against telephony here**, though SIP ships in Phase 7. Either the axis accounts for telephony now, or telephony comes out of the validating shapes in section 1 - not both.

**Deliverable:** create a session by API, join with a token, get an agent dispatched, and receive typed events.

---

### Phase 1b - Prove the provider seam

**Scope:** the second provider per stage, added by following the fixed checklist and nothing else.

**Deliverable:** a second provider per stage is live and switching between them is a config edit. **If adding one required touching the runtime, control plane, or config schema, the abstraction has a hole and this phase does not exit until it is fixed.**

---

### Phase 2 - Recording and governance

**Scope:** recording orchestration with selectable layouts; the seal stage with per-recording envelope encryption, plaintext hashing, and the manifest; consent capture and the enforced gate; retention classes, write-once immutability, legal hold, and coupled deletion of data and wrapped key; residency enforcement in provider and storage selection; privacy modes enforced at the control plane; and queryable audit logging with export and deletion endpoints.

**Deliverable:** a consumer re-hashes a sealed recording and the hashes match; a held recording refuses deletion; and the audit record survives the deletion of what it describes.

---

### Phase 3 - Consumer surface

Runs alongside Phase 2, because a consumer cannot integrate against an API they cannot call from their own screens.

**Scope:** client SDKs (core, React, React Native, then native); the token-refresh hook and the connection state machine; reconnection, device management, pre-call check, and the degradation ladder; the admin SDK; the testkit with the in-bundle fake session; capability introspection; and documentation, quickstarts, and example applications of different shapes.

**Deliverable:** a consumer's call screens pass their own browser and device end-to-end suites against the fake session, and one real-media smoke test passes.

---

### Phase 4 - Transcription of record

**Scope:** batch transcription on demand, per-track attribution, verbatim and clean modes; transcript versions with full provenance; the terminology service with per-provider compilation; and export with hash.

**Deliverable:** a defensible canonical transcript from every recorded session, and a regenerated transcript is a new version rather than an overwrite.

---

### Phase 5 - Agent runtime

Off the critical path.

**Scope:** worker bootstrap with the setup/entrypoint split and prewarming; pipeline assembly from config, fully streaming and fully cancellable; turn detection strategies with per-language `auto` resolution; the behaviour system; the speech planner (normalization plus chunking) for the first languages; the tool registry with latency and effect classes; per-turn tracing, metrics, and budget enforcement; behavioural test helpers and the first regression suite; and the tuning runbook.

**Deliverable:** a configurable conversational agent meeting the declared latency budget in at least two languages and two channels, with the fault cases degrading as designed.

---

### Phase 6 - Scale and resilience

**Scope:** provider fallback, circuit breaking, and the partial-output guard; backpressure and admission control throughout; multi-tenancy completion (quotas, bring-your-own credentials, dedicated workers, fair queuing); metering and cost controls; load testing and autoscaling tuning including the SFU drain guard under real scale-in; and the evals package with the golden dataset, fault injection, and production scoring.

---

### Phase 7 - Optional capabilities

Behind config flags, built when a consumer needs them: live captions and spoken translation, telephony inbound and outbound, vision input, avatars, half-cascade and speech-to-speech modes, a dedicated self-hosted relay fleet, managed and hybrid topologies, and a second region.
