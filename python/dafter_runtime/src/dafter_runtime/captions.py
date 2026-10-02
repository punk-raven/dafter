from __future__ import annotations

import logging
import re
import secrets
from collections.abc import Callable
from typing import Any

from dafter_core.config import ProviderRef
from dafter_core.enums import EventType, SpeakerKind
from livekit.agents import AgentSession
from livekit.agents.voice.events import UserInputTranscribedEvent
from livekit.agents.voice.io import TextOutput

log = logging.getLogger("dafter.runtime.captions")

PARTICIPANT = re.compile(r"^p_[0-9a-f]{8}$")
LANGUAGE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
MAX_TEXT = 4000

Emit = Callable[[EventType, dict[str, Any]], bool]


def segment_id() -> str:
    return "sg_" + secrets.token_hex(8)


def source_of(ref: ProviderRef | None) -> dict[str, str] | None:
    if ref is None or not ref.model:
        return None
    return {"provider": ref.provider, "model": ref.model}


def human(participant: str) -> dict[str, str]:
    return {"kind": str(SpeakerKind.HUMAN), "participantId": participant}


AGENT: dict[str, str] = {"kind": str(SpeakerKind.AGENT)}


def segment(
    sid: str,
    speaker: dict[str, str],
    text: str,
    language: str | None = None,
    source: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"segmentId": sid, "speaker": speaker, "text": text[:MAX_TEXT]}
    if language and LANGUAGE.fullmatch(language):
        payload["language"] = language
    if source is not None:
        payload["source"] = source
    return payload


class AgentCaptions(TextOutput):
    def __init__(self, emit: Emit) -> None:
        super().__init__(label="DafterCaptions", next_in_chain=None)
        self._emit = emit
        self._segment: str | None = None
        self._text = ""

    async def capture_text(self, text: str) -> None:
        if self._segment is None:
            self._segment = segment_id()
            self._text = ""
        self._text += text
        if self._text.strip():
            self._emit(
                EventType.TRANSCRIPT_PARTIAL, segment(self._segment, AGENT, self._text.strip())
            )

    def flush(self) -> None:
        if self._segment is not None and self._text.strip():
            self._emit(
                EventType.TRANSCRIPT_FINAL, segment(self._segment, AGENT, self._text.strip())
            )
        self._segment = None
        self._text = ""


class Captions:
    def __init__(self, emit: Emit, source: dict[str, str] | None) -> None:
        self._emit = emit
        self._source = source
        self._open: dict[str, str] = {}
        self.agent = AgentCaptions(emit)

    def follow(self, participant: str, session: AgentSession[Any]) -> None:
        if not PARTICIPANT.fullmatch(participant):
            log.info("participant not captioned: not an identity the control plane minted")
            return

        def transcribed(ev: UserInputTranscribedEvent) -> None:
            self.heard(participant, ev.transcript, ev.is_final, ev.language)

        session.on("user_input_transcribed", transcribed)

    def heard(self, participant: str, text: str, final: bool, language: str | None) -> None:
        text = text.strip()
        if not text:
            return
        sid = self._open.get(participant) or segment_id()
        self._open[participant] = sid
        payload = segment(sid, human(participant), text, language, self._source)
        self._emit(EventType.TRANSCRIPT_FINAL if final else EventType.TRANSCRIPT_PARTIAL, payload)
        if final:
            del self._open[participant]

    def left(self, participant: str) -> None:
        self._open.pop(participant, None)


__all__ = ["AgentCaptions", "Captions", "segment", "segment_id", "source_of"]
