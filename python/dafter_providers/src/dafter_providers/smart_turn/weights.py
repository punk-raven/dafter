from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from pathlib import Path

import httpx

REPOSITORY = "pipecat-ai/smart-turn-v3"
REVISION = "f766f81d3cfdf7737ac64aad813d91bbfd56bf93"
WEIGHTS_FILE = "smart-turn-v3.2-cpu.onnx"
WEIGHTS_SHA256 = "2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f"
WEIGHTS_URL = f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{WEIGHTS_FILE}"
CACHE_ENV = "DAFTER_MODEL_CACHE"
DOWNLOAD_TIMEOUT_S = 120.0
READ_CHUNK_BYTES = 1 << 20

Download = Callable[[str, Path], None]


class WeightsMismatchError(Exception):
    pass


def cache_root() -> Path:
    if configured := os.environ.get(CACHE_ENV):
        return Path(configured)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "dafter" / "models"


def weights_path() -> Path:
    return cache_root() / "smart_turn" / REVISION / WEIGHTS_FILE


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, into: Path) -> None:
    with (
        httpx.stream("GET", url, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT_S) as response,
        into.open("wb") as f,
    ):
        response.raise_for_status()
        for chunk in response.iter_bytes():
            f.write(chunk)


def fetch(get: Download = download) -> Path:
    path = weights_path()
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.{os.getpid()}.part")
    try:
        get(WEIGHTS_URL, partial)
        if (digest := sha256_of(partial)) != WEIGHTS_SHA256:
            raise WeightsMismatchError(
                f"{WEIGHTS_FILE} at {REVISION} hashed {digest}, pinned {WEIGHTS_SHA256}"
            )
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)
    return path
