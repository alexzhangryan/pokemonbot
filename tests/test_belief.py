"""M5: the belief filter.

The tests are organised around the things that can silently go wrong rather
than around the module list, because `docs/03-belief-filter.md` and `CLAUDE.md`
constraint 5 agree on what the dangerous failure is: a filter that eliminates
the truth looks exactly like a filter that is learning fast.

Four of these exist because the code got it wrong first, and each was invisible
in the output and obvious in the measurement:

- The damage inference read the *base* forme's base stats for a Mega Evolved
  Pokemon. Greninja-Mega has 142 base Speed against Greninja's 122, so every
  bound drawn about it was wrong in the same direction.
- It read the species' static types for a Protean user. Blizzard from an
  Ice-typed Greninja got no STAB and its resisted return hit got none of the
  0.5x, so the filter concluded the opponent was both weak and bulky and pinned
  four stats to the wrong ends of their ranges.
- Every unrecognised ability counted as a possible damage multiplier, so almost
  every particle carried the wide tolerance and the spread layer narrowed no
  interval across a whole battle. Intimidate, Competitive and Snow Warning are
  all announced by the protocol; treating them as unknown multipliers threw
  that away.
- A Mega Evolution's ability, attributed to the base species, made every
  particle inconsistent at once -- Gengar-Mega is Shadow Tag whatever Gengar
  was registered with.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from champions.belief import effects as effect_table
from champions.belief.evaluate import (
    BeliefScore,
    calibration_table,
    score_summary,
    truth_from_team_file,
    wilson,
)
from champions.belief.evidence import (
    DamageEvidence,
    EvidenceBuilder,
    Reveal,
    SpeedEvidence,
    parse_hp,
)
from champions.belief.filter import BattleBelief
from champions.belief.hypothesis import BeliefEffects, BeliefHypothesis
from champions.belief.particles import ParticleFilter, TeamConstraints
from champions.belief.priors import SetHypothesis, SetPrior
from champions.belief.spreads import SpreadBelief, joint_restrict
from champions.dex.loader import REPO_ROOT, Dex
from champions.dex.stats import MAX_POINTS_PER_STAT, MAX_POINTS_TOTAL, STAT_IDS
from champions.protocol import parser
from champions.search.payoff import OpponentHypothesis
from champions.search.policy import opponent_candidates

FORMAT_ID = "gen9championsvgc2026regmb"
VENDOR = REPO_ROOT / "vendor" / "showdown" / "data"


@pytest.fixture(scope="module")
def dex() -> Dex:
    return Dex.load(FORMAT_ID)


@pytest.fixture(scope="module")
def prior() -> SetPrior:
    """A small hand-built prior, so the tests do not depend on the live corpus.

    The corpus grows while the scraper runs, and a test whose expected values
    move with it is a test that will be deleted the first time it fails for a
    reason nobody caused.
    """
    rows = [
        {
            "replay_id": f"r{i}",
            "side": "p1",
            "species": species,
            "item": item,
            "ability": ability,
            "moves": moves,
            "nature": nature,
        }
        for i in range(20)
        for species, item, ability, moves, nature in (
            (
                "Metagross",
                "Metagrossite",
                "Clear Body",
                "ironhead,psychicfangs,protect,icepunch",
                "jolly",
            ),
            (
                "Incineroar",
                "Sitrus Berry",
                "Intimidate",
                "fakeout,partingshot,flareblitz,knockoff",
                "careful",
            ),
            (
                "Garchomp",
                "Life Orb",
                "Rough Skin",
                "earthquake,dragonclaw,rockslide,protect",
                "adamant",
            ),
            ("Milotic", "Leftovers", "Competitive", "scald,icywind,recover,protect", "calm"),
            (
                "Gengar",
                "Focus Sash",
                "Cursed Body",
                "sludgebomb,shadowball,protect,destinybond",
                "timid",
            ),
            (
                "Vanilluxe",
                "Choice Scarf",
                "Snow Warning",
                "blizzard,freezedry,icywind,auroraveil",
                "timid",
            ),
            ("Greninja", "Greninjite", "Protean", "blizzard,darkpulse,protect,lowkick", "timid"),
        )
    ]
    # One player runs a different Metagross, so the empirical distribution is
    # not degenerate and `probability` has two outcomes to weigh.
    rows += [
        {
            "replay_id": f"s{i}",
            "side": "p2",
            "species": "Metagross",
            "item": "Life Orb",
            "ability": "Clear Body",
            "moves": "ironhead,bulletpunch,protect,stompingtantrum",
            "nature": "adamant",
        }
        for i in range(5)
    ]
    return SetPrior.from_rows(rows)


TEAM = ["metagross", "incineroar", "garchomp", "milotic", "gengar", "vanilluxe"]

#: The team the protocol fixture below actually plays. Greninja rather than
#: Metagross, because Protean is the type-change case worth exercising.
BATTLE_TEAM = ["greninja", "incineroar", "garchomp", "milotic", "gengar", "vanilluxe"]


def _filter(dex: Dex, prior: SetPrior, n: int = 32, seed: int = 7) -> ParticleFilter:
    return ParticleFilter(dex, prior, TEAM, n_particles=n, rng=np.random.default_rng(seed))


# ------------------------------------------------------------- spreads


def test_resource_constraint_is_the_closed_form(dex: Dex) -> None:
    """`u_i <- min(u_i, 32, 66 - sum_{j != i} l_j)`, exactly."""
    belief = SpreadBelief.unconstrained(dex.species["metagross"]["baseStats"], "hardy", {})
    assert belief.upper["atk"] == MAX_POINTS_PER_STAT

    belief.constrain_points("hp", low=30)
    belief.constrain_points("def", low=30)
    # 66 - (30 + 30) leaves 6 for anything else, and that is a cap on all four.
    assert belief.upper["atk"] == MAX_POINTS_TOTAL - 60
    assert belief.upper["spe"] == MAX_POINTS_TOTAL - 60
    # And it does not touch the two that are already spending.
    assert belief.upper["hp"] >= belief.lower["hp"]


def test_over_budget_is_infeasible(dex: Dex) -> None:
    belief = SpreadBelief.unconstrained(dex.species["metagross"]["baseStats"], "hardy", {})
    for stat in ("hp", "atk", "def"):
        belief.constrain_points(stat, low=32)
    assert not belief.feasible


def test_stat_observations_invert_the_real_formula(dex: Dex) -> None:
    """A bound on a derived stat becomes the right bound on points.

    Checked against `compute_stat` itself rather than against arithmetic
    rederived here, which is the whole reason the inversion is a scan.
    """
    nature = dex.nature("jolly")
    belief = SpreadBelief.unconstrained(dex.species["metagross"]["baseStats"], "jolly", nature)
    target = belief.stat_at("spe", 20)

    belief.observe_stat_at_least("spe", target)
    assert belief.stat_at("spe", belief.lower["spe"]) >= target
    assert belief.lower["spe"] <= 20

    belief.observe_stat_at_most("spe", target)
    assert belief.upper["spe"] >= 20
    assert belief.contains({**dict.fromkeys(STAT_IDS, 0), "spe": 20})


def test_joint_restrict_is_a_superset_projection(dex: Dex) -> None:
    """The projection can only widen, so it can never eliminate the truth."""
    belief = SpreadBelief.unconstrained(dex.species["milotic"]["baseStats"], "calm", {})
    allowed = [(0, 32), (32, 0)]
    assert joint_restrict(belief, "hp", "def", allowed)
    # Neither corner is excluded, even though the pair (32, 32) is now inside
    # the box and was not in `allowed`. That is the documented relaxation.
    assert belief.contains({**dict.fromkeys(STAT_IDS, 0), "hp": 0, "def": 32})
    assert belief.contains({**dict.fromkeys(STAT_IDS, 0), "hp": 32, "def": 0})


def test_mega_forme_base_stats_override(dex: Dex) -> None:
    """The points belong to the set; the base stats they are added to do not.

    Greninja-Mega is 142 base Speed against Greninja's 122, and reading the
    base forme for a mega'd Pokemon is what made every inference about it wrong
    in the same direction.
    """
    belief = SpreadBelief.unconstrained(dex.species["greninja"]["baseStats"], "timid", {})
    base = belief.stat_at("spe", 32)
    mega = belief.stat_at("spe", 32, dex.species["greninjamega"]["baseStats"])
    assert mega - base == 142 - 122


# -------------------------------------------------------------- priors


def test_prior_draws_whole_sets_not_marginals(prior: SetPrior) -> None:
    """Every observed set is one a player registered, so it is coherent by
    construction -- which is a stronger guarantee than any consistency check
    over independent marginals could give."""
    sets = prior.observed_sets("metagross")
    assert len(sets) == 2
    items = {hypothesis.item for hypothesis, _ in sets}
    assert items == {"metagrossite", "lifeorb"}
    for hypothesis, _ in sets:
        assert len(hypothesis.moves) == 4


def test_prior_probability_mixes_empirical_and_composed(prior: SetPrior) -> None:
    common, rare = (h for h, _ in prior.observed_sets("metagross"))
    assert prior.probability(common) > prior.probability(rare) > 0.0
    # A set nobody registered is possible but unlikely, which is what stops a
    # revealed move outside the corpus from making every particle inconsistent.
    invented = SetHypothesis(
        species="metagross",
        item="lifeorb",
        ability="clearbody",
        moves=frozenset({"ironhead", "protect", "icepunch", "agility"}),
        nature="jolly",
    )
    assert 0.0 < prior.probability(invented) < prior.probability(rare)


def test_prior_round_trips_through_json(prior: SetPrior, tmp_path: Path) -> None:
    path = prior.save(tmp_path / "setprior.deadbeef0000.json")
    assert path.exists()
    reloaded = SetPrior.load(tmp_path)
    assert reloaded.observed_sets("metagross") == prior.observed_sets("metagross")
    hypothesis = prior.observed_sets("garchomp")[0][0]
    assert reloaded.probability(hypothesis) == pytest.approx(prior.probability(hypothesis))


def test_load_refuses_to_guess_between_two_priors(prior: SetPrior, tmp_path: Path) -> None:
    prior.save(tmp_path / "setprior.aaaaaaaaaaaa.json")
    prior.save(tmp_path / "setprior.bbbbbbbbbbbb.json")
    with pytest.raises(ValueError, match="ambiguous"):
        SetPrior.load(tmp_path)


# ----------------------------------------------------------- particles


def test_every_particle_satisfies_item_clause(dex: Dex, prior: SetPrior) -> None:
    """Item Clause couples the six, which is why sampling is a Gibbs sweep
    rather than six independent draws."""
    population = _filter(dex, prior, n=64)
    for particle in population.particles:
        items = [h.item for h in particle.sets.values() if h.item]
        assert len(items) == len(set(items)), items


def test_a_revealed_move_eliminates_particles_that_lack_it(dex: Dex, prior: SetPrior) -> None:
    population = _filter(dex, prior)
    context = _context()
    before = population.marginals("garchomp")["moves"]
    assert any(entry["value"] == "earthquake" for entry in before)

    population.observe([_reveal("garchomp", "move", "swordsdance")], context)
    after = {entry["value"] for entry in population.marginals("garchomp")["moves"]}
    assert "swordsdance" in after
    for particle in population.particles:
        if particle.alive:
            assert "swordsdance" in particle.sets["garchomp"].moves


def test_a_revealed_item_propagates_through_item_clause(dex: Dex, prior: SetPrior) -> None:
    population = _filter(dex, prior)
    population.observe([_reveal("garchomp", "item", "sitrusberry")], _context())

    assert population.constraints.item["garchomp"] == "sitrusberry"
    assert "sitrusberry" in population.constraints.excluded_items["incineroar"]
    for particle in population.particles:
        if particle.alive:
            assert particle.sets["incineroar"].item != "sitrusberry"


def test_a_mega_ability_is_not_taken_as_the_registered_one(dex: Dex, prior: SetPrior) -> None:
    """Gengar-Mega is Shadow Tag whatever Gengar was registered with.

    Taking that as a hard constraint on the base species eliminated every
    particle at once, which reads as a filter that has learned something.
    """
    population = _filter(dex, prior)
    population.observe([_reveal("gengar", "ability", "shadowtag")], _context())
    assert "gengar" not in population.constraints.ability
    assert any(p.alive for p in population.particles)

    # A legal ability is still a hard constraint.
    population.observe([_reveal("gengar", "ability", "cursedbody")], _context())
    assert population.constraints.ability["gengar"] == "cursedbody"


def test_resampling_keeps_the_constraints(dex: Dex, prior: SetPrior) -> None:
    population = _filter(dex, prior)
    population.observe([_reveal("milotic", "move", "scald")], _context())
    population.resample()
    assert population.resamples >= 1
    for particle in population.particles:
        assert "scald" in particle.sets["milotic"].moves


def test_a_reveal_either_costs_ess_or_triggers_a_resample(dex: Dex, prior: SetPrior) -> None:
    """Both outcomes are correct, and which one happens is not the point.

    A reveal kills the particles that contradict it. If enough survive, the
    effective sample size falls; if not, the population is redrawn from the
    prior restricted to the new constraints. What must hold either way is that
    the constraint is in force and the population is not empty.
    """
    population = _filter(dex, prior, n=64)
    assert population.effective_sample_size() == pytest.approx(64.0)
    population.observe([_reveal("metagross", "item", "lifeorb")], _context())

    assert population.effective_sample_size() < 64.0 or population.resamples == 1
    alive = [p for p in population.particles if p.alive]
    assert alive
    assert all(p.sets["metagross"].item == "lifeorb" for p in alive)


def test_constraints_allow_is_exact() -> None:
    constraints = TeamConstraints()
    constraints.note_species("metagross")
    hypothesis = SetHypothesis(
        species="metagross",
        item="lifeorb",
        ability="clearbody",
        moves=frozenset({"ironhead", "protect"}),
        nature="jolly",
    )
    assert constraints.allows(hypothesis)
    constraints.moves["metagross"] = {"agility"}
    assert not constraints.allows(hypothesis)


# ---------------------------------------------------- the effects tables


def _derive_from_source(path: Path, handlers: tuple[str, ...]) -> set[str]:
    source = path.read_text(encoding="utf-8")
    return {
        match.group(1)
        for match in re.finditer(r"\n\t(\w+): \{(.*?)\n\t\},", source, re.S)
        if any(handler in match.group(2) for handler in handlers)
    }


@pytest.mark.skipif(not VENDOR.exists(), reason="vendor/showdown not built")
def test_type_boost_items_match_the_pinned_source() -> None:
    """The table is a transcription, so it is checked against what it transcribes.

    A Showdown bump that changes an item's multiplier should be a failing test
    rather than a quietly different damage number.
    """
    source = (VENDOR / "items.ts").read_text(encoding="utf-8")
    derived: dict[str, str] = {}
    for match in re.finditer(r"\n\t(\w+): \{(.*?)\n\t\},", source, re.S):
        name, body = match.group(1), match.group(2)
        if "onBasePower" not in body or "move.type ===" not in body:
            continue
        types = re.findall(r"move\.type === '(\w+)'", body)
        if len(types) == 1 and "chainModify([4915, 4096])" in body:
            derived[name] = types[0]
    # The table covers the single-type boosters that are legal here; plates and
    # orbs boost two types and are not in the format's item pool.
    for item, expected in effect_table.TYPE_BOOST_ITEMS.items():
        assert derived.get(item) == expected, item


@pytest.mark.skipif(not VENDOR.exists(), reason="vendor/showdown not built")
def test_resist_berries_match_the_pinned_source() -> None:
    source = (VENDOR / "items.ts").read_text(encoding="utf-8")
    derived: dict[str, str] = {}
    for match in re.finditer(r"\n\t(\w+): \{(.*?)\n\t\},", source, re.S):
        name, body = match.group(1), match.group(2)
        if "onSourceModifyDamage" in body and "typeMod > 0" in body:
            types = re.findall(r"move\.type === '(\w+)'", body)
            if len(types) == 1:
                derived[name] = types[0]
    assert derived == effect_table.RESIST_BERRIES


@pytest.mark.skipif(not VENDOR.exists(), reason="vendor/showdown not built")
def test_damage_affecting_sets_match_the_pinned_source() -> None:
    handlers = (
        "onModifyAtk",
        "onModifySpA",
        "onModifyDef",
        "onModifySpD",
        "onModifySpe",
        "onBasePower",
        "onModifyDamage",
        "onSourceModifyDamage",
        "onSourceModifyAtk",
        "onSourceModifySpA",
        "onSourceBasePower",
        "onAnyModifyDamage",
        "onFoeBasePower",
        "onAllyBasePower",
        "onEffectiveness",
        "onSourceEffectiveness",
        "onTryHit",
        "onImmunity",
        "onDamage",
        "onModifyMove",
        "onModifyType",
        "onModifyCritRatio",
        "onCriticalHit",
        "onWeather",
        "onAnyBasePower",
        "onSourceModifyDef",
        "onSourceModifySpD",
        "onAllyModifyAtk",
        "onAllyModifySpA",
        "onModifyAccuracy",
    )
    assert _derive_from_source(VENDOR / "abilities.ts", handlers) == (
        effect_table.DAMAGE_AFFECTING_ABILITIES
    )
    assert _derive_from_source(VENDOR / "items.ts", handlers) == (
        effect_table.DAMAGE_AFFECTING_ITEMS
    )


@pytest.mark.skipif(not VENDOR.exists(), reason="vendor/showdown not built")
def test_champions_does_not_change_item_mechanics() -> None:
    """The claim `effects.py` is built on, checked rather than assumed.

    If a future Showdown bump adds a real mechanical override to the mod's
    items, every multiplier in `effects.py` becomes suspect at once -- so the
    claim is a test rather than a sentence in a docstring.
    """
    mod = REPO_ROOT / "vendor" / "showdown" / "data" / "mods" / "champions" / "items.ts"
    interesting = [
        line
        for line in mod.read_text(encoding="utf-8").splitlines()
        if line.strip()
        and not re.match(
            r"^\s*(inherit: true,|isNonstandard:|\}?,?|\w+: \{|export const|desc:|shortDesc:)", line
        )
    ]
    # Only White Herb's Parting Shot desync fix, which is not a damage effect.
    assert all(
        "WhiteHerb" in line
        or "queue" in line
        or "effect" in line
        or "choice" in line
        or "event" in line
        or "order" in line
        or "pokemon" in line
        or "//" in line
        or "}" in line
        or ")" in line
        for line in interesting
    ), interesting


def test_unmodelled_only_counts_effects_that_could_matter(dex: Dex) -> None:
    """Intimidate is announced by the protocol, so hypothesising it costs the
    inference nothing. Counting it as an unknown multiplier is what made every
    particle carry the wide tolerance."""
    chart = effect_table.TypeChart.from_dex(dex)
    announced = SetHypothesis(
        species="incineroar",
        item="sitrusberry",
        ability="intimidate",
        moves=frozenset(),
        nature="careful",
    )
    effects = effect_table.attacker_effects(announced, dex.move("flareblitz"), ["Water"], chart)
    assert effects.is_certain, effects.unmodelled

    hidden = SetHypothesis(
        species="incineroar",
        item="sitrusberry",
        ability="sheerforce",
        moves=frozenset(),
        nature="careful",
    )
    assert not effect_table.attacker_effects(
        hidden, dex.move("flareblitz"), ["Water"], chart
    ).is_certain


def test_life_orb_and_type_items_enter_at_the_right_place(dex: Dex) -> None:
    """Base power and final damage are different multiplication points, and
    `modify` rounds at each of them."""
    chart = effect_table.TypeChart.from_dex(dex)
    orb = SetHypothesis("gengar", "lifeorb", "cursedbody", frozenset(), "timid")
    coal = SetHypothesis("incineroar", "charcoal", "intimidate", frozenset(), "careful")

    assert effect_table.attacker_effects(
        orb, dex.move("sludgebomb"), ["Normal"], chart
    ).final_modifiers == (effect_table.LIFE_ORB,)
    assert effect_table.attacker_effects(
        coal, dex.move("flareblitz"), ["Normal"], chart
    ).base_power_modifiers == (effect_table.TYPE_BOOST,)
    # And the wrong type gets nothing.
    assert not effect_table.attacker_effects(
        coal, dex.move("knockoff"), ["Normal"], chart
    ).base_power_modifiers


# ------------------------------------------------------------ evidence


LOG = """|start
|switch|p1a: Metagross|Metagross, L50, M|207/207
|switch|p1b: Milotic|Milotic, L50, F|202/202
|switch|p2a: Greninja|Greninja, L50, M|149/149
|switch|p2b: Incineroar|Incineroar, L50, M|100/100
|turn|1
|move|p2a: Greninja|Protect|p2a: Greninja
|-singleturn|p2a: Greninja|Protect
|move|p1a: Metagross|Iron Head|p2b: Incineroar
|-damage|p2b: Incineroar|72/100
|move|p2b: Incineroar|Flare Blitz|p1a: Metagross
|-crit|p1a: Metagross
|-damage|p1a: Metagross|91/207
|turn|2
|-start|p2a: Greninja|typechange|Ice|[from] ability: Protean
|move|p2a: Greninja|Blizzard|p1a: Metagross|[spread] p1a,p1b
|-damage|p1a: Metagross|40/207
|-damage|p1b: Milotic|150/202
|move|p1b: Milotic|Scald|p2a: Greninja
|-damage|p2a: Greninja|61/100
|turn|3
"""


def _observations(log: str) -> list[parser.Observation]:
    state = parser.ParserState()
    out: list[parser.Observation] = []
    for line in log.splitlines():
        out.extend(parser.apply(state, line))
    return out


def test_parse_hp() -> None:
    assert parse_hp("48/100 par") == (48, 100)
    assert parse_hp("0 fnt") == (0, 0)
    assert parse_hp(None) is None
    assert parse_hp("nonsense") is None


def test_evidence_pairs_moves_with_the_damage_they_caused(dex: Dex) -> None:
    builder = EvidenceBuilder(dex)
    evidence = builder.feed(_observations(LOG))
    damage = [e for e in evidence if isinstance(e, DamageEvidence)]

    by_move = {(e.move_id, e.defender.slot): e for e in damage}
    assert by_move[("ironhead", "p2b")].lost == 28.0
    assert by_move[("ironhead", "p2b")].denominator == 100
    assert by_move[("flareblitz", "p1a")].lost == pytest.approx(116.0)
    assert by_move[("flareblitz", "p1a")].denominator == 207
    # A spread move produces one piece of evidence per target.
    assert ("blizzard", "p1a") in by_move
    assert ("blizzard", "p1b") in by_move
    assert by_move[("blizzard", "p1a")].spread


def test_a_critical_hit_is_recorded_not_discarded(dex: Dex) -> None:
    builder = EvidenceBuilder(dex)
    evidence = builder.feed(_observations(LOG))
    crits = [e for e in evidence if isinstance(e, DamageEvidence) and e.crit]
    assert [e.move_id for e in crits] == ["flareblitz"]


def test_type_change_is_tracked(dex: Dex) -> None:
    """Protean rewrites the types, and both STAB and effectiveness follow."""
    builder = EvidenceBuilder(dex)
    evidence = builder.feed(_observations(LOG))
    blizzard = next(
        e for e in evidence if isinstance(e, DamageEvidence) and e.move_id == "blizzard"
    )
    assert blizzard.attacker_types == ("Ice",)
    assert builder.types["p2a"] == ("Ice",)


def test_speed_evidence_only_between_equal_priorities(dex: Dex) -> None:
    builder = EvidenceBuilder(dex)
    evidence = builder.feed(_observations(LOG))
    speed = [e for e in evidence if isinstance(e, SpeedEvidence)]
    # Protect is +4 priority, so Greninja going first says nothing about Speed;
    # Metagross before Incineroar on the same turn does.
    pairs = {(e.faster.slot, e.slower.slot) for e in speed}
    assert ("p1a", "p2b") in pairs
    assert not any(e.faster_move == "protect" for e in speed)


def test_reveals_carry_moves_items_and_abilities(dex: Dex) -> None:
    builder = EvidenceBuilder(dex)
    evidence = builder.feed(_observations(LOG))
    reveals = {(r.actor.species, r.kind, r.value) for r in evidence if isinstance(r, Reveal)}
    assert ("greninja", "move", "protect") in reveals
    assert ("greninja", "ability", "protean") in reveals
    assert ("incineroar", "move", "flareblitz") in reveals


# -------------------------------------------------------------- filter


def test_the_filter_runs_a_battle_and_narrows(dex: Dex, prior: SetPrior) -> None:
    belief = BattleBelief(dex, prior, BATTLE_TEAM, player_role="p1", n_particles=32, seed=1)
    snapshot = _snapshot(dex)
    belief.update(_observations(LOG), snapshot)

    assert belief.turns_observed == 1
    summary = belief.summary()
    assert summary["alive"] > 0
    # Protean was announced, so it is a hard fact about Greninja now.
    assert summary["constraints"]["ability"]["greninja"] == "protean"
    assert "protect" in summary["constraints"]["moves"]["greninja"]
    assert belief.set_for("greninja") is not None
    assert belief.stats_for("greninja") is not None


def test_believed_moves_replace_the_empty_turn_one_column(dex: Dex, prior: SetPrior) -> None:
    """The degeneracy `docs/STATUS.md` records: with no belief the opponent's
    candidate set is empty on turn one, the matrix has a single column, and the
    equilibrium collapses to an argmax against an opponent doing nothing."""
    belief = BattleBelief(dex, prior, BATTLE_TEAM, player_role="p1", n_particles=32, seed=1)
    snapshot = _snapshot(dex)

    without = opponent_candidates(snapshot, dex, k=8)
    assert len(without) == 1
    assert without[0]["slots"][0]["kind"] == "none"

    with_belief = opponent_candidates(
        snapshot, dex, k=8, believed_moves=lambda s: belief.believed_moves(s, 0.1)
    )
    assert len(with_belief) > 1
    kinds = {slot["kind"] for action in with_belief for slot in action["slots"]}
    assert kinds == {"move"}


def test_belief_hypothesis_beats_the_constant(dex: Dex, prior: SetPrior) -> None:
    """The seam swap changes the numbers, and does so toward a legal spread.

    `ASSUMED_POINTS = 32` everywhere describes a Pokemon spending 192 of a 66
    point budget; a particle cannot.
    """
    belief = BattleBelief(dex, prior, BATTLE_TEAM, player_role="p1", n_particles=32, seed=1)
    view = {
        "species": "garchomp",
        "base_stats": dex.species["garchomp"]["baseStats"],
        "known": False,
    }
    constant = OpponentHypothesis().stats_for(view)
    informed = BeliefHypothesis(belief=belief).stats_for(view)

    assert sum(informed.values()) < sum(constant.values())
    spread = belief.particles.expected_spread("garchomp")
    assert spread is not None
    # The allocation the search reads has to be one a player could register.
    # Six independent midpoints are not: unconstrained they sum to 96.
    allocation = spread.allocation()
    assert sum(allocation.values()) <= MAX_POINTS_TOTAL
    assert all(spread.lower[s] <= allocation[s] <= spread.upper[s] for s in STAT_IDS)


def test_belief_effects_read_our_own_side_exactly(dex: Dex, prior: SetPrior) -> None:
    belief = BattleBelief(dex, prior, BATTLE_TEAM, player_role="p1", n_particles=8, seed=1)
    effects = BeliefEffects(belief)
    chart = effect_table.TypeChart.from_dex(dex)
    ours = {
        "species": "gengar",
        "known": True,
        "item": "Life Orb",
        "ability": "Cursed Body",
        "moves": [{"id": "sludgebomb"}],
        "types": ["Ghost", "Poison"],
        "status": None,
        "hp_pct": 100.0,
    }
    assert effects.attacker(ours, dex.move("sludgebomb"), ["Normal"], chart).final_modifiers == (
        effect_table.LIFE_ORB,
    )


def test_an_unknown_species_falls_back_rather_than_inventing(dex: Dex, prior: SetPrior) -> None:
    belief = BattleBelief(dex, prior, BATTLE_TEAM, player_role="p1", n_particles=8, seed=1)
    assert belief.stats_for("pikachu") is None
    view = {"species": "pikachu", "base_stats": dex.species["pikachu"]["baseStats"], "known": False}
    assert BeliefHypothesis(belief=belief).stats_for(view) == OpponentHypothesis().stats_for(view)


# ------------------------------------------------------------ evaluate


TEAM_FILE = """Incineroar @ Sitrus Berry
Ability: Intimidate
Level: 50
EVs: 32 HP / 32 Def / 1 SpD / 1 Spe
Careful Nature
- Parting Shot
- Fake Out

