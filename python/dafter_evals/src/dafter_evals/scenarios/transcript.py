from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CALLER_ROLE = "caller"
AGENT_ROLE = "agent"
LANGUAGE_NAMES = {
    "en-IN": "Indian English",
    "hi": "Hindi in Devanagari script",
    "te-IN": "Telugu in Telugu script",
    "kn-IN": "Kannada in Kannada script",
    "mr-IN": "Marathi in Devanagari script",
}


@dataclass(frozen=True, slots=True)
class Line:
    role: str
    speaker: str
    text: str

    def shown(self) -> str:
        said = self.text or "(says nothing)"
        return f"{self.speaker}: {said}"


@dataclass(frozen=True, slots=True)
class Call:
    tool: str
    arguments: dict[str, Any]
    output: str | None
    error: bool


@dataclass
class Tokens:
    input: int = 0
    output: int = 0

    def add(self, input_tokens: int, output_tokens: int) -> None:
        self.input += input_tokens
        self.output += output_tokens


@dataclass
class Transcript:
    lines: list[Line] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)

    def agent_text(self) -> str:
        return "\n".join(line.text for line in self.lines if line.role == AGENT_ROLE)

    def shown(self) -> str:
        return "\n".join(line.shown() for line in self.lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "lines": [{"role": x.role, "speaker": x.speaker, "text": x.text} for x in self.lines],
            "calls": [
                {"tool": c.tool, "arguments": c.arguments, "output": c.output, "error": c.error}
                for c in self.calls
            ],
        }
