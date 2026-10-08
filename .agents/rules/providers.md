---
paths:
  - "go/internal/config/llm*.go"
  - "go/internal/ids/*.go"
  - "schemas/common/v1/ids.schema.json"
  - "python/dafter_providers/src/dafter_providers/{credentials,registry,options}.py"
  - "python/dafter_providers/src/dafter_providers/openai_compat/**"
  - "python/dafter_providers/tests/test_{credentials,openai_compat,llm_placement}.py"
  - "python/dafter_runtime/src/dafter_runtime/labels.py"
  - "python/dafter_runtime/tests/test_{labels,llm_routes}.py"
  - "go/cmd/dafter-control/agent-llm.js"
  - "go/cmd/dafter-control/jstest/llm.test.mjs"
  - "go/cmd/dafter-control/catalog.json"
  - "python/dafter_evals/src/dafter_evals/screen/candidates.json"
---

# Rules enforced in code: LLM routes and credentials

Identical on both halves (Go and Python) unless stated. Index:
`.agents/skills/project-context/SKILL.md`.

## LLM routes

- A session picks its LLM only from the catalog's `llms` table (key `provider/model`, a full
  provider ref). `POST /sessions` names one in `llm`; resolution lands it whole over
  `agent.pipeline.llm` after the overlays and stamps `llm` in the document
  (`go/internal/config/llm.go`). An override may not name or tune it.
- `groq`, `openrouter`, `google` (AI Studio, key `GEMINI_API_KEY`), `opencode_zen` and `openai` are
  vendors built from `dafter_providers/openai_compat/endpoints.json` (one https host and one key
  each), reporting usage under their own name.
- Each route sets the least thinking its endpoint allows:
  - Groq qwen: `reasoningEffort` `none`; gpt-oss: `low`.
  - OpenRouter: `extraBody.reasoning.enabled` false.
  - Gemma 4 on AI Studio: `thinking_level` `minimal` under the literal body key `extra_body` (it
    refuses a thinking budget or effort).
  - lfm-2.5 cannot turn reasoning off: `effort` `low` with a larger `maxTokens`.
- A new model is one line in the catalog's `llms` and one in `LLMS` in the test client's
  `agent-llm.js`, pinned to the catalog by `jstest/llm.test.mjs`. The page shows the LLM the agent
  reports, red when it differs from the config.
- Each compat vendor holds one HTTP/2 connection per job (`openai_compat/wire.py`; over HTTP/1.1 the
  framework abandons the stream body and every turn reconnects).
- Each request is logged as `llm request` (settings, status, HTTP version, `headers_ms`, rate-limit
  headroom, never message text).
- Models copy the `[Speaker 1, to you]` turn labels; `labels.py` strips a label at any line start in
  `llm_node`.

## Credentials and identifiers

- Identifiers are opaque patterns (`schemas/common/v1/ids.schema.json`, minted in
  `go/internal/ids`); credentials are `secret://` refs; region tokens are opaque. Real credentials
  reach the process only through the environment.
- The worker resolves `secret://.../<vendor>/<name>` to the env var `<VENDOR>_<NAME>`, only for a
  provider key on `PROVIDER_CREDENTIALS` that the stage's vendor binds (`credentials.resolve`;
  Sarvam binds `SARVAM_API_KEY`).
  - A ref naming anything else (e.g. a `DAFTER_*`, LiveKit, worker or storage secret) is refused by
    pointer before the worker joins, whatever its path says.
  - The tenant segment of a ref is not read.
- An OpenAI-compatible LLM never takes a URL from the document. Each entry of the operator's table
  `python/dafter_providers/src/dafter_providers/openai_compat/endpoints.json` is its own vendor,
  named by its key, bound to one https base URL and exactly one provider key; a `credentialRef`
  other than that endpoint's key is refused.
- The eval catalog (`dafter_evals/screen/candidates.json`) names the same vendors and holds no URL
  or key of its own.
