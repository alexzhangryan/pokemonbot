"""Baseline agents. Nothing here is intelligent; these exist so that the
transport, the trace, the watchdog, and the harness are exercised end to end
from M0, and so later agents have a frozen opponent pool to be measured against.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from poke_env.battle import AbstractBattle, DoubleBattle
from poke_env.concurrency import handle_threaded_coroutines
from poke_env.player import Player
from poke_env.player.battle_order import BattleOrder, DefaultBattleOrder, DoubleBattleOrder

from champions.search.watchdog import AnytimeDecision, decide_with_deadline
from champions.trace.schema import EventType
from champions.trace.writer import Trace

DEFAULT_DECISION_DEADLINE_S = 45.0


class TracingPlayer(Player):
    """A poke-env Player that writes a decision trace, one file per battle.

    Two things here are policy rather than convenience:

    Open Team Sheets is always declined. Champions has no such mechanism, so an
    agent trained or evaluated with that information does not transfer to the
    target game (DECISIONS.md D2). poke-env sends `/rejectopenteamsheets` for
    VGC formats when `accept_open_team_sheet` is False; we pass it explicitly
    rather than relying on the default, and refuse to be constructed with True.

    Every decision goes through the deadline watchdog, even though a random
    choice is instant. VGC Timer auto-loses inactive players, so the anytime
    structure is wired in from the first game rather than retrofitted (D7).
    """

    def __init__(
        self,
        *args: Any,
        trace_dir: str = "traces",
        decision_deadline_s: float = DEFAULT_DECISION_DEADLINE_S,
        seed: int | None = None,
        **kwargs: Any,
    ) -> None:
        if kwargs.get("accept_open_team_sheet"):
            raise ValueError(
                "Open Team Sheets must be declined: Champions has no such mechanism, "
                "so accepting produces an agent that does not transfer (DECISIONS.md D2)."
            )
        kwargs["accept_open_team_sheet"] = False
        super().__init__(*args, **kwargs)

        self._trace_dir = trace_dir
        self._decision_deadline_s = decision_deadline_s
        self._seed = seed
        self._rng = random.Random(seed)
        self._traces: dict[str, Trace] = {}
        self._started: set[str] = set()

    # -- tracing ---------------------------------------------------------

    def trace_for(self, battle: AbstractBattle) -> Trace:
        tag = battle.battle_tag
        if tag not in self._traces:
            # The file is per agent-view: in self-play both players share a
            # battle_tag, and a shared file would interleave two sides' events
            # under two independent seq counters.
            self._traces[tag] = Trace(tag, trace_dir=self._trace_dir, name=f"{tag}.{self.username}")
        return self._traces[tag]

    def trace_path(self, battle_tag: str) -> Any:
        return self._traces[battle_tag].path

    async def close_traces(self) -> None:
        """Flush and close every trace this player opened.

        The drain tasks were created inside poke-env's POKE_LOOP, which runs on
        its own thread, so they can only be awaited from that loop. Callers
        usually live on the main loop, hence the bridge.
        """
        loop = self.ps_client.loop
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None

        if current is loop:
            await self._close_traces()
        else:
            await handle_threaded_coroutines(self._close_traces(), loop)

    async def _close_traces(self) -> None:
        for trace in self._traces.values():
            await trace.close()

    def _emit_battle_start_once(self, battle: AbstractBattle) -> None:
        if battle.battle_tag in self._started:
            return
        self._started.add(battle.battle_tag)
        self.trace_for(battle).emit(
            EventType.BATTLE_START,
            {
                "format_id": self.format,
                "player_role": battle.player_role,
                "our_team": sorted(p.species for p in battle.team.values()),
                # At preview Champions reveals the opponent's six species and
                # nothing else, which is exactly what this list is.
                "opponent_team_preview": sorted(
                    p.species for p in battle.teampreview_opponent_team
                ),
                "seed": self._seed,
                "accept_open_team_sheet": False,
            },
        )

    # -- decisions -------------------------------------------------------

    def teampreview(self, battle: AbstractBattle) -> str:
        self._emit_battle_start_once(battle)

        order = self.random_teampreview(battle)

        self.trace_for(battle).emit(
            EventType.PREVIEW_DECISION,
            {
                "order": order,
                "selected": [p.species for p in battle.team.values() if p._selected_in_teampreview],
                "policy": "uniform_random",
            },
        )
        return order

    async def choose_move(self, battle: AbstractBattle) -> BattleOrder:
        self._emit_battle_start_once(battle)
        trace = self.trace_for(battle)

        trace.emit(
            EventType.TURN_START,
            {
                "turn": battle.turn,
                "active": [p.species if p else None for p in _active_of(battle)],
                "opponent_active": [p.species if p else None for p in _opponent_active_of(battle)],
                "our_remaining": sum(1 for p in battle.team.values() if not p.fainted),
                "opponent_remaining": sum(
                    1 for p in battle.opponent_team.values() if not p.fainted
                ),
            },
        )

        orders = self._legal_orders(battle)
        fallback: BattleOrder = orders[0] if orders else DefaultBattleOrder()

        async def search(decision: AnytimeDecision[BattleOrder]) -> None:
            # A uniform mixed strategy over the legal joint actions. For a random
            # agent that is the whole policy; for later agents this is where the
            # equilibrium solve goes.
            if orders:
                decision.propose(self._rng.choice(orders))

        result = await decide_with_deadline(
            search,
            fallback=fallback,
            deadline_s=self._decision_deadline_s,
            trace=trace,
            trace_payload={"turn": battle.turn, "phase": "choose_move"},
        )

        trace.emit(
            EventType.EQUILIBRIUM,
            {
                "turn": battle.turn,
                "chosen": result.action.message,
                "n_legal_joint_actions": len(orders),
                # Uniform, so recording the support size rather than a flat vector
                # of len(orders) identical floats.
                "strategy": "uniform",
                "seed": self._seed,
                "watchdog_fired": result.watchdog_fired,
            },
        )
        return result.action

    def _legal_orders(self, battle: AbstractBattle) -> list[BattleOrder]:
        if isinstance(battle, DoubleBattle):
            return list(DoubleBattleOrder.join_orders(*battle.valid_orders))
        return list(battle.valid_orders)

    # -- lifecycle -------------------------------------------------------

    def _battle_finished_callback(self, battle: AbstractBattle) -> None:
        self.trace_for(battle).emit(
            EventType.BATTLE_END,
            {
                "result": "win" if battle.won else ("tie" if battle.won is None else "loss"),
                "turns": battle.turn,
                "rating": battle.rating,
            },
        )


class RandomAgent(TracingPlayer):
    """Uniform random over legal joint actions. The floor of the opponent pool."""


def _active_of(battle: AbstractBattle) -> list[Any]:
    if isinstance(battle, DoubleBattle):
        return list(battle.active_pokemon)
    return [battle.active_pokemon]


def _opponent_active_of(battle: AbstractBattle) -> list[Any]:
    if isinstance(battle, DoubleBattle):
        return list(battle.opponent_active_pokemon)
    return [battle.opponent_active_pokemon]
