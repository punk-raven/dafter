from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum

from dafter_core.config import ResolvedSessionConfig
from dafter_core.versioning import version_label
from dafter_evals.screen.judge import VERDICTS
from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram

PLACE = ("language", "version", "arm")
JUDGED = (*PLACE, "criterion", "verdict")
OUTCOMES = (*PLACE, "outcome")
SCORE_BUCKETS = (0.0, 0.25, 0.5, 0.625, 0.75, 0.875, 1.0)
STABLE = "stable"


class Outcome(StrEnum):
    SCORED = "scored"
    ERRORED = "errored"
    UNSAMPLED = "unsampled"
    CAPPED = "capped"
    DROPPED = "dropped"
    KEPT_FOR_REVIEW = "kept_for_review"


class ScribeMetrics:
    def __init__(self, registry: CollectorRegistry = REGISTRY) -> None:
        self.registry = registry
        self.turns = Counter(
            "dafter_scribe_turns",
            "Agent turns the scribe saw, by what became of them: scored, errored (the judge "
            "failed), unsampled, capped (past scoring.maxTurnsPerSession) or dropped (the judge "
            "fell behind). kept_for_review counts the scored turns with a fail verdict written "
            "to the review queue, so it overlaps scored.",
            OUTCOMES,
            registry=registry,
        )
        self.verdicts = Counter(
            "dafter_scribe_verdicts",
            "Judge verdicts on sampled agent turns, one per criterion of the dafter-screen judge.",
            JUDGED,
            registry=registry,
        )
        self.scores = Histogram(
            "dafter_scribe_turn_score",
            "A scored turn's mean over the criteria: pass 1, maybe 0.5, fail 0.",
            PLACE,
            buckets=SCORE_BUCKETS,
            registry=registry,
        )


def place_of(cfg: ResolvedSessionConfig) -> tuple[str, str, str]:
    version = cfg.version
    arm = version.arm if version is not None else STABLE
    return (cfg.language, version_label(version), arm)


class Quality:
    def __init__(
        self, metrics: ScribeMetrics, place: tuple[str, str, str], criteria: Iterable[str]
    ) -> None:
        self._metrics = metrics
        self._place = place
        for outcome in Outcome:
            metrics.turns.labels(*place, str(outcome))
        for criterion in criteria:
            for verdict in sorted(VERDICTS):
                metrics.verdicts.labels(*place, criterion, verdict)
        metrics.scores.labels(*place)

    def turn(self, outcome: Outcome) -> None:
        self._metrics.turns.labels(*self._place, str(outcome)).inc()

    def scored(self, verdicts: Mapping[str, str], score: float) -> None:
        self.turn(Outcome.SCORED)
        for criterion, verdict in verdicts.items():
            self._metrics.verdicts.labels(*self._place, criterion, verdict).inc()
        self._metrics.scores.labels(*self._place).observe(score)


SCRIBE = ScribeMetrics()

__all__ = ["PLACE", "SCRIBE", "Outcome", "Quality", "ScribeMetrics", "place_of"]
