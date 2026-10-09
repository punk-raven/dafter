from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import hub, indicvoices, mucs, svarah
from .corpus import Archive, Clip, Sample, pin_kathbath
from .remote import describe, ranged

HF_TOKEN = "HF_TOKEN"
KATHBATH = "kathbath"
SVARAH = "svarah"
MUCS2021 = "mucs2021"
MUCS2021_CODESWITCH = "mucs2021-codeswitch"
HUB_PINS = {
    SVARAH: svarah.pin_svarah,
    indicvoices.INDICVOICES: indicvoices.pin_indicvoices,
    indicvoices.LAHAJA: indicvoices.pin_lahaja,
}
TARBALLS = (MUCS2021, MUCS2021_CODESWITCH)
DATASETS = (KATHBATH, *HUB_PINS, *TARBALLS)
ARCHIVES = "archives"

Reader = Callable[[Clip], bytes]


def hub_token(dataset: str) -> str:
    value = os.environ.get(HF_TOKEN)
    if not value:
        raise SystemExit(f"{dataset} is gated on Hugging Face: set {HF_TOKEN} (see the README)")
    return value


def zip_archive(source: dict[str, Any]) -> Archive:
    found = describe(source["archive"])
    if (found.size, found.etag) != (source["bytes"], source["etag"]):
        raise SystemExit(f"{source['archive']} changed since it was pinned: {found}")
    return Archive(found.size, ranged(source["archive"]))


def tarball(audio_root: Path, dataset: str, language: str, source: dict[str, Any]) -> Path:
    path = audio_root / ARCHIVES / f"{dataset}-{language}.tar.gz"
    try:
        return mucs.download(source["languages"][language], path)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def pin_sample(
    dataset: str, source: dict[str, Any], language: str, count: int, audio_root: Path
) -> tuple[Sample, dict[str, bytes]]:
    if language not in source["languages"]:
        raise SystemExit(f"{dataset} has no {language}; it has {', '.join(source['languages'])}")
    if dataset == KATHBATH:
        return pin_kathbath(zip_archive(source), source, language, count)
    if dataset in HUB_PINS:
        get = hub.authorized(hub_token(dataset))
        return HUB_PINS[dataset](get, source, language, count)
    if dataset in TARBALLS:
        archive = tarball(audio_root, dataset, language, source)
        return mucs.pin_mucs(archive, source, dataset, language, count)
    raise SystemExit(f"unknown dataset {dataset}; known: {', '.join(DATASETS)}")


def clip_reader(sample: Sample, source: dict[str, Any], audio_root: Path) -> Reader:
    if sample.dataset == KATHBATH:
        archive = zip_archive(source)
        return lambda clip: archive.read(clip.member)
    if sample.dataset in HUB_PINS:
        get = hub.authorized(hub_token(sample.dataset))
        return lambda clip: hub.audio(get, source["dataset"], clip.member)
    if sample.dataset in TARBALLS:
        return mucs.clip_reader(
            lambda: tarball(audio_root, sample.dataset, sample.language, source), sample
        )
    raise SystemExit(f"unknown dataset {sample.dataset}; known: {', '.join(DATASETS)}")


__all__ = [
    "DATASETS",
    "HF_TOKEN",
    "HUB_PINS",
    "TARBALLS",
    "clip_reader",
    "hub_token",
    "pin_sample",
    "tarball",
    "zip_archive",
]
