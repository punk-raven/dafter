from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from dafter_core.enums import WakeSource
from livekit.agents import AgentSession
from livekit.agents.llm import ChatMessage
from livekit.agents.voice.speech_handle import SpeechHandle

from .addressing import Said, Timing
from .timing import heard

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

    def answer(
        self, speaker: str, text: str, overheard: list[Said], timing: Timing | None = None
    ) -> None:
        if self.before_answer is not None:
            self.before_answer(speaker, text)
        if self._interruptible:
            self._session.interrupt()
        said = ChatMessage(
            role="user",
            content=[turn_text(self._roster, speaker, text, overheard)],
            metrics=heard(timing),
        )
        self.reply = self._session.generate_reply(user_input=said)

    def hush(self) -> None:
        self._session.interrupt(force=True)

    def barge_in(self) -> None:
        speech = self._interruptible_speech()
        if speech is None:
            return
        log.info("the caller talked over the reply")
        speech.interrupt(source="audio_activity")
        speech.add_done_callback(lambda _: self.resume())

    def pause(self) -> bool:
        output = self._session.output.audio
        if self._interruptible_speech() is None or output is None or not output.can_pause:
            return False
        output.pause()
        return True

    def resume(self) -> None:
        if (output := self._session.output.audio) is not None:
            output.resume()

    def _interruptible_speech(self) -> SpeechHandle | None:
        speech = self._session.current_speech
        if not self._interruptible or speech is None:
            return None
        if speech.interrupted or not speech.allow_interruptions:
            return None
        return speech

    def addressed(self, woken_by: str | None, via: WakeSource | None) -> None:
        log.info(
            "agent addressed",
            extra={"awake": woken_by is not None, "by": woken_by, "via": via and str(via)},
        )
        if self.announce is not None:
            self.announce(woken_by, via)


__all__ = ["Roster", "Voice", "turn_text"]
