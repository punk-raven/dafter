from __future__ import annotations

import asyncio
import json
import unicodedata
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from dafter_core.config import parse
from dafter_core.enums import SpeechNormalization
from dafter_core.speech import Speech
from dafter_runtime.speech_plan import SpeechPlan, Substitutions, sentence_cut
from dafter_runtime.spoken_hindi import normalize, number_words, year_words
from livekit.agents import Agent, AgentSession, llm
from session_rig import SlowReader, Speaker, until
from stub_llm import StubLLM

ROOT = Path(__file__).resolve().parents[3] / "testdata"
VECTORS = json.loads((ROOT / "speech" / "hindi-normalization.json").read_bytes())
JOB = ROOT / "agent" / "hindi-webrtc-job.json"


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


@pytest.mark.parametrize("case", VECTORS, ids=[f"{c['kind']}-{i}" for i, c in enumerate(VECTORS)])
def test_each_vector_is_spoken_as_pinned(case: dict[str, str]) -> None:
    assert normalize(case["text"]) == nfc(case["spoken"])


@pytest.mark.parametrize(
    ("n", "spoken"),
    [
        (0, "शून्य"),
        (99, "निन्यानवे"),
        (100, "एक सौ"),
        (1000, "एक हज़ार"),
        (100000, "एक लाख"),
        (12345678, "एक करोड़ तेईस लाख पैंतालीस हज़ार छह सौ अठहत्तर"),
        (10**9, "एक सौ करोड़"),
    ],
)
def test_numbers_are_grouped_the_indian_way(n: int, spoken: str) -> None:
    assert number_words(n) == nfc(spoken)


def test_years_before_2000_are_read_as_hundreds() -> None:
    assert year_words(1947) == nfc("उन्नीस सौ सैंतालीस")
    assert year_words(1900) == nfc("उन्नीस सौ")
    assert year_words(2026) == nfc("दो हज़ार छब्बीस")


def plan(**speech: Any) -> SpeechPlan:
    return SpeechPlan(Speech(**speech), "hi")


async def chunks(*parts: str) -> AsyncIterator[str]:
    for part in parts:
        yield part


def streamed(p: SpeechPlan, *parts: str) -> list[str]:
    async def run() -> list[str]:
        return [c async for c in p.transform(chunks(*parts))]

    return asyncio.run(run())


def test_a_number_split_across_tokens_is_spoken_whole_sentence_by_sentence() -> None:
    spoken = streamed(plan(), "कुल ₹1,2", "5,000 लगें", "गे। फ़ोन 98765 ", "43210 पर करें")
    assert spoken == [
        nfc("कुल एक लाख पच्चीस हज़ार रुपये लगेंगे। "),
        nfc("फ़ोन नौ आठ सात छह पाँच, चार तीन दो एक शून्य पर करें"),
    ]


def test_a_sentence_is_not_cut_before_a_number() -> None:
    assert sentence_cut("कीमत Rs. 5") == 0
    assert sentence_cut("ठीक है। 5 मिनट") == 0
    assert sentence_cut("ठीक है। अब") == len("ठीक है। ")


def test_a_long_run_without_a_sentence_end_is_released_between_words() -> None:
    text = "शब्द " * 70 + "12 345"
    cut = sentence_cut(text)
    assert 0 < cut < len(text)
    assert not text[cut - 2].isdigit()


def test_numbered_lists_lose_their_markers() -> None:
    spoken = streamed(plan(normalization=SpeechNormalization.PROVIDER), "1. पहले फ़ॉर्म भरें\n2) फिर ")
    assert "".join(spoken) == "पहले फ़ॉर्म भरें\nफिर "


def test_the_provider_mode_leaves_digits_for_tts() -> None:
    p = plan(normalization=SpeechNormalization.PROVIDER)
    assert "".join(streamed(p, "कुल ₹1,25,000 लगेंगे।")) == "कुल ₹1,25,000 लगेंगे।"


def test_a_language_without_rules_passes_through() -> None:
    p = SpeechPlan(Speech(), "ta-IN")
    assert p.spoken("It costs ₹500.") == "It costs ₹500."


def test_substitutions_replace_whole_words_only_in_the_sessions_language() -> None:
    words = {"hi": {"Sarvam": "सर्वम्", "Dafter": "डैफ़्टर"}, "en": {"Sarvam": "Survum"}}
    p = plan(substitutions=words)
    assert p.spoken("Sarvam और Dafter, Sarvamx नहीं") == nfc("सर्वम् और डैफ़्टर, Sarvamx नहीं")
    assert Substitutions({})("Sarvam") == "Sarvam"


def test_the_session_hands_tts_the_spoken_text_and_keeps_the_written_transcript() -> None:
    cfg = parse(JOB.read_bytes())
    reply = "कुल ₹1,25,000 लगेंगे। **ज़रूरी** बात।"

    async def run() -> tuple[list[str], list[str]]:
        reader = SlowReader(0.1)
        session: AgentSession[Any] = AgentSession(
            llm=StubLLM(reply),
            tts=reader,
            tts_text_transforms=SpeechPlan(cfg.agent.speech, cfg.language).transforms(),
        )
        session.output.audio = Speaker()
        await session.start(Agent(instructions=""), record=False)
        await session.generate_reply(user_input="कितना लगेगा?")
        await until(lambda: len(reader.read) >= 1)
        said = [
            m.text_content or ""
            for m in session.history.items
            if isinstance(m, llm.ChatMessage) and m.role == "assistant"
        ]
        await session.aclose()
        return reader.read, said

    read, said = asyncio.run(run())
    assert " ".join(r.strip() for r in read) == nfc("कुल एक लाख पच्चीस हज़ार रुपये लगेंगे। ज़रूरी बात।")
    assert said == [reply]
