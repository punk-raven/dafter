# Dafter agent rules

Hard rules. Each one applies to every message, task and subagent, without exception. Project rules
win over global rules on conflict; otherwise both apply. Only an explicit user bypass for the
current message suspends a rule (rule 5). A rule that cannot be met: stop and say so, never work
around it.

1. **Tool only.** Do only the task given. Never add suggestions, follow-ups, interpretations,
   advice, or assumptions. Never start tests, dev servers, migrations, or anything else not
   explicitly instructed.
2. **User-level rules.** Every rule in the user's global config (`~/.claude/CLAUDE.md`,
   `~/.claude/RULES.md`, `~/.claude/TOOLING.md`) must be followed without exception.
3. **Project files stay here.** All project-related memory, temp files and scratchpad must live
   inside this folder and must be gitignored.
4. **Tooling routing.** Plans must go through `lavish-axi`, tasks through `tasks-axi`, GitHub
   through `gh-axi`. Every task must be subagent-driven on a new terminal, never on the main thread.
5. **Explicit bypass is single-message only.** A bypass applies only to the message that grants it.
   On the next message every rule applies again; a bypass never carries over.
6. **No comments in code.** Names and structure must carry the context. Tool directives (`//go:`,
   `//nolint`, `# noqa`, `# type:`, shebangs, generated-code headers) are the only exception.
   (Enforced: `make rules-check`, PostToolUse hook.)
7. **500 lines per file, hard cap.** A file that would pass 500 lines must be split before it does.
   (Enforced: `make rules-check`, PreToolUse hook.)
8. **Names carry intent.** Names must follow each language's convention (Go MixedCaps and
   initialisms, Python PEP 8, JS camelCase) and state intent, so code reads without comments.
   (Enforced: ruff `N`, golangci-lint `revive` and `errname`.)
