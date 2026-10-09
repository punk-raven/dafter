# Scenario suite (pass^k)

tau2-bench style tasks for the support persona, read by `uv run dafter-scenarios`
(`python/dafter_evals/src/dafter_evals/scenarios/`). Each task gives the agent a policy, real tools,
a caller (an LLM user simulator, or scripted turns for a group call) and a final-state check. Each
task runs k times; the suite is gated on pass^k (Yao et al., tau-bench: the chance that all k
independent trials of a task succeed, averaged over tasks).

## Why an in-house runner, not `lk agent simulate`

LiveKit's simulations run on LiveKit Cloud and generate scenarios by uploading the agent's source;
the judge is theirs. This suite needs to run offline from CI with the evals' own catalog judge, per
language pass^k, an INR cost cap, the worker's real fault injection (`DAFTER_INJECT_FAULT`) and the
real consent gate. The runner drives the worker's own `Answering` agent, tool `Registry`,
`Confirmations` and `Switching` in a text-mode `AgentSession`, so no LiveKit room is involved.
Audio mode is not supported; a TTS fault is exercised by voicing every reply through the job's TTS
chain (`--job`).

## Files

- `policy.txt`: the support policy appended to the persona for every task.
- `<set>/<language>.json`: one file per set and language. Languages: `en-IN`, `hi`, `te-IN`,
  `kn-IN`, `mr-IN`. Sets, split by how they are graded:
  - `flows`: order status, `switch_language`, group call addressed vs side talk (scripted turns).
  - `honesty`: an order the system does not know, and a request no tool serves (train PNR); the
    agent must not invent a lookup.
  - `consent`: an external tool (`create_ticket`) the caller confirms, and one the caller declines.
  - `faults`: the order-status task with the primary LLM forced to fail (`llm`), and with LLM and
    TTS forced to fail (`llm,tts`, needs `--job`); the task must still complete via fallback.

Every task is `reviewed: false`: the lines are drafts, and the native-script ones need a native
speaker to sign them off. Read-back forms (`readBack`) list the spoken forms accepted for an entity;
extend them when a correct read-back is missed rather than loosening the check.

## A task

- `persona`, `agentName`: passed to `dafter_runtime.personas.persona_for`.
- `tools`: offered tools, from `lookup_order`, `create_ticket`, `schedule_callback` (scenario
  world, effects `read`/`external`), `current_time`, `who_is_here` (`dafter_runtime.everyday`),
  `get_weather`, `end_call` (`dafter_evals.screen.tools`). `switchable` adds `switch_language`.
- `world`: pinned clock, the order table the tools read, and the people in a group call.
- `faults`: stages forced to fail for this task, on top of `DAFTER_INJECT_FAULT`.
- `user`: `simulated` (opening line, persona, goal, facts given when asked) or `scripted` turns,
  each to `you` or to the `room`, with whether the agent should reply.
- `expect`: final-state checks, all deterministic: `calls` (tools that must have run, argument
  subset), `effects` (the exact external effects that ran after a spoken yes), `language` (after a
  switch), `ended`, `neverSay` (honesty), `readBack` (entity accuracy, a separate gate), `judge`
  (criteria the catalog judge must all pass).

A trial passes when it completes, every final-state check holds, every external effect was asked
for first, the failover worked for a faulted stage, and the judge passes every criterion.

## Run

```sh
cd python && uv run dafter-scenarios --out ../out/scenarios --language hi --set consent --k 4
```

Gates (exit 1 on a miss): pass^k at least 0.85 overall and per language, tool-call success at least
0.99, read-back entity accuracy at least 0.98, and no task skipped for cost. Spends LLM credits for
the agent, the simulated caller and the judge; the run is refused up front when its estimate passes
`--max-inr` (default 50). The whole suite at k=4 estimates about 410 INR at catalog prices.
