from __future__ import annotations

from typing import Any

import pytest
from dafter_runtime.cost import OutputTokens
from dafter_runtime.timing import FIRST_SENTENCE_MOSTLY, Turns, TurnTiming, first_sentence_share
from livekit.agents.llm import ChatMessage

ONE_SENTENCE = "आज दिल्ली में मौसम साफ़ रहेगा और शाम तक हल्की हवा चलेगी।"
THREE_SENTENCES = "नमस्ते! आज मौसम साफ़ रहेगा। शाम तक हल्की हवा चलेगी और रात में ठंड बढ़ेगी।"


@pytest.mark.parametrize(
    ("text", "share"),
    [
        (ONE_SENTENCE, 1.0),
        ("नमस्ते! बताइए।", len("नमस्ते!") / len("नमस्ते! बताइए।")),
        ("कीमत 3.5 रुपये है। धन्यवाद।", len("कीमत 3.5 रुपये है।") / len("कीमत 3.5 रुपये है। धन्यवाद।")),
        ("   ", None),
        (None, None),
    ],
)
def test_the_first_sentence_ends_at_a_danda_or_a_stop_but_not_a_decimal_point(
    text: str | None, share: float | None
) -> None:
    assert first_sentence_share(text) == share


def waited(ms: int, share: float | None) -> TurnTiming:
    return TurnTiming(
        turn=1,
        interrupted=False,
        first_sentence_share=share,
        seconds={"llm_node_ttft": 0.233, "llm_node_ttfs": 0.233 + ms / 1000},
    )


def test_a_reply_that_is_mostly_its_first_sentence_is_not_serial() -> None:
    timing = waited(617, first_sentence_share(ONE_SENTENCE))
    assert timing.serial() is False
    assert timing.payload()["serial"] is False
    assert timing.log_fields()["first_sentence_share"] == 1.0


def test_a_reply_that_waited_past_a_short_first_sentence_is_serial() -> None:
    share = first_sentence_share(THREE_SENTENCES)
    assert share is not None and share < FIRST_SENTENCE_MOSTLY
    assert waited(617, share).serial() is True
    assert waited(400, share).serial() is False


def test_a_reply_whose_text_is_unknown_is_judged_on_the_wait_alone() -> None:
    assert waited(617, None).serial() is True


def test_a_spoken_reply_carries_the_share_of_its_first_sentence() -> None:
    turns = Turns()
    reply = ChatMessage.model_validate(
        {
            "role": "assistant",
            "content": [THREE_SENTENCES],
            "metrics": {"llm_node_ttft": 0.233, "llm_node_ttfs": 0.85},
        }
    )
    timing = turns.add(reply)
    assert timing is not None
    assert timing.first_sentence_share == first_sentence_share(THREE_SENTENCES)
    assert timing.payload()["serial"] is True


def usage(output: float) -> dict[str, Any]:
    items = [
        {"unit": "input_token", "quantity": 900.0},
        {"unit": "output_token", "quantity": output},
        {"unit": "character", "quantity": 40.0},
    ]
    return {"items": items}


def test_each_turn_logs_the_output_tokens_it_added() -> None:
    generated = OutputTokens()
    assert generated.turn(usage(33.0)) == 33
    assert generated.turn(usage(33.0)) == 0
    assert generated.turn(usage(61.0)) == 28
