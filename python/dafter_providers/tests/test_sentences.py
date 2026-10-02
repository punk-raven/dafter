from __future__ import annotations

import asyncio

import pytest
from dafter_core.config import ProviderRef
from dafter_providers import sarvam
from dafter_providers.sarvam.sentences import SentenceTokenizer, split_sentences

REPLY = "नमस्ते मेरे दोस्त, कैसे हो। आज मौसम बहुत अच्छा है। आप कैसे हैं?"
SENTENCES = ["नमस्ते मेरे दोस्त, कैसे हो।", "आज मौसम बहुत अच्छा है।", "आप कैसे हैं?"]


@pytest.fixture(autouse=True)
def sarvam_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SARVAM_API_KEY", "test-only-not-a-key")


def test_a_danda_ends_a_sentence_and_a_decimal_point_does_not() -> None:
    assert SentenceTokenizer().tokenize(REPLY) == SENTENCES
    assert [s for s, _, _ in split_sentences("तापमान 3.5 डिग्री है। ठीक है।")] == [
        "तापमान 3.5 डिग्री है।",
        "ठीक है।",
    ]


def test_sentences_leave_while_the_reply_is_still_being_written() -> None:
    async def run() -> tuple[list[str], list[str]]:
        stream = SentenceTokenizer().stream()
        stream.push_text(REPLY)
        early = [(await asyncio.wait_for(stream.__anext__(), 1)).token for _ in range(2)]
        stream.end_input()
        return early, [token.token async for token in stream]

    assert asyncio.run(run()) == (SENTENCES[:2], SENTENCES[2:])


def test_sarvam_tts_streams_through_the_danda_aware_tokenizer() -> None:
    ref = ProviderRef(
        provider="sarvam",
        model="bulbul:v3",
        credential_ref="secret://tenants/t_9c21a4be/sarvam/api-key",
    )
    tts = sarvam.build_tts(ref, "hi")
    assert isinstance(tts._opts.word_tokenizer, SentenceTokenizer)  # type: ignore[attr-defined]
