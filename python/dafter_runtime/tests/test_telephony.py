from __future__ import annotations

from pathlib import Path

from dafter_core.enums import AddressingMode, Channel
from dafter_runtime.plan import load, plan
from dafter_runtime.worker import room_options, stt_sample_rate

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-telephony-job.json"
POOL = "dafter-py"


def job() -> bytes:
    return JOB.read_bytes().strip()


def test_the_pinned_phone_call_answers_every_turn_on_the_narrowband_line() -> None:
    p = plan(load(job()), POOL)
    assert p.config.channel is Channel.TELEPHONY
    assert p.config.agent.addressing.mode is AddressingMode.ALWAYS
    assert not p.called_by_name
    assert p.opening == p.persona.greeting
    assert stt_sample_rate(p) == 8000
    assert p.pipeline.tts is not None and p.pipeline.tts.options["sampleRate"] == 8000
    options = room_options(p, 8000)
    assert options.audio_input is not False and options.close_on_disconnect
