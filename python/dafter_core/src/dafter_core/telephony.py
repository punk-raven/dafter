from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .enums import PhoneGuests, RecordingNotice


@dataclass(frozen=True, slots=True)
class Telephony:
    trunk: str | None = None
    phone_guests: PhoneGuests = PhoneGuests.OFF
    ringing_timeout_seconds: int = 30
    max_call_duration_seconds: int = 1800
    recording_notice: RecordingNotice = RecordingNotice.ALWAYS

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Telephony:
        return cls(
            trunk=d.get("trunk"),
            phone_guests=PhoneGuests(d.get("phoneGuests", PhoneGuests.OFF)),
            ringing_timeout_seconds=d.get("ringingTimeoutSeconds", 30),
            max_call_duration_seconds=d.get("maxCallDurationSeconds", 1800),
            recording_notice=RecordingNotice(d.get("recordingNotice", RecordingNotice.ALWAYS)),
        )
