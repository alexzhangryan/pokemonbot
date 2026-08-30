"""Baseline agents. Nothing here is intelligent; these exist so that the
transport, the trace, the watchdog, and the harness are exercised end to end
from M0, and so later agents have a frozen opponent pool to be measured against.

The decision pipeline itself lives in `TracingPlayer.choose_move`, which is
where every event on the trace is emitted. A subclass supplies only `_search`
and a strategy name. That split is the reason the viewer can render an agent it
has never heard of: the emission is a property of the base class, so a new agent
gets the full observability surface without opting into it.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import random
import time
from collections.abc import Callable
from functools import cache
from typing import Any

from poke_env.battle import AbstractBattle, DoubleBattle
from poke_env.concurrency import handle_threaded_coroutines
from poke_env.player import Player
from poke_env.player.battle_order import (
    BattleOrder,
    DefaultBattleOrder,
    DoubleBattleOrder,
    _EmptyBattleOrder,
)

from champions.belief.filter import BattleBelief
from champions.belief.priors import PriorNotBuiltError, SetPrior
from champions.dex.loader import Dex, DexNotBuiltError
from champions.protocol import actions, parser, state
from champions.search.evaluate import evaluate
from champions.search.watchdog import AnytimeDecision, decide_with_deadline
from champions.trace.schema import EventType
from champions.trace.writer import Trace

DEFAULT_DECISION_DEADLINE_S = 45.0

# The joint action space is about 156 actions per side with the Mega flag
# available, and each described candidate is a few hundred bytes. Emitting all
# of them is affordable now and is what makes the viewer's candidate table
# complete; the cap exists so that an unexpectedly large space degrades the
# trace rather than the battle. Once M2 prunes, this list is the pruned set and
# the cap stops mattering.
MAX_TRACED_CANDIDATES = 300


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

    #: Recorded on every equilibrium event so a trace identifies the policy that
    #: produced it. Subclasses override.
    strategy = "abstract"

    def __init__(
        self,
        *args: Any,
        trace_dir: str = "traces",
        decision_deadline_s: float = DEFAULT_DECISION_DEADLINE_S,
        seed: int | None = None,
        dex: Dex | None = None,
        prior: SetPrior | None = None,
        belief: bool = True,
        on_battle_end: Callable[[AbstractBattle], None] | None = None,
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
        self._log_buffer: dict[str, list[str]] = {}
        # One parser state per battle, carried across turns because a
        # nickname bound on turn 1 has to still resolve on turn 20.
        self._parsers: dict[str, parser.ParserState] = {}
        self._dex = dex if dex is not None else _load_dex_if_built(self.format)

        # The belief filter is on by default and lives in the base class, for
        # the same reason the rest of the emission does: a new agent should get
        # the whole observability surface without opting into it, and the
        # `belief` panel is the most useful debugging surface in the system once
        # it exists (`docs/07-observability.md`). It costs a few milliseconds a
        # turn against a 45 second budget.
        #
        # It needs both the dex and a built prior. Missing either is a normal
        # state -- a fresh checkout has no corpus -- so it degrades to no belief
        # rather than refusing to play, and `battle_start` records which.
        self._prior = prior if prior is not None else (_load_prior_if_built() if belief else None)
        self._beliefs: dict[str, BattleBelief] = {}
        self._belief_enabled = bool(belief and self._dex is not None and self._prior is not None)
        # Lets a caller report progress as a run proceeds. poke-env only returns
        # once every battle is done, so without this a long run is silent until
        # it ends -- which is exactly when progress stops being useful.
        self._on_battle_end = on_battle_end

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

    # -- conceding -------------------------------------------------------

    async def forfeit_active(self) -> list[str]:
        """Concede every battle currently in progress, and report which.

        This is the only thing in the agent that can end a battle without
        playing it out, and it exists for one situation: a game has gone long or
        gone wrong and what you want is the *next* game, not the death of the
        run. Killing the process was the previous answer and it is the wrong
        granularity -- it takes the remaining games with it.

        `/forfeit` is a room command rather than a move, so it goes out through
        the client directly instead of through `choose_move`. Nothing about the
        decision pipeline is involved and no trace event claims a decision was
        made: the battle simply ends, and `_battle_finished_callback` emits the
        `battle_end` it would have emitted for any other loss.
        """
        tags = [tag for tag, battle in self._battles.items() if not battle.finished]
        for tag in tags:
            await self.ps_client.send_message("/forfeit", tag)
        return tags

    # -- protocol log ----------------------------------------------------

    async def _handle_battle_message(self, split_messages: list[list[str]]) -> None:
        """Record the raw protocol before poke-env consumes it.

        This is what actually happened, in the server's own words and censored
        to our side of the field, and no other source has it: poke-env folds
        each message into its battle state and keeps no log. Without it a trace
        shows the agent's decisions with no account of what they led to, which
        is the first question anyone watching asks.

        Capture must precede `super()`, because the superclass dispatches the
        request that calls `choose_move`, and by then this turn's messages need
        to already be in the buffer.
        """
        room = split_messages[0][0] if split_messages and split_messages[0] else ""
        if room.startswith(">battle-"):
            buffer = self._log_buffer.setdefault(room[1:], [])
            for split_message in split_messages[1:]:
                if len(split_message) > 1 and split_message[1] not in _LOG_NOISE:
                    buffer.append("|".join(split_message))
                elif len(split_message) == 1 and not split_message[0].startswith(">"):
                    # A bare "|" separates message groups; the renderer uses it.
                    buffer.append("|")
        await super()._handle_battle_message(split_messages)

    def _take_log(self, battle: AbstractBattle) -> list[str]:
        """The protocol lines seen since the last time this was called."""
        return self._log_buffer.pop(battle.battle_tag, [])

    def _observe(self, battle: AbstractBattle, lines: list[str]) -> list[parser.Observation]:
        """Fold new protocol lines into this battle's parser state.

        Unrecognised message types are counted rather than dropped, and the
        count rides along on `battle_end`, so protocol drift shows up as a
        number instead of as quietly missing evidence.
        """
        state_for_battle = self._parsers.setdefault(battle.battle_tag, parser.ParserState())
        observations: list[parser.Observation] = []
        for line in lines:
            observations.extend(parser.apply(state_for_battle, line))
        return observations

    # -- belief ----------------------------------------------------------

    def belief_for(self, battle: AbstractBattle) -> BattleBelief | None:
        """This battle's belief, created on first use. None when unavailable.

        Created lazily rather than at construction because it is seeded from the
        opponent's previewed six, which do not exist until the battle does. The
        seed is derived per battle so a replayed battle rebuilds the same
        particles -- the same reason `OnePlyAgent` derives its equilibrium draw
        per decision rather than from a shared generator (D31).
        """
        if not self._belief_enabled or self._dex is None or self._prior is None:
            return None
        tag = battle.battle_tag
        if tag not in self._beliefs:
            preview = [p.species for p in battle.teampreview_opponent_team]
            if not preview:
                preview = [p.species for p in battle.opponent_team.values()]
            if not preview:
                return None
            self._beliefs[tag] = BattleBelief(
                dex=self._dex,
                prior=self._prior,
                opponent_species=preview,
                player_role=battle.player_role or "p1",
                seed=_battle_seed(self._seed, tag),
            )
        return self._beliefs[tag]

    # -- events ----------------------------------------------------------

    def _emit_battle_start_once(self, battle: AbstractBattle) -> None:
        if battle.battle_tag in self._started:
            return
        self._started.add(battle.battle_tag)
        self.trace_for(battle).emit(
            EventType.BATTLE_START,
            {
                "format_id": self.format,
                "player_role": battle.player_role,
                "player_username": battle.player_username,
                "opponent_username": battle.opponent_username,
                "agent": type(self).__name__,
                "strategy": self.strategy,
                "our_team": sorted(p.species for p in battle.team.values()),
                # At preview Champions reveals the opponent's six species and
                # nothing else, which is exactly what this list is.
                "opponent_team_preview": sorted(
                    p.species for p in battle.teampreview_opponent_team
                ),
                "seed": self._seed,
                "accept_open_team_sheet": False,
                "dex_hash": self._dex.content_hash if self._dex else None,
                "belief": self._belief_enabled,
            },
        )

    def teampreview(self, battle: AbstractBattle) -> str:
        self._emit_battle_start_once(battle)

        order = self.random_teampreview(battle)

        self.trace_for(battle).emit(
            EventType.PREVIEW_DECISION,
            {
                "order": order,
                "selected": [p.species for p in battle.team.values() if p._selected_in_teampreview],
                "policy": "uniform_random",
                # The bring-4 equilibrium is M7. The viewer renders the pseudo
                # turn before turn 1 either way, marked unanalyzed.
                "pending": ["subset_distribution", "payoff_matrix", "equilibrium_weights"],
            },
        )
        return order

    async def choose_move(self, battle: AbstractBattle) -> BattleOrder:
        """The decision pipeline, and the only place trace events are emitted.

        Order matters: state before candidates before the decision, so that a
        reader replaying the file sees what the agent saw before it sees what
        the agent did with it.

        A finished battle is decided *before* anything is emitted. Showdown can
        hand out a request and then end the battle underneath it -- the other
        side concedes, an inactivity timer fires, or we concede ourselves over
        the control channel -- and poke-env dispatches the request it already
        had. Deciding it produced two visible failures: a full turn of events
        appended after `battle_end`, which makes the trace invalid by our own
        validator and shows the viewer a turn that never happened, and a
        `/choose` into a room we had already left, which Showdown answers with a
        popup. An empty order is the one poke-env does not send at all.
        """
        if battle.finished:
            return _EmptyBattleOrder()

        self._emit_battle_start_once(battle)
        trace = self.trace_for(battle)
        log_lines = self._take_log(battle)

        # What happened since we last chose, as observations rather than as
        # protocol. `docs/07-observability.md` section 2 specifies `turn_result`
        # and left open whether it should be a parsed digest of the log; it
        # should, and this is it. The same parser feeds the replay corpus (D32),
        # so the live agent and the offline corpus cannot drift apart.
        observations = self._observe(battle, log_lines)
        if observations:
            trace.emit(
                EventType.TURN_RESULT,
                {
                    "turn": battle.turn,
                    "observations": [o.as_row() for o in observations],
                },
            )

        snapshot = state.snapshot(battle, self._dex)
        # The eval bar `docs/04-decision-engine.md` section 5 describes, emitted
        # by the base class so every agent has one. It has to come from here
        # rather than from the viewer: the viewer renders traces and does not
        # import the agent, and an evaluation recomputed at display time would
        # be a different function from the one the search actually used.
        # `calibrated` travels with the number, so a trace written before M6 was
        # fit still says that its numbers are not probabilities.
        position = evaluate(snapshot)
        trace.emit(
            EventType.TURN_START,
            {
                "turn": battle.turn,
                "state": snapshot,
                "log": log_lines,
                "evaluation": {
                    "win_prob": position.win_prob,
                    "log_odds": None if math.isinf(position.log_odds) else position.log_odds,
                    "calibrated": position.calibrated,
                    "features": position.features,
                },
            },
        )

        # The belief update runs before the search, because the search reads it.
        # It consumes the same observations `turn_result` was built from rather
        # than re-reading the log, which is what stops the live filter and the
        # offline corpus from drifting apart (D32).
        belief = self.belief_for(battle)
        if belief is not None:
            started = time.perf_counter()
            belief.add_species(p.species for p in battle.opponent_team.values())
            belief.update(observations, snapshot)
            trace.emit(
                EventType.BELIEF,
                {
                    "turn": battle.turn,
                    "elapsed_s": round(time.perf_counter() - started, 4),
                    **belief.summary(),
                },
            )

        orders = self._legal_orders(battle)
        fallback: BattleOrder = orders[0] if orders else DefaultBattleOrder()

        trace.emit(
            EventType.CANDIDATES,
            {
                "turn": battle.turn,
                "n_legal_joint_actions": len(orders),
                "joint": [actions.describe(o, self._dex) for o in orders[:MAX_TRACED_CANDIDATES]],
                "truncated": len(orders) > MAX_TRACED_CANDIDATES,
                # The per-slot legal sets, which are what a person reads: the
                # joint list is their product and is an order of magnitude
                # longer for the same information.
                "slot_options": self._slot_options(battle),
                "pruned": False,
                "annotations_pending": list(actions.PENDING_ANNOTATIONS),
            },
        )

        async def search(decision: AnytimeDecision[BattleOrder]) -> None:
            await self._search(battle, orders, decision)

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
                "chosen_action": actions.describe(result.action, self._dex),
                "n_legal_joint_actions": len(orders),
                "strategy": self.strategy,
                "value": result.value,
                "seed": self._seed,
                "watchdog_fired": result.watchdog_fired,
                "proposals": result.proposals,
                # A real mixed strategy arrives with the equilibrium solver at
                # M5. Naming what is missing beats emitting a degenerate vector
                # that a reader would mistake for a solved one.
                "pending": ["mixed_strategy", "game_value"],
            },
        )
        return result.action

    async def _search(
        self,
        battle: AbstractBattle,
        orders: list[BattleOrder],
        decision: AnytimeDecision[BattleOrder],
    ) -> None:
        """Propose improving actions until done or cancelled. Subclasses implement."""
        raise NotImplementedError

    def _legal_orders(self, battle: AbstractBattle) -> list[BattleOrder]:
        if isinstance(battle, DoubleBattle):
            return list(DoubleBattleOrder.join_orders(*battle.valid_orders))
        return list(battle.valid_orders)

    def _slot_options(self, battle: AbstractBattle) -> list[list[dict[str, Any]]]:
        if isinstance(battle, DoubleBattle):
            per_slot = list(battle.valid_orders)
        else:
            per_slot = [battle.valid_orders]
        return [[actions.describe_slot(o, self._dex) for o in slot] for slot in per_slot]

    # -- lifecycle -------------------------------------------------------

    def _battle_finished_callback(self, battle: AbstractBattle) -> None:
        if self._on_battle_end is not None:
            self._on_battle_end(battle)
        final_log = self._take_log(battle)
        observations = self._observe(battle, final_log)
        parser_state = self._parsers.pop(battle.battle_tag, None)
        belief = self._beliefs.pop(battle.battle_tag, None)
        self.trace_for(battle).emit(
            EventType.BATTLE_END,
            {
                "result": "win" if battle.won else ("tie" if battle.won is None else "loss"),
                "turns": battle.turn,
                "rating": battle.rating,
                "state": state.snapshot(battle, self._dex),
                "log": final_log,
                "final_observations": [o.as_row() for o in observations],
                # Zero unless Showdown emitted protocol this parser does not
                # read. Non-zero is a signal to look, not a failure (D32).
                "unhandled_messages": dict(parser_state.unhandled) if parser_state else {},
                # The belief as it stood at the end. On a forced-open-sheet
                # replay the truth is knowable, which makes this the row an
                # offline accuracy measurement joins against.
                "belief": belief.summary() if belief is not None else None,
            },
        )


class RandomAgent(TracingPlayer):
    """Uniform random over legal joint actions. The floor of the opponent pool."""

    strategy = "uniform_random"

    async def _search(
        self,
        battle: AbstractBattle,
        orders: list[BattleOrder],
        decision: AnytimeDecision[BattleOrder],
    ) -> None:
        # A uniform mixed strategy over the legal joint actions. For a random
        # agent that is the whole policy; for later agents this is where the
        # equilibrium solve goes.
        if orders:
            decision.propose(self._rng.choice(orders))


class MaxBasePowerAgent(TracingPlayer):
    """Greedy on base power, summed across both slots. Switching scores zero.

    Named for what it does. This is not a damage maximizer: damage depends on
    stats, types, items, abilities, and spread reduction, and the Champions
    damage layer is M1 work that the M0 task list explicitly defers. Base power
    is a crude proxy that makes a stronger-than-random opponent available now.

    It does read base power from the Champions dex rather than from poke-env,
    whose mainline Gen 9 numbers are wrong for this format (T0.3 found 303
    modified moves, base power among the changed fields).
    """

    strategy = "argmax_base_power"

    def __init__(self, *args: Any, dex: Dex | None = None, **kwargs: Any) -> None:
        super().__init__(*args, dex=dex, **kwargs)
        if self._dex is None:
            raise DexNotBuiltError(
                f"{type(self).__name__} reads base power from the Champions dex, which is "
                f"not built for {self.format!r}. Build it with:\n"
                f"    python scripts/build_dex.py {self.format}"
            )

    def _score(self, order: BattleOrder) -> float:
        """Summed base power, disqualifying orders that attack our own side.

        In doubles, negative move targets are our own slots (-1, -2) and
        positive ones are the opponent's. Scoring base power alone makes an
        ally-targeted attack tie with the same move aimed at a foe, and the
        first such order wins the argmax, so the agent spends the game hitting
        its own partner. Disqualify rather than merely penalize: there is no
        base-power reason to ever aim a damaging move at our own side.
        """
        assert self._dex is not None  # guaranteed by __init__
        total = 0.0
        for single in actions.single_orders(order):
            move = getattr(single, "order", None)
            move_id = getattr(move, "id", None)
            if move_id is None:
                continue
            power = self._dex.base_power(move_id)
            if power > 0 and getattr(single, "move_target", 0) < 0:
                return float("-inf")
            total += power
        return total

    async def _search(
        self,
        battle: AbstractBattle,
        orders: list[BattleOrder],
        decision: AnytimeDecision[BattleOrder],
    ) -> None:
        best_score = float("-inf")
        for order in orders:
            score = self._score(order)
            if score > best_score:
                best_score = score
                decision.propose(order, value=score)
            # Yield so the watchdog can interrupt a long enumeration.
            await asyncio.sleep(0)


@cache
@cache
def _load_prior_if_built() -> SetPrior | None:
    """The set prior, or None when the corpus has not been distilled into one.

    Cached for the same reason the dex is: it is a ~1 MB JSON parse, and a
    ladder run constructs several agents. The SetPrior is read-only after
    construction, so one instance is safe to share across battles and across
    agents -- and sharing it is also what makes the two arms of a head-to-head
    provably run against the same prior rather than two loads of the same file.
    """
    try:
        return SetPrior.load()
    except (PriorNotBuiltError, ValueError):
        return None


def _load_dex_if_built(format_id: str) -> Dex | None:
    """The dex is gitignored and built locally, so its absence is not fatal here.

    An agent that needs it says so in its own constructor. Everything else
    degrades to poke-env's mainline numbers, which the trace labels as such
    rather than passing off as Champions values.

    Cached because the dump is a multi-megabyte JSON parse and every agent in a
    self-play or ladder run would otherwise repeat it. The Dex is read-only.
    """
    try:
        return Dex.load(format_id)
    except (DexNotBuiltError, ValueError):
        return None


# Protocol lines that carry nothing about the battle. Everything else is kept
# verbatim, including the blank separators and `|upkeep|`, because the log is
# not only for reading: Showdown's own battle renderer replays it, and it
# segments turns using lines a summary would throw away. `|request|` is the one
# substantive omission -- it is our own private input rather than an account of
# the battle, and it is by far the largest thing on the wire.
_LOG_NOISE = frozenset(
    {
        "request",
        "inactive",
        "inactiveoff",
        "j",
        "J",
        "join",
        "l",
        "L",
        "leave",
        "c",
        "C",
        "c:",
        "chat",
        "raw",
        "html",
        "uhtml",
        "uhtmlchange",
        "message",
        "expire",
        "askreg",
        "unlink",
        "notify",
    }
)


def _battle_seed(seed: int | None, battle_tag: str) -> int:
    """A per battle seed derived from the run seed and the battle id.

    hashlib rather than the builtin `hash()`: Python randomises string hashing
    per process, so the builtin would give the same battle different particles
    on every rerun -- the same irreproducibility D31 records for the equilibrium
    draw.
    """
    key = f"{seed}:{battle_tag}".encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big")
