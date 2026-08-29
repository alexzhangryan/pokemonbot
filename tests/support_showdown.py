"""Shared websocket helpers for talking to a local Showdown server in tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from websockets.asyncio.client import ClientConnection

# A hand-built, format-validated team (`node pokemon-showdown validate-team
# gen9championsvgc2026regmb` exits 0), packed for `/utm`. Champions VGC
# requires "your own team" before it will accept a challenge.
PACKED_TEST_TEAM = (
    "Incineroar|||Blaze|Protect,FlareBlitz,FireBlast,BulkUp|Adamant|||||50|]"
    "Aegislash|||StanceChange|Protect,ShadowBall,IronHead,SwordsDance|Modest|||||50|]"
    "Corviknight|||Pressure|Protect,IronHead,Uturn,BulkUp|Impish|||||50|]"
    "Clefable|||MagicGuard|Protect,Moonblast,Moonlight,ShadowBall|Calm|||||50|]"
    "Conkeldurr|||Guts|Protect,DrainPunch,StoneEdge,BulkUp|Adamant|||||50|]"
    "Charizard|||Blaze|Protect,FlareBlitz,FireBlast,Earthquake|Modest|||||50|"
)


async def recv_until(
    ws: ClientConnection, predicate: Callable[[str], bool], timeout: float = 10.0
) -> str:
    async with asyncio.timeout(timeout):
        while True:
            message = await ws.recv()
            assert isinstance(message, str)
            if predicate(message):
                return message


async def login(ws: ClientConnection, username: str) -> None:
    """Log in without a signed token, as allowed by the server's --no-security flag."""
    await recv_until(ws, lambda m: m.startswith("|challstr|"))
    await ws.send(f"|/trn {username},0,")
    await recv_until(ws, lambda m: "|updateuser|" in m)
