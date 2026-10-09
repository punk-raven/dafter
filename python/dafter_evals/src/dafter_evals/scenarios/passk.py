from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import comb


def pass_hat_k(trials: int, successes: int, k: int) -> float:
    if not 0 <= successes <= trials:
        raise ValueError("successes lie between 0 and the number of trials")
    if not 1 <= k <= trials:
        raise ValueError("k lies between 1 and the number of trials")
    return comb(successes, k) / comb(trials, k)


def suite_pass_hat_k(results: Mapping[str, Sequence[bool]], k: int) -> float | None:
    if not results:
        return None
    scores = [pass_hat_k(len(r), sum(r), k) for r in results.values()]
    return sum(scores) / len(scores)


def curve(results: Mapping[str, Sequence[bool]], k: int) -> dict[str, float | None]:
    return {f"pass^{n}": suite_pass_hat_k(results, n) for n in range(1, k + 1)}
