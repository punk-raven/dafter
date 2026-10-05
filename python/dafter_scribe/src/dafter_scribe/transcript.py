from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from dafter_core.enums import SpeakerKind

log = logging.getLogger("dafter.scribe.transcript")

MAX_PENDING_CHARS = 24000


@dataclass(frozen=True, slots=True)
class Line:
    segment: str
    speaker: dict[str, str]
    label: str
    text: str

    @property
    def from_agent(self) -> bool:
        return self.speaker.get("kind") == str(SpeakerKind.AGENT)

    def prompt(self) -> str:
        return f"[{self.label}] {self.text}"


class Transcript:
    def __init__(
        self,
        label_of: Callable[[str], str],
        agent_label: str,
        max_chars: int = MAX_PENDING_CHARS,
    ) -> None:
        self._label_of = label_of
        self._agent_label = agent_label
        self._max_chars = max_chars
        self._pending: list[Line] = []
        self._speakers: dict[str, dict[str, str]] = {}
        self._participants: dict[str, str] = {}
        self.dropped = 0

    def _label(self, speaker: Mapping[str, str]) -> str:
        participant = speaker.get("participantId")
        if participant is None:
            return self._agent_label
        if participant in self._participants:
            return self._participants[participant]
        wanted = self._label_of(participant)
        label, n = wanted, 1
        while label in self._speakers or label == self._agent_label:
            n += 1
            label = f"{wanted} ({n})"
        self._participants[participant] = label
        return label

    def heard(self, caption: Mapping[str, Any]) -> Line | None:
        speaker = caption.get("speaker")
        text = str(caption.get("text", "")).strip()
        if not isinstance(speaker, dict) or not text:
            return None
        who = {k: str(v) for k, v in speaker.items()}
        label = self._label(who)
        self._speakers[label] = who
        line = Line(str(caption.get("segmentId", "")), who, label, text)
        self._pending.append(line)
        self._bound()
        return line

    def _bound(self) -> None:
        while sum(len(line.text) for line in self._pending) > self._max_chars:
            self._pending.pop(0)
            self.dropped += 1
            log.warning("scribe fell behind, oldest unwritten line dropped")

    @property
    def pending(self) -> tuple[Line, ...]:
        return tuple(self._pending)

    def covered(self, lines: tuple[Line, ...]) -> None:
        taken = {id(line) for line in lines}
        self._pending = [line for line in self._pending if id(line) not in taken]

    @property
    def speakers(self) -> dict[str, dict[str, str]]:
        return dict(self._speakers)


__all__ = ["MAX_PENDING_CHARS", "Line", "Transcript"]
