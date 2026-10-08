from __future__ import annotations

import logging
from collections import Counter
from collections.abc import AsyncIterator

from livekit.agents import stt

from .backchannel import TRANSCRIPTS, Events, text_of
from .naming import words

log = logging.getLogger("dafter.runtime.plausible")

FASTEST_CHARS_PER_S = 30.0
JUDGED_CHARS = 40
MOST_REPEATS = 5


def looped(text: str) -> bool:
    heard = words(text)
    if not heard:
        return False
    _, times = Counter(heard).most_common(1)[0]
    return times > MOST_REPEATS and times * 2 >= len(heard)


def too_fast(text: str, seconds: float) -> bool:
    chars = len(text.strip())
    return seconds > 0 and chars >= JUDGED_CHARS and chars / seconds > FASTEST_CHARS_PER_S


def spoken_for(event: stt.SpeechEvent) -> float:
    if event.alternatives:
        timed = event.alternatives[0]
        if timed.end_time > timed.start_time > 0:
            return timed.end_time - timed.start_time
    return 0.0


class Plausible:
    def __call__(self, events: Events) -> Events:
        return self._sieve(events)

    def impossible(self, event: stt.SpeechEvent) -> bool:
        text = text_of(event)
        seconds = spoken_for(event)
        if not (looped(text) or too_fast(text, seconds)):
            return False
        log.info(
            "a transcript no one could have spoken was dropped",
            extra={"chars": len(text), "seconds": round(seconds, 2)},
        )
        return True

    async def _sieve(self, events: Events) -> AsyncIterator[stt.SpeechEvent | str]:
        async for event in events:
            if (
                isinstance(event, stt.SpeechEvent)
                and event.type in TRANSCRIPTS
                and self.impossible(event)
            ):
                continue
            yield event


__all__ = ["Plausible", "looped", "too_fast"]
