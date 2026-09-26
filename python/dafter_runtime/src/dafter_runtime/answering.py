from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from dafter_core.enums import WakeSource
from livekit.agents import AgentSession
from livekit.agents.voice.speech_handle import SpeechHandle

from .addressing import Said

log = logging.getLogger("dafter.runtime.answering")


class Roster:
    def __init__(self) -> None:
        self._labels: dict[str, str] = {}
        self._present: list[str] = []
        self._joined = 0

    def join(self, identity: str, name: str = "") -> None:
        if identity not in self._labels:
            self._joined += 1
            self._labels[identity] = name.strip() or f"Speaker {self._joined}"
        if identity not in self._present:
            self._present.append(identity)

    def leave(self, identity: str) -> None:
        if identity in self._present:
            self._present.remove(identity)

    def label(self, identity: str) -> str:
        return self._labels.get(identity, "Someone")

    def present(self) -> list[str]:
        return [self.label(i) for i in self._present]


def turn_text(roster: Roster, speaker: str, text: str, overheard: list[Said]) -> str:
    lines = [f"[{roster.label(s.speaker)}, not to you] {s.text}" for s in overheard]
    lines.append(f"[{roster.label(speaker)}, to you] {text}")
    return "\n".join(lines)


class Voice:
    def __init__(self, session: AgentSession[Any], roster: Roster, interruptible: bool) -> None:
        self._session = session
        self._roster = roster
        self._interruptible = interruptible
        self.reply: SpeechHandle | None = None
        self.before_answer: Callable[[str, str], None] | None = None
        self.announce: Callable[[str | None, WakeSource | None], None] | None = None

    def answer(self, speaker: str, text: str, overheard: list[Said]) -> None:
        if self.before_answer is not None:
            self.before_answer(speaker, text)
        if self._interruptible:
            self._session.interrupt()
        self.reply = self._session.generate_reply(
            user_input=turn_text(self._roster, speaker, text, overheard)
        )

    def hush(self) -> None:
        self._session.interrupt()

    def barge_in(self) -> None:
        speech = self._session.current_speech
        if not self._interruptible or speech is None:
            return
        if speech.interrupted or not speech.allow_interruptions:
            return
        log.info("the caller talked over the reply")
        speech.interrupt(source="audio_activity")

    def addressed(self, woken_by: str | None, via: WakeSource | None) -> None:
        log.info(
            "agent addressed",
            extra={"awake": woken_by is not None, "by": woken_by, "via": via and str(via)},
        )
        if self.announce is not None:
            self.announce(woken_by, via)


__all__ = ["Roster", "Voice", "turn_text"]
