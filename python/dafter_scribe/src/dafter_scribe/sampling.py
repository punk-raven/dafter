from __future__ import annotations

import hashlib
from dataclasses import dataclass

from dafter_core.config import ResolvedSessionConfig

HASH_BYTES = 8
HASH_SPAN = float(1 << (8 * HASH_BYTES))


@dataclass(frozen=True, slots=True)
class Sampling:
    rate: float
    cap: int

    @property
    def scores_any(self) -> bool:
        return self.rate > 0 and self.cap > 0

    def picks(self, segment: str) -> bool:
        if self.rate <= 0:
            return False
        if self.rate >= 1:
            return True
        digest = hashlib.sha256(segment.encode("utf-8")).digest()
        return int.from_bytes(digest[:HASH_BYTES], "big") / HASH_SPAN < self.rate


def sampling_for(cfg: ResolvedSessionConfig) -> Sampling:
    scoring = cfg.scribe.scoring
    return Sampling(scoring.rate_for(cfg.language), scoring.max_turns_per_session)


__all__ = ["Sampling", "sampling_for"]
