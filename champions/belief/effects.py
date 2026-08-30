"""What a hypothesised item and ability do to the numbers.

M2 measured the cost of not having this. The one ply agent beats max-base-power
82% on a team with no items, no stat points and inert abilities, and 56% on a
real competitive team built on Intimidate, Protean, Focus Sash, Sitrus Berry,
Leftovers, Rough Skin, Competitive and a Mega -- so its advantage is close to
proportional to how much of the game its model contains, and items and abilities
were the largest missing piece (D30). M4 reached the same boundary from the
other side: the bring-4 is unpredictable from species precisely because it turns
on items and abilities that preview never reveals (D39).

So the belief filter's whole point is to supply them, and this module is where a
hypothesised set becomes numbers the M1 damage layer can take.

## Champions does not change item or ability mechanics

Checked rather than assumed, which is the rule this project keeps. The mod's
`data/mods/champions/items.ts` is 1,046 lines and every entry but one is
`inherit: true` plus an `isNonstandard` toggle -- the sole mechanical change in
the file is White Herb's Parting Shot desync fix, which is not a damage effect.
`abilities.ts` is the same shape: Anger Shell, Berserk, Disguise, Healer,
Natural Cure, Regenerator and Unseen Fist have handler changes, none of them a
damage multiplier. So what Champions changes about items is *which ones are
legal* -- 148 of them survive, and Choice Band, Choice Specs and Assault Vest
are not among them -- and the multipliers of the survivors are mainline's.

That is the opposite shape from moves, where roughly 300 base powers changed
(D26), and it is why the tables below are allowed to exist at all.

## The tables are checked against the pinned source

`tests/test_belief.py` re-derives every table here from
`vendor/showdown/data/items.ts` at the pinned commit and fails if they disagree.
A Showdown bump that changes a multiplier is then a failing test rather than a
quietly different damage number.

## Everything not listed is 1.0, and says so

`SetEffects.unmodelled` names the item or ability that was hypothesised and not
recognised. A caller that wants to widen its tolerance for an unmodelled effect
can; nothing here silently approximates one. This is the same rule
`champions/search/payoff.py` states for the effects it omits: a wrong number
that looks computed is worse than a missing one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from champions.belief.priors import SetHypothesis
from champions.dex.damage import TypeChart

#: Items that raise the base power of moves of one type by 4915/4096 (1.2x).
#: Derived from the pinned `data/items.ts`; the test re-derives and compares.
TYPE_BOOST_ITEMS: dict[str, str] = {
    "blackbelt": "Fighting",
    "blackglasses": "Dark",
    "charcoal": "Fire",
    "dragonfang": "Dragon",
    "fairyfeather": "Fairy",
    "hardstone": "Rock",
    "magnet": "Electric",
    "metalcoat": "Steel",
    "miracleseed": "Grass",
    "mysticwater": "Water",
    "nevermeltice": "Ice",
    "oddincense": "Psychic",
    "poisonbarb": "Poison",
    "rockincense": "Rock",
    "roseincense": "Grass",
    "seaincense": "Water",
    "sharpbeak": "Flying",
    "silkscarf": "Normal",
    "silverpowder": "Bug",
    "softsand": "Ground",
    "spelltag": "Ghost",
    "twistedspoon": "Psychic",
    "waveincense": "Water",
}

#: Berries that halve a super effective hit of one type, once.
RESIST_BERRIES: dict[str, str] = {
    "babiriberry": "Steel",
    "chartiberry": "Rock",
    "chopleberry": "Fighting",
    "cobaberry": "Flying",
    "colburberry": "Dark",
    "habanberry": "Dragon",
    "kasibberry": "Ghost",
    "kebiaberry": "Poison",
    "occaberry": "Fire",
    "passhoberry": "Water",
    "payapaberry": "Psychic",
    "rindoberry": "Grass",
    "roseliberry": "Fairy",
    "shucaberry": "Ground",
    "tangaberry": "Bug",
    "wacanberry": "Electric",
    "yacheberry": "Ice",
}

#: Abilities that can raise a move's priority. An ordering involving one of
#: these is evidence about the ability, not about Speed, so the Speed inference
#: declines to use it rather than drawing a bound it has no right to.
PRIORITY_ABILITIES = frozenset(
    {"prankster", "galewings", "quickdraw", "triage", "myceliummight", "stall"}
)

TYPE_BOOST = 4915 / 4096
LIFE_ORB = 5324 / 4096
EXPERT_BELT = 4915 / 4096
RESIST_BERRY = 0.5
CHOICE_SCARF_SPEED = 1.5

#: Abilities whose effect on damage is a single multiplier this module can
#: apply without reading move flags it does not have. Deliberately short: an
#: ability half-modelled is a wrong number that looks computed.
ATTACK_DOUBLING_ABILITIES = frozenset({"hugepower", "purepower"})
#: 1.5x Attack when statused, and burn is ignored on top.
GUTS = "guts"
#: STAB becomes 2.0 rather than 1.5.
ADAPTABILITY = "adaptability"
#: Halve Fire and Ice damage taken.
THICK_FAT = "thickfat"
THICK_FAT_TYPES = frozenset({"fire", "ice"})
#: Halve damage taken at full HP.
MULTISCALE = frozenset({"multiscale", "shadowshield"})
#: Super effective damage taken is multiplied by 3072/4096 (0.75x).
SUPER_EFFECTIVE_DAMPENERS = frozenset({"solidrock", "filter", "prismarmor"})
SUPER_EFFECTIVE_DAMPENER = 3072 / 4096

#: Abilities granting a type immunity. Absorbing the hit entirely is a large
#: effect and a simple one, so it is here rather than in the unmodelled bucket.
IMMUNITY_ABILITIES: dict[str, str] = {
    "levitate": "ground",
    "voltabsorb": "electric",
    "lightningrod": "electric",
    "motordrive": "electric",
    "waterabsorb": "water",
    "stormdrain": "water",
    "dryskin": "water",
    "flashfire": "fire",
    "sapsipper": "grass",
    "eartheater": "ground",
    "wellbakedbody": "fire",
    "windrider": "flying",
}

#: Items whose effect is real, common, and not a damage multiplier. Listed so
#: that holding one does not count as "unmodelled" and needlessly widen every
#: bound the filter derives.
NON_DAMAGE_ITEMS = frozenset(
    {
        "leftovers",
        "sitrusberry",
        "focussash",
        "lightclay",
        "whiteherb",
        "mentalherb",
        "widelens",
        "brightpowder",
        "covertcloak",
        "clearamulet",
        "safetygoggles",
        "ejectbutton",
        "ejectpack",
        "roomservice",
        "boosterenergy",
    }
)


#: Every ability whose definition touches a damage handler, derived from the
#: pinned `vendor/showdown/data/abilities.ts` and checked by
#: `tests/test_belief.py`. An ability outside this set cannot change a damage
#: number by a route the protocol does not already announce, so hypothesising
#: one costs the inference nothing and it is not counted as unmodelled.
#:
#: This distinction is load-bearing. Treating every unrecognised ability as a
#: possible multiplier meant almost every particle carried the wide tolerance,
#: and the spread layer narrowed no interval across a whole battle. Intimidate,
#: Protean, Competitive and Snow Warning are all absent from this set: each has
#: a real effect, and each announces it -- as a boost, a type change, or a
#: weather message -- so the filter reads it rather than guessing at it.
DAMAGE_AFFECTING_ABILITIES = frozenset(
    {
        "aerilate",
        "analytic",
        "angershell",
        "battery",
        "battlearmor",
        "battlebond",
        "berserk",
        "blaze",
        "bulletproof",
        "chlorophyll",
        "darkaura",
        "defeatist",
        "disguise",
        "dragonize",
        "dragonsmaw",
        "dryskin",
        "eartheater",
        "fairyaura",
        "filter",
        "firemane",
        "flareboost",
        "flashfire",
        "flowergift",
        "fluffy",
        "forecast",
        "friendguard",
        "furcoat",
        "galvanize",
        "gluttony",
        "goodasgold",
        "gorillatactics",
        "grasspelt",
        "guts",
        "hadronengine",
        "heatproof",
        "hugepower",
        "hustle",
        "icebody",
        "iceface",
        "icescales",
        "illuminate",
        "infiltrator",
        "ironfist",
        "keeneye",
        "lightningrod",
        "liquidvoice",
        "longreach",
        "magicbounce",
        "magicguard",
        "magmaarmor",
        "marvelscale",
        "megalauncher",
        "megasol",
        "merciless",
        "mindseye",
        "minus",
        "moldbreaker",
        "motordrive",
        "mountaineer",
        "multiscale",
        "myceliummight",
        "neuroforce",
        "normalize",
        "oblivious",
        "orichalcumpulse",
        "overcoat",
        "overgrow",
        "pixilate",
        "plus",
        "poisonheal",
        "powerspot",
        "prismarmor",
        "propellertail",
        "protosynthesis",
        "punkrock",
        "purepower",
        "purifyingsalt",
        "quarkdrive",
        "quickfeet",
        "raindish",
        "rebound",
        "reckless",
        "refrigerate",
        "ripen",
        "rivalry",
        "rockhead",
        "rockypayload",
        "sandforce",
        "sandrush",
        "sandveil",
        "sapsipper",
        "scrappy",
        "serenegrace",
        "shadowshield",
        "sharpness",
        "sheerforce",
        "shellarmor",
        "skilllink",
        "slowstart",
        "slushrush",
        "sniper",
        "snowcloak",
        "solarpower",
        "solidrock",
        "soundproof",
        "stakeout",
        "stalwart",
        "stancechange",
        "steelworker",
        "steelyspirit",
        "stench",
        "stormdrain",
        "strongjaw",
        "sturdy",
        "superluck",
        "supremeoverlord",
        "surgesurfer",
        "swarm",
        "swiftswim",
        "tangledfeet",
        "technician",
        "telepathy",
        "teravolt",
        "thickfat",
        "tintedlens",
        "torrent",
        "toughclaws",
        "toxicboost",
        "transistor",
        "turboblaze",
        "unburden",
        "unseenfist",
        "voltabsorb",
        "waterabsorb",
        "waterbubble",
        "wellbakedbody",
        "windrider",
        "wonderguard",
        "wonderskin",
    }
)

#: The same for items. Derived from the pinned `vendor/showdown/data/items.ts`.
#: Leftovers, Focus Sash and Mega Stones are all outside it.
DAMAGE_AFFECTING_ITEMS = frozenset(
    {
        "adamantcrystal",
        "adamantorb",
        "assaultvest",
        "babiriberry",
        "blackbelt",
        "blackglasses",
        "brightpowder",
        "charcoal",
        "chartiberry",
        "chilanberry",
        "choiceband",
        "choicescarf",
        "choicespecs",
        "chopleberry",
        "cobaberry",
        "colburberry",
        "cornerstonemask",
        "deepseascale",
        "deepseatooth",
        "dracoplate",
        "dragonfang",
        "dreadplate",
        "earthplate",
        "eviolite",
        "expertbelt",
        "fairyfeather",
        "fistplate",
        "flameplate",
        "focusband",
        "focussash",
        "griseouscore",
        "griseousorb",
        "habanberry",
        "hardstone",
        "hearthflamemask",
        "icicleplate",
        "insectplate",
        "ironball",
        "ironplate",
        "kasibberry",
        "kebiaberry",
        "kingsrock",
        "laxincense",
        "leek",
        "lifeorb",
        "lightball",
        "loadeddice",
        "luckypunch",
        "lustrousglobe",
        "lustrousorb",
        "machobrace",
        "magnet",
        "meadowplate",
        "metalcoat",
        "metalpowder",
        "metronome",
        "mindplate",
        "miracleseed",
        "muscleband",
        "mysticwater",
        "nevermeltice",
        "occaberry",
        "oddincense",
        "passhoberry",
        "payapaberry",
        "pinkbow",
        "pixieplate",
        "poisonbarb",
        "polkadotbow",
        "poweranklet",
        "powerband",
        "powerbelt",
        "powerbracer",
        "powerlens",
        "powerweight",
        "punchingglove",
        "quickpowder",
        "razorclaw",
        "razorfang",
        "rindoberry",
        "rockincense",
        "roseincense",
        "roseliberry",
        "safetygoggles",
        "scopelens",
        "seaincense",
        "sharpbeak",
        "shucaberry",
        "silkscarf",
        "silverpowder",
        "skyplate",
        "softsand",
        "souldew",
        "spelltag",
        "splashplate",
        "spookyplate",
        "stick",
        "stoneplate",
        "tangaberry",
        "thickclub",
        "toxicplate",
        "twistedspoon",
        "vilevial",
        "wacanberry",
        "waveincense",
        "wellspringmask",
        "wiseglasses",
        "yacheberry",
        "zapplate",
    }
)


@dataclass(frozen=True)
class SetEffects:
    """Everything a hypothesised set contributes to one damage calculation.

    The three multiplier lists are kept separate because they enter the formula
    at three different points and `modify` rounds at each of them: collapsing
    them into one product drifts by an HP on a meaningful fraction of rolls,
    which is the difference between a guaranteed knockout and a probable one.
    """

    base_power_modifiers: tuple[float, ...] = ()
    attack_modifiers: tuple[float, ...] = ()
    final_modifiers: tuple[float, ...] = ()
    speed_modifiers: tuple[float, ...] = ()
    stab_override: float | None = None
    ignore_burn: bool = False
    immune: bool = False
    modelled: tuple[str, ...] = ()
    #: Hypothesised item or ability that carries an effect this module does not
    #: know about. The caller widens its tolerance rather than pretending to 1.0.
    unmodelled: tuple[str, ...] = field(default=())

    @property
    def is_certain(self) -> bool:
        return not self.unmodelled

    def as_dict(self) -> dict[str, Any]:
        return {
            "modelled": list(self.modelled),
            "unmodelled": list(self.unmodelled),
            "immune": self.immune,
        }


def attacker_effects(
    hypothesis: SetHypothesis | None,
    move: Mapping[str, Any],
    defender_types: Sequence[str],
    chart: TypeChart,
    statused: bool = False,
) -> SetEffects:
    """What the attacker's item and ability do to this hit."""
    if hypothesis is None:
        return SetEffects(unmodelled=("unknown set",))

    base_power: list[float] = []
    attack: list[float] = []
    final: list[float] = []
    speed: list[float] = []
    modelled: list[str] = []
    unmodelled: list[str] = []
    stab_override: float | None = None
    ignore_burn = False

    move_type = str(move.get("type") or "")
    effectiveness = chart.effectiveness(move_type, list(defender_types))

    item = hypothesis.item
    if item:
        if item in TYPE_BOOST_ITEMS:
            if TYPE_BOOST_ITEMS[item].lower() == move_type.lower():
                base_power.append(TYPE_BOOST)
            modelled.append(item)
        elif item == "lifeorb":
            final.append(LIFE_ORB)
            modelled.append(item)
        elif item == "expertbelt":
            if effectiveness > 0:
                final.append(EXPERT_BELT)
            modelled.append(item)
        elif item == "choicescarf":
            speed.append(CHOICE_SCARF_SPEED)
            modelled.append(item)
        elif (
            item in NON_DAMAGE_ITEMS or item in RESIST_BERRIES or item not in DAMAGE_AFFECTING_ITEMS
        ):
            modelled.append(item)
        else:
            unmodelled.append(item)

    ability = hypothesis.ability
    if ability:
        if ability in ATTACK_DOUBLING_ABILITIES:
            attack.append(2.0)
            modelled.append(ability)
        elif ability == GUTS:
            if statused:
                attack.append(1.5)
            ignore_burn = True
            modelled.append(ability)
        elif ability == ADAPTABILITY:
            if move_type.lower() in {t.lower() for t in _attacker_types(hypothesis, move)}:
                stab_override = 2.0
            modelled.append(ability)
        elif ability not in DAMAGE_AFFECTING_ABILITIES:
            modelled.append(ability)
        else:
            unmodelled.append(ability)

    return SetEffects(
        base_power_modifiers=tuple(base_power),
        attack_modifiers=tuple(attack),
        final_modifiers=tuple(final),
        speed_modifiers=tuple(speed),
        stab_override=stab_override,
        ignore_burn=ignore_burn,
        modelled=tuple(modelled),
        unmodelled=tuple(unmodelled),
    )


