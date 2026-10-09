from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dafter_core.config import ResolvedSessionConfig
from dafter_runtime.personas import base_language

REVIEW_DIR_ENV = "DAFTER_SCRIBE_REVIEW_DIR"
QUEUE_VERSION = 1
QUEUE_SUFFIX = ".jsonl"
FAIL = "fail"

log = logging.getLogger("dafter.scribe.review")


@dataclass(frozen=True, slots=True)
class AudioReference:
    consent_id: str
    participant_ids: tuple[str, ...]
    segment_ids: tuple[str, ...]
    heard_from: str
    heard_to: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "consentId": self.consent_id,
            "participantIds": list(self.participant_ids),
            "segmentIds": list(self.segment_ids),
            "heardFrom": self.heard_from,
            "heardTo": self.heard_to,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> AudioReference:
        return cls(
            consent_id=d["consentId"],
            participant_ids=tuple(d["participantIds"]),
            segment_ids=tuple(d["segmentIds"]),
            heard_from=d["heardFrom"],
            heard_to=d["heardTo"],
        )


@dataclass(frozen=True, slots=True)
class FailedTurn:
    session_id: str
    language: str
    channel: str
    segment_id: str
    question: str
    reply: str
    criteria: dict[str, str]
    score: float
    reasoning: str
    judged_at: str
    config_version: dict[str, str] | None = None
    audio: AudioReference | None = None

    @property
    def failed(self) -> tuple[str, ...]:
        return tuple(sorted(k for k, v in self.criteria.items() if v == FAIL))

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "version": QUEUE_VERSION,
            "sessionId": self.session_id,
            "language": self.language,
            "channel": self.channel,
            "segmentId": self.segment_id,
            "question": self.question,
            "reply": self.reply,
            "criteria": dict(self.criteria),
            "score": self.score,
            "reasoning": self.reasoning,
            "judgedAt": self.judged_at,
        }
        if self.config_version is not None:
            d["configVersion"] = dict(self.config_version)
        if self.audio is not None:
            d["audio"] = self.audio.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> FailedTurn:
        if d.get("version") != QUEUE_VERSION:
            raise ValueError(f"a review entry of version {d.get('version')!r}")
        audio = d.get("audio")
        return cls(
            session_id=d["sessionId"],
            language=d["language"],
            channel=d["channel"],
            segment_id=d["segmentId"],
            question=d["question"],
            reply=d["reply"],
            criteria={str(k): str(v) for k, v in d["criteria"].items()},
            score=float(d["score"]),
            reasoning=d.get("reasoning", ""),
            judged_at=d["judgedAt"],
            config_version=d.get("configVersion"),
            audio=AudioReference.from_dict(audio) if audio else None,
        )


@dataclass(frozen=True, slots=True)
class ReviewQueue:
    path: Path

    def keep(self, turn: FailedTurn) -> bool:
        line = json.dumps(turn.to_dict(), ensure_ascii=False, sort_keys=True)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as queue:
                queue.write(line + "\n")
        except OSError as exc:
            log.warning("failed turn not kept for review", extra={"error": type(exc).__name__})
            return False
        return True


def queue_path(directory: Path, language: str, session_id: str) -> Path:
    return directory / base_language(language) / f"{session_id}{QUEUE_SUFFIX}"


def review_queue(
    cfg: ResolvedSessionConfig, env: Mapping[str, str] = os.environ
) -> ReviewQueue | None:
    if not cfg.scribe.scoring.keep_failures:
        return None
    directory = env.get(REVIEW_DIR_ENV, "").strip()
    if not directory:
        log.warning("failed turns are not kept: no review folder", extra={"env": REVIEW_DIR_ENV})
        return None
    return ReviewQueue(queue_path(Path(directory), cfg.language, cfg.session_id))


def audio_consent(cfg: ResolvedSessionConfig) -> str | None:
    recording = cfg.recording
    if not recording.enabled or not recording.consent_artifact_id:
        return None
    return recording.consent_artifact_id


def read_queue(directory: Path, language: str) -> Iterator[FailedTurn]:
    folder = directory / base_language(language)
    if not folder.is_dir():
        return
    for path in sorted(folder.glob(f"*{QUEUE_SUFFIX}")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                yield FailedTurn.from_dict(json.loads(line))
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise ValueError(f"{path}:{number}: not a review entry: {exc}") from exc


__all__ = [
    "FAIL",
    "QUEUE_VERSION",
    "REVIEW_DIR_ENV",
    "AudioReference",
    "FailedTurn",
    "ReviewQueue",
    "audio_consent",
    "queue_path",
    "read_queue",
    "review_queue",
]
