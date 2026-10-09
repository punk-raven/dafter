from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from dafter_core.config import ResolvedSessionConfig

from .quality import SCRIBE, Quality, ScribeMetrics, place_of
from .review import ReviewQueue, audio_consent, review_queue
from .sampling import Sampling, sampling_for

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class SessionFacts:
    session_id: str
    language: str
    channel: str
    config_version: dict[str, str] | None
    audio_consent: str | None


@dataclass(frozen=True, slots=True)
class ScoringLoop:
    sampling: Sampling
    session: SessionFacts
    metrics: ScribeMetrics
    place: tuple[str, str, str]
    queue: ReviewQueue | None = None
    clock: Clock = utc_now

    def quality(self, criteria: list[str]) -> Quality:
        return Quality(self.metrics, self.place, criteria)


def session_facts(cfg: ResolvedSessionConfig) -> SessionFacts:
    version = cfg.version
    return SessionFacts(
        session_id=cfg.session_id,
        language=cfg.language,
        channel=str(cfg.channel),
        config_version=None if version is None else {"id": version.id, "arm": version.arm},
        audio_consent=audio_consent(cfg),
    )


def scoring_loop(
    cfg: ResolvedSessionConfig,
    metrics: ScribeMetrics = SCRIBE,
    env: Mapping[str, str] | None = None,
) -> ScoringLoop:
    return ScoringLoop(
        sampling=sampling_for(cfg),
        session=session_facts(cfg),
        metrics=metrics,
        place=place_of(cfg),
        queue=review_queue(cfg) if env is None else review_queue(cfg, env),
    )


__all__ = ["Clock", "ScoringLoop", "SessionFacts", "scoring_loop", "session_facts", "utc_now"]
