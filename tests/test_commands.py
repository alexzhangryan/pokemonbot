"""The control channel a run reads, and the guard that keeps a conceded battle
from producing one more decision.

Both exist for the same reason: a battle can now end at a moment nobody planned
for. `champions/agents/commands.py` is how the viewer asks for that, and
`TracingPlayer.choose_move`'s finished check is what stops the agent from
answering a request for a battle that is already over.
"""

from __future__ import annotations

import asyncio
import io
import threading
import time
from typing import Any

from poke_env.player.battle_order import _EmptyBattleOrder

from champions.agents import commands


class _FakeClient:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop


class _FakePlayer:
    """Just enough of a player for the channel: a loop and something to concede."""

    def __init__(self, loop: asyncio.AbstractEventLoop, live: list[str]) -> None:
        self.ps_client = _FakeClient(loop)
        self.live = live
        self.calls = 0

    async def forfeit_active(self) -> list[str]:
        self.calls += 1
        conceded, self.live = self.live, []
        return conceded


def _loop_on_a_thread() -> tuple[asyncio.AbstractEventLoop, threading.Thread]:
    """poke-env runs its client on a loop on another thread, and the channel's
    whole job is to hand work across that boundary safely. Reproduce it."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    return loop, thread


def test_forfeit_is_run_on_the_clients_loop_not_the_reader_thread() -> None:
    loop, _ = _loop_on_a_thread()
    try:
        player = _FakePlayer(loop, ["battle-x-1"])
        channel = commands.CommandChannel([player])  # type: ignore[list-item]

        channel.handle("forfeit")

        assert player.calls == 1
        assert channel.forfeited == ["battle-x-1"]
    finally:
        loop.call_soon_threadsafe(loop.stop)


def test_it_concedes_once_even_though_self_play_holds_both_sides() -> None:
    """Both players live in one process and share the battle. Conceding on one
    side ends it for both, so a second forfeit would be a race against the
    battle already being over."""
    loop, _ = _loop_on_a_thread()
    try:
        first = _FakePlayer(loop, ["battle-x-1"])
        second = _FakePlayer(loop, ["battle-x-1"])
        channel = commands.CommandChannel([first, second])  # type: ignore[list-item]

        channel.handle("forfeit")

        assert first.calls == 1
        assert second.calls == 0
    finally:
        loop.call_soon_threadsafe(loop.stop)


def test_nothing_to_concede_is_reported_rather_than_raised() -> None:
    """The run may be between games or waiting for a challenge. The button is
    harmless there and must stay harmless."""
    loop, _ = _loop_on_a_thread()
    try:
        player = _FakePlayer(loop, [])
        channel = commands.CommandChannel([player])  # type: ignore[list-item]

        channel.handle("forfeit")

        assert channel.forfeited == []
    finally:
        loop.call_soon_threadsafe(loop.stop)


def test_an_unknown_command_does_not_take_the_run_down() -> None:
    loop, _ = _loop_on_a_thread()
    try:
        player = _FakePlayer(loop, ["battle-x-1"])
        channel = commands.CommandChannel([player])  # type: ignore[list-item]

        channel.handle("selfdestruct")

        assert player.calls == 0
    finally:
        loop.call_soon_threadsafe(loop.stop)


def test_the_reader_turns_a_line_of_stdin_into_a_command() -> None:
    """The supervisor writes a line down a pipe; nothing else checks that the
    word it writes is the word this reads."""
    loop, _ = _loop_on_a_thread()
    try:
        player = _FakePlayer(loop, ["battle-x-1"])
        channel = commands.listen([player], stream=io.StringIO("\nFORFEIT\n"))  # type: ignore[list-item]

        deadline = time.time() + 5
        while time.time() < deadline and not channel.forfeited:
            time.sleep(0.02)

        assert channel.forfeited == ["battle-x-1"]
    finally:
        loop.call_soon_threadsafe(loop.stop)


# -- the other half: not deciding a battle that is already over ---------------


class _FinishedBattle:
    finished = True
    battle_tag = "battle-x-1"


async def test_a_finished_battle_is_not_decided_and_emits_nothing() -> None:
    """Showdown can hand out a request and then end the battle underneath it.
    Deciding it appended a whole turn of events after `battle_end` -- which our
    own validator rejects -- and sent a `/choose` into a room we had left.

    An empty order is the one poke-env declines to send at all, which is why it
    is the right answer rather than a default move.
    """
    from champions.agents.baseline import TracingPlayer

    emitted: list[Any] = []

    class _Spy(TracingPlayer):
        def trace_for(self, battle: Any) -> Any:  # pragma: no cover - must not run
            emitted.append(battle)
            raise AssertionError("a finished battle must not reach the trace")

    order = await TracingPlayer.choose_move(
        _Spy.__new__(_Spy),  # no transport needed: the guard is the first statement
        _FinishedBattle(),  # type: ignore[arg-type]
    )

    assert isinstance(order, _EmptyBattleOrder)
    assert order.message == ""
    assert emitted == []
