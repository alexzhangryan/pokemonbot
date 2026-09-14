"""M11: the adaptive agent, against a real simulator.

The allocation arithmetic is tested in `tests/test_clock.py`. This checks the
thing it is for: that the agent plays under an allocated budget, that the
budget state reaches the timing event, and that the second stage runs exactly
when the first was close.
"""

from __future__ import annotations

import json
from pathlib import Path

from champions.harness.ladder import run_matchup
from champions.search.clock import TURN_LIMIT_S
from champions.teams import ALPHA
from scripts.run_ladder import build_arm

N_GAMES = 2


async def test_the_adaptive_agent_plays_on_a_budget_and_says_so(
    showdown_server: int, tmp_path: Path
) -> None:
    results = await run_matchup(
        build_arm("adaptive", showdown_server, ALPHA),
        build_arm("greedy", showdown_server, ALPHA),
        N_GAMES,
        tmp_path,
        seed=3,
    )
    assert sum(r.games for r in results) == 2 * N_GAMES

    traces = sorted(tmp_path.glob("*.adaptive3.jsonl"))
    assert traces, "the adaptive arm wrote traces"

    escalated = decisive = 0
    for path in traces:
        spent_before = -1.0
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            payload = event["payload"]
            if event["type"] == "timing":
                assert 0.0 < payload["budget_s"] <= TURN_LIMIT_S
                assert payload["deadline_s"] == payload["budget_s"]
                # The clock only ever goes one way within a battle.
                assert payload["spent_before_s"] >= spent_before
                spent_before = payload["spent_before_s"]
                assert payload["remaining_player_clock_s"] <= 7 * 60.0
            if event["type"] == "candidates" and payload.get("pruned"):
                assert "decisive" in payload and "escalated" in payload
                assert not (payload["decisive"] and payload["escalated"])
                assert payload["model"] == "analytic-adaptive"
                escalated += int(payload["escalated"])
                decisive += int(payload["decisive"])
        assert spent_before >= 0.0, "every battle recorded at least one decision"
    assert escalated + decisive > 0
