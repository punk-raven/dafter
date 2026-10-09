from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from dafter_evals.golden import (
    GOLDEN_ROOT,
    LABEL_PATTERN,
    LANGUAGES,
    GoldenClip,
    GoldenEntity,
    load,
    parse_manifest,
)

EXAMPLE = GOLDEN_ROOT / "example.json"
SCHEMA = GOLDEN_ROOT / "manifest.schema.json"


def example_document() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    return document


def write_manifest(root: Path, language: str, document: Any) -> None:
    (root / f"{language}.json").write_text(
        json.dumps(document, ensure_ascii=False), encoding="utf-8"
    )


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_language_has_a_pinned_manifest_that_loads(language: str) -> None:
    assert (GOLDEN_ROOT / f"{language}.json").is_file()
    assert load(language) == []


def test_a_missing_manifest_loads_as_no_clips(tmp_path: Path) -> None:
    assert load("hi", tmp_path) == []


def test_the_example_manifest_loads_every_field(tmp_path: Path) -> None:
    write_manifest(tmp_path, "hi", example_document())
    [clip] = load("hi", tmp_path)
    assert clip == GoldenClip(
        clip_id="hi-0001",
        language="hi",
        audio_path=tmp_path / "audio" / "hi" / "hi-0001.wav",
        channel="telephony_8k",
        reference_native="मेरा नंबर 98450 12345 है और 500 रुपये भेजो",
        reference_romanized="mera number 98450 12345 hai aur 500 rupaye bhejo",
        entities=(
            GoldenEntity(kind="phone", text="98450 12345", start_char=10, end_char=21),
            GoldenEntity(kind="amount", text="500 रुपये", start_char=28, end_char=37),
        ),
        labels=("turn:end", "codemix", "noise:traffic", "voice:male_adult"),
        consent_id="consent-example-0001",
    )


def test_a_clip_is_frozen(tmp_path: Path) -> None:
    write_manifest(tmp_path, "hi", example_document())
    [clip] = load("hi", tmp_path)
    with pytest.raises(AttributeError):
        clip.clip_id = "other"  # type: ignore[misc]


def test_optional_fields_default_to_empty(tmp_path: Path) -> None:
    document = example_document()
    clip = document["clips"][0]
    del clip["entities"], clip["labels"]
    clip["reference"]["romanized"] = None
    [loaded] = parse_manifest(document, "hi", tmp_path)
    assert loaded.entities == ()
    assert loaded.labels == ()
    assert loaded.reference_romanized is None


def test_text_that_is_not_json_is_malformed(tmp_path: Path) -> None:
    (tmp_path / "hi.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        load("hi", tmp_path)


def first_clip(document: dict[str, Any]) -> dict[str, Any]:
    clip: dict[str, Any] = document["clips"][0]
    return clip


def first_entity(document: dict[str, Any]) -> dict[str, Any]:
    entity: dict[str, Any] = first_clip(document)["entities"][0]
    return entity


MALFORMATIONS: dict[str, Callable[[dict[str, Any]], object]] = {
    "version": lambda d: d.update(version=2),
    "language": lambda d: d.update(language="te-IN"),
    "review": lambda d: d["nativeReview"].update(status="done"),
    "approved without reviewers": lambda d: d["nativeReview"].update(status="approved"),
    "clips not a list": lambda d: d.update(clips={}),
    "duplicate id": lambda d: d["clips"].append(copy.deepcopy(first_clip(d))),
    "channel": lambda d: first_clip(d).update(channel="pstn"),
    "consent": lambda d: first_clip(d).pop("consentId"),
    "blank consent": lambda d: first_clip(d).update(consentId="  "),
    "one annotator": lambda d: first_clip(d).update(annotators=["annotator-a"]),
    "same annotator twice": lambda d: first_clip(d).update(
        annotators=["annotator-a", "annotator-a"]
    ),
    "native reference": lambda d: first_clip(d)["reference"].pop("native"),
    "romanized": lambda d: first_clip(d)["reference"].update(romanized=7),
    "audio outside": lambda d: first_clip(d).update(audio="../secret.wav"),
    "audio absolute": lambda d: first_clip(d).update(audio="/audio/hi/x.wav"),
    "audio not under audio": lambda d: first_clip(d).update(audio="clips/x.wav"),
    "audio directory only": lambda d: first_clip(d).update(audio="audio"),
    "entity kind": lambda d: first_entity(d).update(kind="email"),
    "entity span": lambda d: first_entity(d).update(startChar=11),
    "entity past end": lambda d: first_entity(d).update(endChar=999),
    "entity reversed": lambda d: first_entity(d).update(startChar=21, endChar=10),
    "entity bool offset": lambda d: first_entity(d).update(startChar=True),
    "label": lambda d: first_clip(d).update(labels=["turn:maybe"]),
    "label case": lambda d: first_clip(d).update(labels=["noise:Traffic"]),
    "labels not a list": lambda d: first_clip(d).update(labels="turn:end"),
}


@pytest.mark.parametrize("change", list(MALFORMATIONS))
def test_a_malformed_manifest_is_refused(tmp_path: Path, change: str) -> None:
    document = example_document()
    MALFORMATIONS[change](document)
    write_manifest(tmp_path, "hi", document)
    with pytest.raises(ValueError):
        load("hi", tmp_path)


def test_a_manifest_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    write_manifest(tmp_path, "hi", [])
    with pytest.raises(ValueError, match="must be an object"):
        load("hi", tmp_path)


def test_an_approved_review_with_reviewers_loads(tmp_path: Path) -> None:
    document = example_document()
    document["nativeReview"] = {"status": "approved", "reviewers": ["reviewer-hi"]}
    assert len(parse_manifest(document, "hi", tmp_path)) == 1


@pytest.mark.parametrize(
    ("label", "known"),
    [
        ("turn:hold", True),
        ("turn:end", True),
        ("codemix", True),
        ("backchannel:acknowledge", True),
        ("interruption:correction", True),
        ("noise:traffic", True),
        ("voice:female_adult", True),
        ("turn:pause", False),
        ("backchannel:", False),
        ("noise:traffic_", False),
        ("weather:rain", False),
    ],
)
def test_label_vocabulary(label: str, known: bool) -> None:
    assert bool(LABEL_PATTERN.match(label)) is known


def test_the_schema_label_pattern_matches_the_loader() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    pattern = schema["$defs"]["clip"]["properties"]["labels"]["items"]["pattern"]
    assert re.compile(pattern).pattern == LABEL_PATTERN.pattern
    assert schema["properties"]["language"]["enum"] == list(LANGUAGES)