def defender_effects(
    hypothesis: SetHypothesis | None,
    move: Mapping[str, Any],
    defender_types: Sequence[str],
    chart: TypeChart,
    at_full_hp: bool = False,
) -> SetEffects:
    """What the defender's item and ability do to this hit."""
    if hypothesis is None:
        return SetEffects(unmodelled=("unknown set",))

    final: list[float] = []
    speed: list[float] = []
    modelled: list[str] = []
    unmodelled: list[str] = []
    immune = False

    move_type = str(move.get("type") or "")
    effectiveness = chart.effectiveness(move_type, list(defender_types))

    item = hypothesis.item
    if item:
        if item in RESIST_BERRIES:
            if effectiveness > 0 and RESIST_BERRIES[item].lower() == move_type.lower():
                final.append(RESIST_BERRY)
            modelled.append(item)
        elif item == "choicescarf":
            speed.append(CHOICE_SCARF_SPEED)
            modelled.append(item)
        elif (
            item in TYPE_BOOST_ITEMS
            or item in NON_DAMAGE_ITEMS
            or item in ("lifeorb", "expertbelt")
            or item not in DAMAGE_AFFECTING_ITEMS
        ):
            modelled.append(item)
        else:
            unmodelled.append(item)

    ability = hypothesis.ability
    if ability:
        if ability in IMMUNITY_ABILITIES:
            if IMMUNITY_ABILITIES[ability] == move_type.lower():
                immune = True
            modelled.append(ability)
        elif ability == THICK_FAT:
            if move_type.lower() in THICK_FAT_TYPES:
                final.append(0.5)
            modelled.append(ability)
        elif ability in MULTISCALE:
            if at_full_hp:
                final.append(0.5)
            modelled.append(ability)
        elif ability in SUPER_EFFECTIVE_DAMPENERS:
            if effectiveness > 0:
                final.append(SUPER_EFFECTIVE_DAMPENER)
            modelled.append(ability)
        elif ability not in DAMAGE_AFFECTING_ABILITIES:
            modelled.append(ability)
        else:
            unmodelled.append(ability)

    return SetEffects(
        final_modifiers=tuple(final),
        speed_modifiers=tuple(speed),
        immune=immune,
        modelled=tuple(modelled),
        unmodelled=tuple(unmodelled),
    )


def speed_multiplier(hypothesis: SetHypothesis | None) -> float:
    """Everything a set multiplies its own Speed by. Choice Scarf, and that is all.

    Read by the Speed-ordering inference: a Pokemon that outran something we
    know the Speed of either invested in Speed or held a Scarf, and the filter
    has to be able to conclude the second rather than forcing the first.
    """
    if hypothesis is None or hypothesis.item != "choicescarf":
        return 1.0
    return CHOICE_SCARF_SPEED


def _attacker_types(hypothesis: SetHypothesis, move: Mapping[str, Any]) -> Sequence[str]:
    """Adaptability needs the attacker's own types, which a set does not carry.

    The caller supplies them on the move mapping under `_attacker_types` when it
    has them; without it Adaptability is recognised and applied as no change,
    which is the conservative direction.
    """
    return move.get("_attacker_types") or ()
