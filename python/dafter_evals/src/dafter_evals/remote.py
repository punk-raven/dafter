from __future__ import annotations

import io
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass

Fetch = Callable[[int, int], bytes]
TIMEOUT_S = 60


@dataclass(frozen=True, slots=True)
class Resource:
    url: str
    size: int
    etag: str
    last_modified: str


def describe(url: str, headers: Mapping[str, str] | None = None) -> Resource:
    request = urllib.request.Request(url, method="HEAD", headers=dict(headers or {}))
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        h = response.headers
        return Resource(
            url, int(h["Content-Length"]), h.get("ETag", ""), h.get("Last-Modified", "")
        )


def ranged(url: str, headers: Mapping[str, str] | None = None) -> Fetch:
    def fetch(start: int, end: int) -> bytes:
        wanted = {**(headers or {}), "Range": f"bytes={start}-{end - 1}"}
        request = urllib.request.Request(url, headers=wanted)
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            data: bytes = response.read()
            return data

    return fetch


class RangeFile(io.RawIOBase):
    def __init__(self, size: int, fetch: Fetch) -> None:
        self._size = size
        self._fetch = fetch
        self._position = 0
        self.fetched = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._position, io.SEEK_END: self._size}[whence]
        self._position = base + offset
        return self._position

    def readinto(self, buffer: bytearray | memoryview) -> int:  # type: ignore[override]
        end = min(self._position + len(buffer), self._size)
        if end <= self._position:
            return 0
        data = self._fetch(self._position, end)
        buffer[: len(data)] = data
        self._position += len(data)
        self.fetched += len(data)
        return len(data)


__all__ = ["Fetch", "RangeFile", "Resource", "describe", "ranged"]
