from __future__ import annotations

import logging

from dafter_core.config import ResolvedSessionConfig
from dafter_core.enums import EventType
from dafter_core.errors import DafterError
from dafter_core.events import EventEnvelope, parse_event

from .judging import Scorer
from .notes import AgentNote
from .writer import Writer

log = logging.getLogger("dafter.scribe.inbox")


class Scribe:
    def __init__(
        self, cfg: ResolvedSessionConfig, writer: Writer, scorer: Scorer | None = None
    ) -> None:
        self._cfg = cfg
        self.writer = writer
        self.scorer = scorer

    def event(self, data: bytes) -> EventEnvelope | None:
        try:
            event = parse_event(data)
        except DafterError:
            log.info("a malformed event on the events topic was ignored")
            return None
        return event if event.session_id == self._cfg.session_id else None

    def received(self, data: bytes, from_worker: bool) -> None:
        if not from_worker:
            return
        event = self.event(data)
        if event is None:
            return
        if event.type is EventType.TRANSCRIPT_FINAL:
            line = self.writer.transcript.heard(event.payload)
            if line is not None and self.scorer is not None:
                self.scorer.heard(line)
        elif event.type is EventType.AGENT_NOTE_TAKEN:
            p = event.payload
            self.writer.note(AgentNote(p["noteId"], p["text"], p.get("takenBy")))


__all__ = ["Scribe"]
