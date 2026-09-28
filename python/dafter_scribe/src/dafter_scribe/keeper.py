from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

log = logging.getLogger("dafter.scribe.keeper")

TIMEOUT = aiohttp.ClientTimeout(total=30)


@dataclass(frozen=True, slots=True)
class MinutesKeeper:
    url: str
    secret: str

    async def store(self, session_id: str, envelope: dict[str, Any]) -> int | None:
        url = f"{self.url}/sessions/{session_id}/minutes"
        headers = {"Authorization": f"Bearer {self.secret}"}
        try:
            async with (
                aiohttp.ClientSession(timeout=TIMEOUT) as http,
                http.post(url, json=envelope, headers=headers) as r,
            ):
                status = r.status
                answer = await r.json(content_type=None) if status == 201 else None
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            log.warning("minutes not kept", extra={"error": type(exc).__name__})
            return None
        if not isinstance(answer, dict):
            log.warning("minutes not kept", extra={"status": status})
            return None
        version = answer.get("version")
        return version if isinstance(version, int) else None


__all__ = ["MinutesKeeper"]