Greninja-Mega @ Greninjite
Ability: Protean
Level: 50
EVs: 2 HP / 32 SpA / 32 Spe
Timid Nature
- Blizzard
- Dark Pulse
"""


def test_team_file_truth_reads_points_and_the_Spd_trap() -> None:
    """`Spd` is Speed and `SpD` is Special Defence, case sensitively.

    M1 already found this the hard way (`sim/dex-data.ts:419`); normalising the
    case here would silently move points onto the wrong stat, and the resulting
    coverage number would look fine.
    """
    truth = truth_from_team_file(TEAM_FILE)
    assert set(truth) == {"incineroar", "greninja"}

    incineroar = truth["incineroar"]
    assert incineroar.item == "sitrusberry"
    assert incineroar.ability == "intimidate"
    assert incineroar.nature == "careful"
    assert incineroar.points == {"hp": 32, "atk": 0, "def": 32, "spa": 0, "spd": 1, "spe": 1}

    # A mega forme in the file is the base species in the belief.
    greninja_points = truth["greninja"].points
    assert greninja_points is not None
    assert greninja_points["spe"] == 32
    assert truth["greninja"].moves == frozenset({"blizzard", "darkpulse"})


def test_scoring_a_summary_separates_modal_from_union() -> None:
    """Coverage is measured on the box the search reads, not on the union.

    The union is nearly always covering, so reporting only that would look
    reassuring while saying nothing about the numbers the payoff model uses.
    """
    truth = truth_from_team_file(TEAM_FILE)
    summary = {
        "team": [
            {
                "species": "incineroar",
                "item": [{"value": "sitrusberry", "probability": 0.8}],
                "ability": [{"value": "intimidate", "probability": 1.0}],
                "nature": [{"value": "careful", "probability": 0.6}],
                "moves": [{"value": "fakeout", "probability": 0.9}],
                "sets": [],
                # The union contains the truth; the modal box does not.
                "points": {s: [0, 32] for s in STAT_IDS},
                "points_modal": {s: [0, 0] for s in STAT_IDS},
            }
        ]
    }
    score = score_summary(summary, truth)
    assert score.item.accuracy == 1.0
    assert score.coverage_union.coverage == 1.0
    assert score.coverage.coverage < 1.0


def test_calibration_and_wilson() -> None:
    table = calibration_table([(0.05, 0), (0.05, 0), (0.95, 1), (0.95, 1)])
    assert [row["realised"] for row in table] == [0.0, 1.0]
    low, high = wilson(98, 100)
    assert 0.0 < low < 0.98 < high <= 1.0
    assert wilson(0, 0) == (0.0, 0.0)


def test_empty_score_is_readable() -> None:
    """A trace with no belief events must produce a table, not a crash."""
    empty = BeliefScore().as_dict()
    assert empty["item"]["n"] == 0
    assert empty["coverage"]["coverage"] == 0.0


# ------------------------------------------------------------- helpers


def _reveal(species: str, kind: str, value: str) -> Reveal:
    from champions.belief.evidence import Actor

    return Reveal(1, 1, Actor("p2", "p2a", species), kind, value)


def _context() -> Any:
    from champions.belief.particles import BeliefContext

    return BeliefContext(opponent_side="p2")


def _snapshot(dex: Dex) -> dict[str, Any]:
    def ours(species: str) -> dict[str, Any]:
        entry = dex.species[species]
        stats = {k: v + 20 for k, v in entry["baseStats"].items() if k != "hp"}
        stats["hp"] = entry["baseStats"]["hp"] + 75
        return {
            "species": species,
            "known": True,
            "types": entry["types"],
            "base_stats": entry["baseStats"],
            "stats": stats,
            "hp": stats["hp"],
            "max_hp": stats["hp"],
            "hp_pct": 100.0,
            "status": None,
            "boosts": {},
            "fainted": False,
            "item": None,
            "ability": None,
            "moves": [],
        }

    def theirs(species: str) -> dict[str, Any]:
        entry = dex.species[species]
        return {
            "species": species,
            "known": False,
            "types": entry["types"],
            "base_stats": entry["baseStats"],
            "hp_pct": 100.0,
            "status": None,
            "boosts": {},
            "fainted": False,
            "item": None,
            "ability": None,
            "revealed_moves": [],
        }

    return {
        "turn": 1,
        "player_role": "p1",
        "weather": {},
        "fields": {},
        "side_conditions": {},
        "opponent_side_conditions": {},
        "ours": {
            "active": [ours("metagross"), ours("milotic")],
            "bench": [],
            "remaining": 2,
            "revealed": 2,
        },
        "theirs": {
            "active": [theirs("greninja"), theirs("incineroar")],
            "bench": [],
            "remaining": 2,
            "revealed": 2,
        },
    }
