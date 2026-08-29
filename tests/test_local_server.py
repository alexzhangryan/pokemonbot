"""T0.5: the local Showdown server accepts a websocket connection and a
challenge in gen9championsvgc2026regmb."""

from __future__ import annotations

from websockets.asyncio.client import connect

from tests.support_showdown import PACKED_TEST_TEAM, login, recv_until

FORMAT_ID = "gen9championsvgc2026regmb"


async def test_server_accepts_websocket_connection(showdown_server: int) -> None:
    uri = f"ws://localhost:{showdown_server}/showdown/websocket"
    async with connect(uri) as ws:
        message = await recv_until(ws, lambda m: m.startswith("|challstr|"))
    assert message.startswith("|challstr|")


async def test_server_accepts_challenge_in_regmb(showdown_server: int) -> None:
    uri = f"ws://localhost:{showdown_server}/showdown/websocket"
    async with connect(uri) as ws_a, connect(uri) as ws_b:
        await login(ws_a, "t0p5usera")
        await login(ws_b, "t0p5userb")

        await ws_a.send(f"|/utm {PACKED_TEST_TEAM}")
        await ws_b.send(f"|/utm {PACKED_TEST_TEAM}")

        await ws_a.send(f"|/challenge t0p5userb,{FORMAT_ID}")
        message = await recv_until(ws_b, lambda m: f"/challenge {FORMAT_ID}" in m)

        assert FORMAT_ID in message
