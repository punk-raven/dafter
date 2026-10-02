# Addressing vectors

`utterances.json` pins what the name matcher decides for one transcribed
utterance, under the agent name and addressing block the embedded catalog resolves by
default (`agent.name` Nivya, its Devanagari, Kannada and Telugu spellings in
`addressing.aliases`, and the near misses), as carried by `testdata/agent/hindi-webrtc-job.json`.

Each case is a language, the text a recognizer might return, what the matcher
must hear and why:

- `called`: the agent was addressed by name (at or near the start, or set off
  as a vocative), so it wakes and answers the speaker
- `stopped`: addressed by name and told to stop, so it goes quiet at once
- `stop`: a stop phrase on its own, which sends an awake agent to sleep
- `aside`: anything else, including passing mentions ("I told Nivya
  yesterday"), the name as a subject, a case-suffixed name, and near misses
  (Navya, Divya, the Marathi नव्या "new", the Kannada ನೀವ್ಯಾರು "who are you")

Every one of en, hi, mr, kn and te has cases of each kind. The text is written,
not recorded or generated audio: the matcher sees only transcripts, so a text
fixture tests all of it.

`python/dafter_runtime/tests/test_naming.py` reads it through
`python/dafter_runtime/src/dafter_runtime/naming.py`. A catalog change to the
name, aliases or near misses regenerates the job vector; rerun this test with
it.
