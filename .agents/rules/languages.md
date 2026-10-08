---
paths:
  - "python/dafter_runtime/src/dafter_runtime/{personas,switching,everyday,spoken_hindi}.py"
  - "python/dafter_providers/src/dafter_providers/multilingual.py"
  - "python/dafter_providers/src/dafter_providers/sarvam/languages.py"
  - "python/dafter_runtime/tests/test_{languages,switching,switching_session,personas}.py"
  - "go/internal/config/switching*.go"
  - "python/dafter_core/src/dafter_core/switching.py"
  - "python/dafter_core/tests/test_switching.py"
  - "schemas/config/v1/language-switching.schema.json"
  - "go/internal/control/languages_test.go"
---

# Rules enforced in code: languages

Identical on both halves (Go and Python) unless stated. Index:
`.agents/skills/project-context/SKILL.md`.

## Languages (stage 5)

- Each focus language routes its own pipeline, voice and turn constants in the catalog (config
  resolution: `.agents/rules/config.md`) and has personas in `personas.py`:
  - Register: casual code-mixed speech (Hinglish, Kanglish, Tenglish, Marathi mixed with English,
    Indian English).
  - Indic words in native script; everyday English words (`EVERYDAY_ENGLISH`) in Latin script, as
    bulbul:v3 and codemix STT expect.
  - Polite address; one or two short sentences; states it is an AI only when asked; never invents a
    lookup or action.
  - Numbers: Hindi writes digits (for `spoken_hindi.py`); Kannada and Telugu say prices, times and
    phone numbers in English words; Marathi says amounts in Marathi and phone numbers in English;
    English uses words.
  - The kn, mr and te text needs a native speaker's review.
- Live switching: `agent.languageSwitching` (catalog defaults list the five, off; a session enables
  it).
  - The STT identifies the language (`Vendor.detects_language`, STT factory language `None`, Sarvam
    `language_code=auto`).
  - `switching.py` switches on a final whose base language is listed, long enough (`minWords`) and
    confident enough (`minConfidence`); observed in the answering agent's `stt_node` (always) or per
    listener and applied when the voice answers that speaker (transcript).
  - The `switch_language` tool pins a requested language.
  - Followers: TTS `Multilingual.speak_in` (voice stays the session's), the speech planner's rules,
    fillers of the new language (synthesized on first use), and `llm_node` swaps in that language's
    persona per reply.
  - Turn constants and the turn detector stay the session's (the framework skips the detector per
    utterance for a language it does not cover).
  - `agent.turn_metrics.language` is the language decided at the turn a reply answers.
