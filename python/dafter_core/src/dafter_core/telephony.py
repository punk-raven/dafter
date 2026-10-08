from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .enums import CallerCheck, PhoneGuests, RecordingNotice


@dataclass(frozen=True, slots=True)
class DialIn:
    caller_check: CallerCheck = CallerCheck.PIN

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DialIn:
        return cls(caller_check=CallerCheck(d.get("callerCheck", CallerCheck.PIN)))


@dataclass(frozen=True, slots=True)
class Telephony:
    trunk: str | None = None
    phone_guests: PhoneGuests = PhoneGuests.OFF
    ringing_timeout_seconds: int = 30
    max_call_duration_seconds: int = 1800
    recording_notice: RecordingNotice = RecordingNotice.ALWAYS
    dial_in: DialIn | None = None

    @property
    def caller_check(self) -> CallerCheck:
        return CallerCheck.PIN if self.dial_in is None else self.dial_in.caller_check

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Telephony:
        dial_in = d.get("dialIn")
        return cls(
            trunk=d.get("trunk"),
            phone_guests=PhoneGuests(d.get("phoneGuests", PhoneGuests.OFF)),
            ringing_timeout_seconds=d.get("ringingTimeoutSeconds", 30),
            max_call_duration_seconds=d.get("maxCallDurationSeconds", 1800),
            recording_notice=RecordingNotice(d.get("recordingNotice", RecordingNotice.ALWAYS)),
            dial_in=None if dial_in is None else DialIn.from_dict(dial_in),
        )
