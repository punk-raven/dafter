from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aiohttp import web
from dafter_evals.connect import join, livekit_url, session_overrides
from dafter_evals.probe import Probe


@pytest.mark.parametrize(
    ("control", "given", "used"),
    [
        ("http://127.0.0.1:8080", "ws://livekit:7880", "ws://127.0.0.1:7880"),
        ("http://localhost:8080", "ws://livekit:7880/rtc", "ws://localhost:7880/rtc"),
        ("https://dafter.example.dev", "ws://livekit:7880", "wss://sfu.example.dev"),
    ],
)
def test_the_media_server_is_reached_the_way_the_browser_client_reaches_it(
    control: str, given: str, used: str
) -> None:
    assert livekit_url(control, given) == used


def test_the_harness_asks_for_an_agent_that_greets_and_answers_every_turn() -> None:
    wanted = {"greets": True, "addressing": {"mode": "always"}}
    assert session_overrides(None) == {"agent": wanted}
    assert session_overrides({"agent": {"enabled": True}, "turn": {"silenceMs": 900}}) == {
        "agent": {**wanted, "enabled": True},
        "turn": {"silenceMs": 900},
    }
    assert session_overrides({"agent": {"greets": False}})["agent"]["greets"] is False


def test_a_probe_that_feeds_back_the_agents_voice_states_a_line_without_echo_cancellation() -> None:
    assert "media" not in session_overrides(None)
    given = {"media": {"audio": {"red": False}, "video": {"enabled": False}}}
    assert session_overrides(given, echoes=True)["media"] == {
        "audio": {"red": False, "echoCancellation": False},
        "video": {"enabled": False},
    }


class Unreachable(Probe):
    def __init__(self) -> None:
        super().__init__()
        self.url: str | None = None

    async def connect(self, url: str, token: str) -> None:
        self.url = url
        raise ConnectionError("failed to lookup address information")


def joined(dispatch: str | None) -> tuple[list[str], str | None]:
    stops: list[str] = []

    async def stop(request: web.Request) -> web.Response:
        stops.append(request.match_info["session"])
        return web.json_response({"sessionId": request.match_info["session"], "recalled": []})

    async def run() -> str | None:
        app = web.Application()
        app.router.add_post("/sessions/{session}/agent/stop", stop)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        created: dict[str, Any] = {
            "sessionId": "s_7f3a9c21",
            "url": "ws://livekit:7880",
            "token": "t",
            "agentDispatchId": dispatch,
        }
        probe = Unreachable()
        try:
            with pytest.raises(ConnectionError):
                await join(probe, f"http://127.0.0.1:{port}", created)
        finally:
            await runner.cleanup()
        return probe.url

    return stops, asyncio.run(run())


def test_a_failed_connect_stops_the_agent_it_dispatched() -> None:
    stops, url = joined("AD_4b1c2d3e")
    assert stops == ["s_7f3a9c21"]
    assert url == "ws://127.0.0.1:7880"


def test_a_session_without_an_agent_has_nothing_to_stop() -> None:
    stops, _ = joined(None)
    assert stops == []
