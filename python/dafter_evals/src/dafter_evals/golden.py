from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, TypeGuard

GOLDEN_ROOT = Path(__file__).resolve().parents[4] / "testdata" / "golden"
MANIFEST_VERSION = 1
LANGUAGES = ("en-IN", "hi", "te-IN", "kn-IN", "mr-IN")
CHANNELS = frozenset({"telephony_8k", "wideband"})
ENTITY_KINDS = frozenset({"number", "phone", "amount", "name", "address", "date"})
REVIEW_STATUSES = frozenset({"pending", "in_review", "approved"})
MINIMUM_ANNOTATORS = 2
LABEL_PATTERN = re.compile(
    r"^(?:turn:(?:hold|end)|codemix"
    r"|(?:backchannel|interruption|noise|voice):[a-z0-9]+(?:_[a-z0-9]+)*)$"
)
AUDIO_DIRECTORY = "audio"


@dataclass(frozen=True, slots=True)
class GoldenEntity:
    kind: str
    text: str
    start_char: int
    end_char: int


@dataclass(frozen=True, slots=True)
class GoldenClip:
    clip_id: str
    language: str
    audio_path: Path
    channel: str
    reference_native: str
    reference_romanized: str | None
    entities: tuple[GoldenEntity, ...]
    labels: tuple[str, ...]
    consent_id: str


def manifest_path(language: str, root: Path | None = None) -> Path:
    return (root or GOLDEN_ROOT) / f"{language}.json"


def load(language: str, root: Path | None = None) -> list[GoldenClip]:
    base = root or GOLDEN_ROOT
    path = manifest_path(language, base)
    if not path.is_file():
        return []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"{path}: not valid JSON: {e}") from e
    return parse_manifest(document, language, base, str(path))


def parse_manifest(
    document: Any, language: str, root: Path, source: str = "manifest"
) -> list[GoldenClip]:
    manifest = _require_object(document, source)
    if manifest.get("version") != MANIFEST_VERSION:
        raise ValueError(f"{source}: version must be {MANIFEST_VERSION}")
    if manifest.get("language") != language:
        raise ValueError(f"{source}: language must be {language!r}")
    _parse_review(manifest.get("nativeReview"), f"{source}.nativeReview")
    clips = manifest.get("clips")
    if not isinstance(clips, list):
        raise ValueError(f"{source}.clips: must be a list")
    parsed = [
        _parse_clip(clip, language, root, f"{source}.clips[{index}]")
        for index, clip in enumerate(clips)
    ]
    seen: set[str] = set()
    for clip in parsed:
        if clip.clip_id in seen:
            raise ValueError(f"{source}: duplicate clip id {clip.clip_id!r}")
        seen.add(clip.clip_id)
    return parsed


def _parse_review(value: Any, where: str) -> None:
    review = _require_object(value, where)
    status = review.get("status")
    if status not in REVIEW_STATUSES:
        raise ValueError(f"{where}.status: must be one of {sorted(REVIEW_STATUSES)}")
    reviewers = review.get("reviewers", [])
    if not isinstance(reviewers, list) or not all(_is_text(r) for r in reviewers):
        raise ValueError(f"{where}.reviewers: must be a list of non-empty strings")
    if status == "approved" and not reviewers:
        raise ValueError(f"{where}: an approved review names its reviewers")


def _parse_clip(value: Any, language: str, root: Path, where: str) -> GoldenClip:
    clip = _require_object(value, where)
    clip_id = _require_text(clip, "id", where)
    channel = _require_text(clip, "channel", where)
    if channel not in CHANNELS:
        raise ValueError(f"{where}.channel: must be one of {sorted(CHANNELS)}")
    reference = _require_object(clip.get("reference"), f"{where}.reference")
    native = _require_text(reference, "native", f"{where}.reference")
    romanized = reference.get("romanized")
    if romanized is not None and not _is_text(romanized):
        raise ValueError(f"{where}.reference.romanized: must be a non-empty string or null")
    entities = clip.get("entities", [])
    if not isinstance(entities, list):
        raise ValueError(f"{where}.entities: must be a list")
    labels = clip.get("labels", [])
    if not isinstance(labels, list):
        raise ValueError(f"{where}.labels: must be a list")
    _require_annotators(clip.get("annotators"), f"{where}.annotators")
    return GoldenClip(
        clip_id=clip_id,
        language=language,
        audio_path=_audio_path(clip.get("audio"), root, f"{where}.audio"),
        channel=channel,
        reference_native=native,
        reference_romanized=romanized,
        entities=tuple(
            _parse_entity(entity, native, f"{where}.entities[{index}]")
            for index, entity in enumerate(entities)
        ),
        labels=tuple(_parse_label(label, f"{where}.labels") for label in labels),
        consent_id=_require_text(clip, "consentId", where),
    )


def _parse_entity(value: Any, native: str, where: str) -> GoldenEntity:
    entity = _require_object(value, where)
    kind = _require_text(entity, "kind", where)
    if kind not in ENTITY_KINDS:
        raise ValueError(f"{where}.kind: must be one of {sorted(ENTITY_KINDS)}")
    text = _require_text(entity, "text", where)
    start, end = entity.get("startChar"), entity.get("endChar")
    if not _is_offset(start) or not _is_offset(end) or not start < end <= len(native):
        raise ValueError(f"{where}: startChar and endChar must span the native reference")
    if native[start:end] != text:
        raise ValueError(f"{where}: text {text!r} is not native[{start}:{end}]")
    return GoldenEntity(kind=kind, text=text, start_char=start, end_char=end)


def _parse_label(value: Any, where: str) -> str:
    if not isinstance(value, str) or not LABEL_PATTERN.match(value):
        raise ValueError(f"{where}: unknown label {value!r}")
    return value


def _audio_path(value: Any, root: Path, where: str) -> Path:
    if not _is_text(value):
        raise ValueError(f"{where}: must be a non-empty string")
    relative = PurePosixPath(value)
    parts = relative.parts
    if relative.is_absolute() or ".." in parts or len(parts) < 2 or parts[0] != AUDIO_DIRECTORY:
        raise ValueError(f"{where}: must be a path under {AUDIO_DIRECTORY}/")
    return root.joinpath(*parts)


def _require_annotators(value: Any, where: str) -> None:
    if not isinstance(value, list) or not all(_is_text(a) for a in value):
        raise ValueError(f"{where}: must be a list of annotator ids")
    if len(set(value)) < MINIMUM_ANNOTATORS:
        raise ValueError(f"{where}: needs {MINIMUM_ANNOTATORS} distinct annotators")


def _require_object(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{where}: must be an object")
    return value


def _require_text(container: dict[str, Any], key: str, where: str) -> str:
    value = container.get(key)
    if not _is_text(value):
        raise ValueError(f"{where}.{key}: must be a non-empty string")
    return value


def _is_text(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip())


def _is_offset(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
