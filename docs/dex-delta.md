# Dex Delta: `champions` vs `gen9` (mainline)

Generated from `vendor/showdown` at commit `bb179fbf8449e3c31632bd56f671ffb4404fa6e7` by `scripts/build_dex.py --delta`. Not published. This is the engineering checklist for M1: every move, item, and ability where `champions` differs from unmodified `gen9`.

## Summary

| Category | Added | Removed | Modified |
| --- | --- | --- | --- |
| moves | 0 | 0 | 303 |
| items | 0 | 0 | 256 |
| abilities | 0 | 0 | 8 |

## Moves (303 modified)

### `absorb`
- `isNonstandard`: null -> "Past"
- `pp`: 25 -> 20

### `acid`
- `isNonstandard`: null -> "Past"
- `pp`: 30 -> 20

### `acupressure`
- `pp`: 30 -> 20

### `aeroblast`
- `isNonstandard`: null -> "Past"

### `agility`
- `pp`: 30 -> 20

### `aircutter`
- `pp`: 25 -> 20

### `anchorshot`
- `basePower`: 80 -> 90
- `zMove`: {"basePower": 160} -> {"basePower": 175}

### `appleacid`
- `basePower`: 80 -> 90
- `zMove`: {"basePower": 160} -> {"basePower": 175}

### `armthrust`
- `isNonstandard`: null -> "Past"

### `astonish`
- `isNonstandard`: null -> "Past"

### `astralbarrage`
- `basePower`: 120 -> 110
- `isNonstandard`: null -> "Past"
- `zMove`: {"basePower": 190} -> {"basePower": 185}

### `attackorder`
- `isNonstandard`: null -> "Past"

### `aurorabeam`
- `isNonstandard`: null -> "Past"

### `babydolleyes`
- `pp`: 30 -> 20

### `banefulbunker`
- `pp`: 10 -> 5

### `batonpass`
- `pp`: 40 -> 20

### `beakblast`
- `basePower`: 100 -> 120
- `maxMove`: {"basePower": 130} -> {"basePower": 140}
- `pp`: 15 -> 5
- `zMove`: {"basePower": 180} -> {"basePower": 190}

### `behemothbash`
- `isNonstandard`: null -> "Past"

### `behemothblade`
- `isNonstandard`: null -> "Past"

### `belch`
- `desc`: null -> "Fails unless the user has eaten a Berry, either by eating one that was held, stealing and eating one off another Pokemon with Bug Bite or Pluck, or eating one that was thrown at it with Fling. Once t…
- `shortDesc`: null -> "Fails unless the user has eaten a Berry."

### `bite`
- `pp`: 25 -> 20

### `blazingtorque`
- `isNonstandard`: "Unobtainable" -> "Past"

### `bleakwindstorm`
- `isNonstandard`: null -> "Past"

### `bloodmoon`
- `basePower`: 140 -> 130
- `isNonstandard`: null -> "Past"
- `zMove`: {"basePower": 200} -> {"basePower": 195}

### `blueflare`
- `isNonstandard`: null -> "Past"

### `boltbeak`
- `basePower`: 85 -> 80

### `boltstrike`
- `isNonstandard`: null -> "Past"

### `bonerush`
- `basePower`: 25 -> 30

### `branchpoke`
- `isNonstandard`: null -> "Past"
- `pp`: 40 -> 20

### `brine`
- `isNonstandard`: null -> "Past"

### `bubble`
- `pp`: 30 -> 20

### `bubblebeam`
- `isNonstandard`: null -> "Past"

### `bulletpunch`
- `pp`: 30 -> 20

### `bulletseed`
- `pp`: 30 -> 20

### `burningbulwark`
- `isNonstandard`: null -> "Past"

### `burnup`
- `isNonstandard`: "Unobtainable" -> null

### `celebrate`
- `isNonstandard`: null -> "Past"
- `pp`: 40 -> 20

### `chloroblast`
- `isNonstandard`: null -> "Past"

### `clangoroussoul`
- `accuracy`: 100 -> true

### `collisioncourse`
- `isNonstandard`: null -> "Past"

### `combattorque`
- `isNonstandard`: "Unobtainable" -> "Past"

### `confide`
- `isNonstandard`: null -> "Past"

### `confusion`
- `isNonstandard`: null -> "Past"
- `pp`: 25 -> 20

### `constrict`
- `pp`: 35 -> 20

### `conversion`
- `isNonstandard`: null -> "Past"
- `pp`: 30 -> 20

### `conversion2`
- `isNonstandard`: null -> "Past"
- `pp`: 30 -> 20

### `corrosivegas`
- `isNonstandard`: "Unobtainable" -> null
- `pp`: 40 -> 20

### `cottonspore`
- `pp`: 40 -> 20

### `courtchange`
- `isNonstandard`: null -> "Past"

### `covet`
- `pp`: 25 -> 20

### `crabhammer`
- `accuracy`: 90 -> 95

### `crushclaw`
- `flags`: {"contact": 1, "protect": 1, "mirror": 1, "metronome": 1} -> {"contact": 1, "protect": 1, "mirror": 1, "metronome": 1, "slicing": 1}

### `crushgrip`
- `isNonstandard`: null -> "Past"

### `cut`
- `isNonstandard`: "Unobtainable" -> "Past"
- `pp`: 30 -> 20

### `darkvoid`
- `isNonstandard`: null -> "Past"

### `defendorder`
- `isNonstandard`: null -> "Past"

### `defensecurl`
- `isNonstandard`: null -> "Past"
- `pp`: 40 -> 20

### `diamondstorm`
- `isNonstandard`: null -> "Past"

### `direclaw`
- `desc`: null -> "Has a 30% chance to cause the target to either fall asleep, become poisoned, or become paralyzed."
- `flags`: {"contact": 1, "protect": 1, "mirror": 1, "metronome": 1} -> {"contact": 1, "protect": 1, "mirror": 1, "metronome": 1, "slicing": 1}
- `secondaries`: [{"chance": 50}] -> [{"chance": 30}]
- `secondary`: {"chance": 50} -> {"chance": 30}
- `shortDesc`: null -> "30% chance to sleep, poison, or paralyze target."

### `disarmingvoice`
- `isNonstandard`: null -> "Past"

### `doodle`
- `isNonstandard`: null -> "Past"

### `doomdesire`
- `isNonstandard`: null -> "Past"

### `doublekick`
- `isNonstandard`: null -> "Past"
- `pp`: 30 -> 20

### `doubleshock`
- `isNonstandard`: null -> "Past"

### `dragonascent`
- `isNonstandard`: null -> "Past"

### `dragonbreath`
- `isNonstandard`: null -> "Past"

### `dragoncheer`
- `flags`: {"bypasssub": 1, "allyanim": 1, "metronome": 1} -> {"bypasssub": 1, "allyanim": 1, "metronome": 1, "sound": 1}

### `dragonclaw`
- `flags`: {"contact": 1, "protect": 1, "mirror": 1, "metronome": 1} -> {"contact": 1, "protect": 1, "mirror": 1, "metronome": 1, "slicing": 1}

### `dragonenergy`
- `isNonstandard`: null -> "Past"

### `dragonhammer`
- `basePower`: 90 -> 100
- `isNonstandard`: null -> "Past"
- `zMove`: {"basePower": 175} -> {"basePower": 180}

### `dreameater`
- `isNonstandard`: null -> "Past"

### `drumbeating`
- `isNonstandard`: null -> "Past"

### `dynamaxcannon`
- `isNonstandard`: null -> "Past"

### `echoedvoice`
- `isNonstandard`: null -> "Past"

### `electrify`
- `isNonstandard`: "Past" -> null

### `electrodrift`
- `isNonstandard`: null -> "Past"

### `ember`
- `isNonstandard`: null -> "Past"
- `pp`: 25 -> 20

### `esperwing`
- `isNonstandard`: null -> "Past"

### `fairywind`
- `isNonstandard`: null -> "Past"
- `pp`: 30 -> 20

### `fakeout`
- `desc`: null -> "Has a 100% chance to make the target flinch. This move cannot be selected unless it is the user's first turn on the field."

### `falsesurrender`
- `isNonstandard`: null -> "Past"

### `falseswipe`
- `isNonstandard`: null -> "Past"
- `pp`: 40 -> 20

### `fellstinger`
- `pp`: 25 -> 20

### `fierywrath`
- `isNonstandard`: null -> "Past"

### `filletaway`
- `isNonstandard`: null -> "Past"

### `firelash`
- `basePower`: 80 -> 90
- `zMove`: {"basePower": 160} -> {"basePower": 175}

### `firepledge`
- `isNonstandard`: null -> "Past"

### `firstimpression`
- `basePower`: 90 -> 100
- `desc`: null -> "This move cannot be selected unless it is the user's first turn on the field."
- `zMove`: {"basePower": 175} -> {"basePower": 180}

### `fishiousrend`
- `basePower`: 85 -> 80

### `flamewheel`
- `isNonstandard`: null -> "Past"
- `pp`: 25 -> 20

### `fleurcannon`
- `isNonstandard`: null -> "Past"

### `floralhealing`
- `isNonstandard`: null -> "Past"

### `focusenergy`
- `pp`: 30 -> 20

### `forcepalm`
- `isNonstandard`: null -> "Past"

### `foresight`
- `pp`: 40 -> 20

### `freezedry`
- `desc`: null -> "This move's type effectiveness against Water is changed to be super effective no matter what this move's type is."
- `secondaries`: [{"chance": 10, "status": "frz"}] -> null
- `secondary`: {"chance": 10, "status": "frz"} -> null
- `shortDesc`: null -> "Super effective on Water."

### `freezeshock`
- `isNonstandard`: null -> "Past"

### `freezingglare`
- `isNonstandard`: null -> "Past"

### `furyattack`
- `isNonstandard`: null -> "Past"

### `furycutter`
- `isNonstandard`: null -> "Past"

### `furyswipes`
- `isNonstandard`: null -> "Past"

### `fusionbolt`
- `isNonstandard`: null -> "Past"

### `fusionflare`
- `isNonstandard`: null -> "Past"

### `geargrind`
- `accuracy`: 85 -> 90
- `basePower`: 50 -> 60

### `glaciallance`
- `isNonstandard`: null -> "Past"

### `glaciate`
- `isNonstandard`: null -> "Past"

### `glaiverush`
- `isNonstandard`: null -> "Past"

### `glare`
- `pp`: 30 -> 20

### `grasspledge`
- `isNonstandard`: null -> "Past"

### `gravapple`
- `basePower`: 80 -> 90
- `zMove`: {"basePower": 160} -> {"basePower": 175}

### `growl`
- `isNonstandard`: null -> "Past"
- `pp`: 40 -> 20

### `growth`
- `baseMoveType`: "Normal" -> "Grass"
- `type`: "Normal" -> "Grass"

### `gust`
- `isNonstandard`: null -> "Past"
- `pp`: 35 -> 20

### `happyhour`
- `isNonstandard`: null -> "Past"
- `pp`: 30 -> 20

### `harden`
- `isNonstandard`: null -> "Past"
- `pp`: 30 -> 20

### `haze`
- `pp`: 30 -> 20

### `headbutt`
- `isNonstandard`: null -> "Past"

### `heartstamp`
- `pp`: 25 -> 20

### `heartswap`
- `isNonstandard`: null -> "Past"

### `holdback`
- `isNonstandard`: "Unobtainable" -> "Past"
- `pp`: 40 -> 20

### `holdhands`
- `isNonstandard`: "Unobtainable" -> "Past"
- `pp`: 40 -> 20

### `honeclaws`
- `isNonstandard`: null -> "Past"

### `hornattack`
- `isNonstandard`: null -> "Past"
- `pp`: 25 -> 20

### `howl`
- `flags`: {"snatch": 1, "sound": 1, "metronome": 1} -> {"snatch": 1, "sound": 1, "bypasssub": 1, "metronome": 1}
- `pp`: 40 -> 20

### `hydrosteam`
- `isNonstandard`: null -> "Past"

### `hyperdrill`
- `basePower`: 100 -> 120
- `isNonstandard`: null -> "Past"
- `maxMove`: {"basePower": 130} -> {"basePower": 140}
- `zMove`: {"basePower": 180} -> {"basePower": 190}

### `hyperspacefury`
- `isNonstandard`: null -> "Past"

### `hyperspacehole`
- `isNonstandard`: null -> "Past"

### `iceburn`
- `isNonstandard`: null -> "Past"

### `iceshard`
- `pp`: 30 -> 20

### `iciclespear`
- `pp`: 30 -> 20

### `incinerate`
- `isNonstandard`: null -> "Past"

### `infernalparade`
- `basePower`: 60 -> 65
- `maxMove`: {"basePower": 110} -> {"basePower": 120}

### `iondeluge`
- `pp`: 25 -> 20

### `ironhead`
- `desc`: null -> "Has a 20% chance to make the target flinch."
- `secondaries`: [{"chance": 30, "volatileStatus": "flinch"}] -> [{"chance": 20, "volatileStatus": "flinch"}]
- `secondary`: {"chance": 30, "volatileStatus": "flinch"} -> {"chance": 20, "volatileStatus": "flinch"}
- `shortDesc`: null -> "20% chance to make the target flinch."

### `ivycudgel`
- `isNonstandard`: null -> "Past"

### `jawlock`
- `isNonstandard`: null -> "Past"

### `judgment`
- `isNonstandard`: null -> "Past"

### `junglehealing`
- `isNonstandard`: null -> "Past"

### `karatechop`
- `pp`: 25 -> 20

### `kingsshield`
- `isNonstandard`: "Past" -> null
- `pp`: 10 -> 5

### `laserfocus`
- `pp`: 30 -> 20

### `leafage`
- `isNonstandard`: null -> "Past"
- `pp`: 40 -> 20

### `leer`
- `isNonstandard`: null -> "Past"
- `pp`: 30 -> 20

### `lick`
- `isNonstandard`: null -> "Past"
- `pp`: 30 -> 20

### `lightofruin`
- `isNonstandard`: "Past" -> null

### `lightscreen`
- `pp`: 30 -> 20

### `luckychant`
- `pp`: 30 -> 20

### `lunarblessing`
- `isNonstandard`: null -> "Past"

### `lunardance`
- `isNonstandard`: null -> "Past"

### `lusterpurge`
- `isNonstandard`: null -> "Past"

### `machpunch`
- `pp`: 30 -> 20

### `magicalleaf`
- `isNonstandard`: null -> "Past"

### `magicaltorque`
- `isNonstandard`: "Unobtainable" -> "Past"

### `magmastorm`
- `isNonstandard`: null -> "Past"

### `magnitude`
- `pp`: 30 -> 20

### `makeitrain`
- `accuracy`: 100 -> 95
- `desc`: null -> "Lowers the user's Special Attack by 2 stages."
- `self`: {"boosts": {"spa": -1}} -> {"boosts": {"spa": -2}}
- `shortDesc`: null -> "Lowers the user's Sp. Atk by 2. Hits foe(s)."

### `malignantchain`
- `isNonstandard`: null -> "Past"

### `meditate`
- `pp`: 40 -> 20

### `megadrain`
- `isNonstandard`: null -> "Past"

### `megapunch`
- `isNonstandard`: null -> "Past"

### `metalclaw`
- `flags`: {"contact": 1, "protect": 1, "mirror": 1, "metronome": 1} -> {"contact": 1, "protect": 1, "mirror": 1, "metronome": 1, "slicing": 1}
- `isNonstandard`: null -> "Past"
- `pp`: 35 -> 20

### `metalsound`
- `pp`: 40 -> 20

### `metronome`
- `isNonstandard`: null -> "Past"

### `mightycleave`
- `isNonstandard`: null -> "Past"

### `milkdrink`
- `isNonstandard`: null -> "Past"

### `mimic`
- `isNonstandard`: null -> "Past"

### `miracleeye`
- `pp`: 40 -> 20

### `mist`
- `isNonstandard`: null -> "Past"
- `pp`: 30 -> 20

### `mistball`
- `isNonstandard`: null -> "Past"

### `moonblast`
- `desc`: null -> "Has a 10% chance to lower the target's Special Attack by 1 stage."
- `secondaries`: [{"chance": 30, "boosts": {"spa": -1}}] -> [{"chance": 10, "boosts": {"spa": -1}}]
- `secondary`: {"chance": 30, "boosts": {"spa": -1}} -> {"chance": 10, "boosts": {"spa": -1}}
- `shortDesc`: null -> "10% chance to lower the target's Sp. Atk by 1."

### `moongeistbeam`
- `isNonstandard`: null -> "Past"

### `mountaingale`
- `basePower`: 100 -> 120
- `maxMove`: {"basePower": 130} -> {"basePower": 140}
- `zMove`: {"basePower": 180} -> {"basePower": 190}

### `mysticalpower`
- `isNonstandard`: null -> "Past"

### `nightdaze`
- `basePower`: 85 -> 90
- `zMove`: {"basePower": 160} -> {"basePower": 175}

### `nightslash`
- `pp`: 15 -> 20

### `nihillight`
- `pp`: 10 -> 5

### `nobleroar`
- `pp`: 30 -> 20

### `noxioustorque`
- `isNonstandard`: "Unobtainable" -> "Past"

### `obstruct`
- `pp`: 10 -> 5

### `odorsleuth`
- `pp`: 40 -> 20

### `orderup`
- `isNonstandard`: null -> "Past"

### `originpulse`
- `isNonstandard`: null -> "Past"

### `overdrive`
- `isNonstandard`: null -> "Past"

### `payday`
- `isNonstandard`: null -> "Past"

### `peck`
- `isNonstandard`: null -> "Past"
- `pp`: 35 -> 20

### `photongeyser`
- `isNonstandard`: null -> "Past"

### `playnice`
- `isNonstandard`: null -> "Past"

### `poisongas`
- `isNonstandard`: null -> "Past"
- `pp`: 40 -> 20

### `poisonpowder`
- `pp`: 35 -> 20

### `poisonsting`
- `isNonstandard`: null -> "Past"
- `pp`: 35 -> 20

### `poisontail`
- `isNonstandard`: null -> "Past"
- `pp`: 25 -> 20

### `pound`
- `pp`: 35 -> 20

### `powdersnow`
- `isNonstandard`: null -> "Past"
- `pp`: 25 -> 20

### `powershift`
- `isNonstandard`: "Unobtainable" -> null

### `precipiceblades`
- `isNonstandard`: null -> "Past"

### `present`
- `isNonstandard`: null -> "Past"

### `prismaticlaser`
- `isNonstandard`: null -> "Past"

### `protect`
- `pp`: 10 -> 5

### `psybeam`
- `isNonstandard`: null -> "Past"

### `psyblade`
- `isNonstandard`: null -> "Past"

### `psychoboost`
- `isNonstandard`: null -> "Past"

### `psyshieldbash`
- `basePower`: 70 -> 90
- `maxMove`: {"basePower": 120} -> {"basePower": 130}
- `zMove`: {"basePower": 140} -> {"basePower": 175}

### `psystrike`
- `isNonstandard`: null -> "Past"

### `purify`
- `pp`: 20 -> 5

### `pyroball`
- `isNonstandard`: null -> "Past"

### `quickattack`
- `pp`: 30 -> 20

### `ragefist`
- `desc`: null -> "Power is equal to 50+(X*50), where X is the total number of times the user has been hit by a damaging attack during the battle, even if the user did not lose HP from the attack. X cannot be greater t…
- `shortDesc`: null -> "+50 BP/hit on user. Max 6 hits. Resets on switch-out."

### `rapidspin`
- `pp`: 40 -> 20

### `razorleaf`
- `isNonstandard`: null -> "Past"
- `pp`: 25 -> 20

### `relicsong`
- `isNonstandard`: null -> "Past"

### `retaliate`
- `isNonstandard`: null -> "Past"

### `revelationdance`
- `basePower`: 90 -> 100
- `isNonstandard`: null -> "Past"
- `zMove`: {"basePower": 175} -> {"basePower": 180}

### `revivalblessing`
- `isNonstandard`: null -> "Past"

### `roaroftime`
- `isNonstandard`: null -> "Past"

### `rocksmash`
- `isNonstandard`: null -> "Past"

### `rockthrow`
- `isNonstandard`: null -> "Past"

### `rollout`
- `isNonstandard`: null -> "Past"

### `ruination`
- `isNonstandard`: null -> "Past"

### `sacredfire`
- `isNonstandard`: null -> "Past"

### `safeguard`
- `pp`: 25 -> 20

### `saltcure`
- `desc`: null -> "Causes damage to the target equal to 1/16 of its maximum HP (1/8 if the target is Steel or Water type), rounded down, at the end of each turn during effect. This effect ends when the target is no lon…
- `shortDesc`: null -> "Deals 1/16 max HP each turn; 1/8 on Steel, Water."

### `sandattack`
- `isNonstandard`: null -> "Past"

### `sandsearstorm`
- `isNonstandard`: null -> "Past"

### `sandstorm`
- `pp`: 10 -> 5

### `scratch`
- `isNonstandard`: null -> "Past"
- `pp`: 35 -> 20

### `screech`
- `pp`: 40 -> 20

### `secretsword`
- `isNonstandard`: null -> "Past"

### `seedflare`
- `isNonstandard`: null -> "Past"

### `shadowclaw`
- `flags`: {"contact": 1, "protect": 1, "mirror": 1, "metronome": 1} -> {"contact": 1, "protect": 1, "mirror": 1, "metronome": 1, "slicing": 1}

### `shadowforce`
- `isNonstandard`: null -> "Past"

### `shadowsneak`
- `pp`: 30 -> 20

### `sharpen`
- `pp`: 30 -> 20

### `shelltrap`
- `pp`: 5 -> 10

### `shiftgear`
- `isNonstandard`: null -> "Past"

### `shockwave`
- `isNonstandard`: null -> "Past"

### `shoreup`
- `isNonstandard`: null -> "Past"

### `silktrap`
- `isNonstandard`: null -> "Past"

### `sketch`
- `isNonstandard`: null -> "Past"

### `slam`
- `isNonstandard`: null -> "Past"

### `slash`
- `isNonstandard`: null -> "Past"

### `sludge`
- `isNonstandard`: null -> "Past"

### `smog`
- `isNonstandard`: null -> "Past"

### `smokescreen`
- `isNonstandard`: null -> "Past"

### `snaptrap`
- `baseMoveType`: "Grass" -> "Steel"
- `isNonstandard`: "Past" -> null
- `type`: "Grass" -> "Steel"

### `snipeshot`
- `basePower`: 80 -> 85
- `isNonstandard`: null -> "Past"

### `snowscape`
- `pp`: 10 -> 5

### `spacialrend`
- `isNonstandard`: null -> "Past"

### `spark`
- `isNonstandard`: null -> "Past"

### `spikyshield`
- `pp`: 10 -> 5

### `spinout`
- `isNonstandard`: null -> "Past"
- `pp`: 5 -> 10

### `spiritshackle`
- `basePower`: 80 -> 90
- `zMove`: {"basePower": 160} -> {"basePower": 175}

### `splash`
- `isNonstandard`: null -> "Past"
- `pp`: 40 -> 20

### `springtidestorm`
- `isNonstandard`: null -> "Past"

### `steameruption`
- `isNonstandard`: null -> "Past"

### `steelwing`
- `pp`: 25 -> 20

### `stomp`
- `isNonstandard`: null -> "Past"

### `stormthrow`
- `isNonstandard`: "Past" -> null

### `strangesteam`
- `isNonstandard`: null -> "Past"

### `strength`
- `isNonstandard`: null -> "Past"

### `stringshot`
- `pp`: 40 -> 20

### `stuffcheeks`
- `desc`: null -> "Fails if the user is not holding a Berry. The user eats its Berry and raises its Defense by 2 stages. This effect is not prevented by the Klutz or Unnerve Abilities, or the effects of Embargo or Magi…
- `shortDesc`: null -> "Fails unless the user has a berry. User eats Berry, Def +2."

### `stunspore`
- `pp`: 30 -> 20

### `sunsteelstrike`
- `isNonstandard`: null -> "Past"

### `supersonic`
- `isNonstandard`: null -> "Past"

### `surgingstrikes`
- `isNonstandard`: null -> "Past"

### `swift`
- `isNonstandard`: null -> "Past"

### `syrupbomb`
- `accuracy`: 85 -> 90

### `tachyoncutter`
- `isNonstandard`: null -> "Past"

### `tackle`
- `isNonstandard`: null -> "Past"
- `pp`: 35 -> 20

### `tailglow`
- `isNonstandard`: null -> "Past"

### `tailwhip`
- `isNonstandard`: null -> "Past"
- `pp`: 30 -> 20

### `takedown`
- `isNonstandard`: null -> "Past"

### `takeheart`
- `isNonstandard`: null -> "Past"

### `tarshot`
- `isNonstandard`: null -> "Past"

### `teleport`
- `isNonstandard`: null -> "Past"

### `terablast`
- `isNonstandard`: null -> "Past"

### `terastarstorm`
- `isNonstandard`: null -> "Past"

### `thief`
- `pp`: 25 -> 20

### `thundercage`
- `isNonstandard`: null -> "Past"

### `thunderclap`
- `isNonstandard`: null -> "Past"

### `thunderouskick`
- `isNonstandard`: null -> "Past"

### `thundershock`
- `isNonstandard`: null -> "Past"
- `pp`: 30 -> 20

### `toxicthread`
- `boosts`: {"spe": -1} -> {"spe": -2}
- `desc`: null -> "Lowers the target's Speed by 2 stages and poisons it."
- `shortDesc`: null -> "Lowers the target's Speed by 2 and poisons it."

### `trickortreat`
- `isNonstandard`: "Past" -> null

### `tripledive`
- `basePower`: 30 -> 35
- `isNonstandard`: null -> "Past"

### `triplekick`
- `isNonstandard`: null -> "Past"

### `tropkick`
- `basePower`: 70 -> 85
- `maxMove`: {"basePower": 120} -> {"basePower": 130}
- `zMove`: {"basePower": 140} -> {"basePower": 160}

### `twister`
- `isNonstandard`: null -> "Past"

### `vacuumwave`
- `pp`: 30 -> 20

### `vcreate`
- `isNonstandard`: "Unobtainable" -> "Past"

### `victorydance`
- `isNonstandard`: null -> "Past"

### `vinewhip`
- `isNonstandard`: null -> "Past"
- `pp`: 25 -> 20

### `visegrip`
- `isNonstandard`: null -> "Past"
- `pp`: 30 -> 20

### `watergun`
- `isNonstandard`: null -> "Past"
- `pp`: 25 -> 20

### `waterpledge`
- `isNonstandard`: null -> "Past"

### `wickedblow`
- `isNonstandard`: null -> "Past"

### `wickedtorque`
- `isNonstandard`: "Unobtainable" -> "Past"

### `wildboltstorm`
- `isNonstandard`: null -> "Past"

### `wingattack`
- `isNonstandard`: null -> "Past"
- `pp`: 35 -> 20

### `withdraw`
- `isNonstandard`: null -> "Past"
- `pp`: 40 -> 20

### `workup`
- `isNonstandard`: null -> "Past"
- `pp`: 30 -> 20

### `zingzap`
- `isNonstandard`: null -> "Past"

## Items (256 modified)

### `abilityshield`
- `isNonstandard`: null -> "Past"

### `abomasite`
- `isNonstandard`: "Past" -> null

### `absolite`
- `isNonstandard`: "Past" -> null

### `absorbbulb`
- `isNonstandard`: null -> "Past"

### `adamantcrystal`
- `isNonstandard`: null -> "Past"

### `adamantorb`
- `isNonstandard`: null -> "Past"

### `adrenalineorb`
- `isNonstandard`: null -> "Past"

### `aerodactylite`
- `isNonstandard`: "Past" -> null

### `aggronite`
- `isNonstandard`: "Past" -> null

### `aguavberry`
- `isNonstandard`: null -> "Past"

### `airballoon`
- `isNonstandard`: null -> "Past"

### `alakazite`
- `isNonstandard`: "Past" -> null

### `altarianite`
- `isNonstandard`: "Past" -> null

### `ampharosite`
- `isNonstandard`: "Past" -> null

### `apicotberry`
- `isNonstandard`: null -> "Past"

### `assaultvest`
- `isNonstandard`: null -> "Past"

### `audinite`
- `isNonstandard`: "Past" -> null

### `auspiciousarmor`
- `isNonstandard`: null -> "Past"

### `banettite`
- `isNonstandard`: "Past" -> null

### `barbaracite`
- `isNonstandard`: "Future" -> null

### `beastball`
- `isNonstandard`: null -> "Past"

### `beedrillite`
- `isNonstandard`: "Past" -> null

### `berrysweet`
- `isNonstandard`: null -> "Past"

### `bignugget`
- `isNonstandard`: null -> "Past"

### `bindingband`
- `isNonstandard`: null -> "Past"

### `blacksludge`
- `isNonstandard`: null -> "Past"

### `blastoisinite`
- `isNonstandard`: "Past" -> null

### `blazikenite`
- `isNonstandard`: "Past" -> null

### `blunderpolicy`
- `isNonstandard`: null -> "Past"

### `boosterenergy`
- `isNonstandard`: null -> "Past"

### `bottlecap`
- `isNonstandard`: null -> "Past"

### `cameruptite`
- `isNonstandard`: "Past" -> null

### `cellbattery`
- `isNonstandard`: null -> "Past"

### `chandelurite`
- `isNonstandard`: "Future" -> null

### `charizarditex`
- `isNonstandard`: "Past" -> null

### `charizarditey`
- `isNonstandard`: "Past" -> null

### `chesnaughtite`
- `isNonstandard`: "Future" -> null

### `chimechite`
- `isNonstandard`: "Future" -> null

### `chippedpot`
- `isNonstandard`: null -> "Past"

### `choiceband`
- `isNonstandard`: null -> "Past"

### `choicespecs`
- `isNonstandard`: null -> "Past"

### `clearamulet`
- `isNonstandard`: null -> "Past"

### `clefablite`
- `isNonstandard`: "Future" -> null

### `cloversweet`
- `isNonstandard`: null -> "Past"

### `cornerstonemask`
- `isNonstandard`: null -> "Past"

### `covertcloak`
- `isNonstandard`: null -> "Past"

### `crabominite`
- `isNonstandard`: "Future" -> null

### `crackedpot`
- `isNonstandard`: null -> "Past"

### `custapberry`
- `isNonstandard`: null -> "Past"

### `darkranite`
- `isNonstandard`: "Future" -> "Past"

### `dawnstone`
- `isNonstandard`: null -> "Past"

### `delphoxite`
- `isNonstandard`: "Future" -> null

### `destinyknot`
- `isNonstandard`: null -> "Past"

### `diveball`
- `isNonstandard`: null -> "Past"

### `dracoplate`
- `isNonstandard`: null -> "Past"

### `dragalgite`
- `isNonstandard`: "Future" -> null

### `dragoninite`
- `isNonstandard`: "Future" -> null

### `dragonscale`
- `isNonstandard`: null -> "Past"

### `drampanite`
- `isNonstandard`: "Future" -> null

### `dreadplate`
- `isNonstandard`: null -> "Past"

### `dreamball`
- `isNonstandard`: null -> "Past"

### `dubiousdisc`
- `isNonstandard`: null -> "Past"

### `duskball`
- `isNonstandard`: null -> "Past"

### `duskstone`
- `isNonstandard`: null -> "Past"

### `earthplate`
- `isNonstandard`: null -> "Past"

### `eelektrossite`
- `isNonstandard`: "Future" -> null

### `ejectbutton`
- `isNonstandard`: null -> "Past"

### `ejectpack`
- `isNonstandard`: null -> "Past"

### `electirizer`
- `isNonstandard`: null -> "Past"

### `electricseed`
- `isNonstandard`: null -> "Past"

### `emboarite`
- `isNonstandard`: "Future" -> null

### `enigmaberry`
- `isNonstandard`: null -> "Past"

### `eviolite`
- `isNonstandard`: null -> "Past"

### `excadrite`
- `isNonstandard`: "Future" -> null

### `falinksite`
- `isNonstandard`: "Future" -> null

### `fastball`
- `isNonstandard`: null -> "Past"

### `feraligite`
- `isNonstandard`: "Future" -> null

### `figyberry`
- `isNonstandard`: null -> "Past"

### `firestone`
- `isNonstandard`: null -> "Past"

### `fistplate`
- `isNonstandard`: null -> "Past"

### `flameorb`
- `isNonstandard`: null -> "Past"

### `flameplate`
- `isNonstandard`: null -> "Past"

### `floatstone`
- `isNonstandard`: null -> "Past"

### `floettite`
- `isNonstandard`: "Future" -> null

### `flowersweet`
- `isNonstandard`: null -> "Past"

### `friendball`
- `isNonstandard`: null -> "Past"

### `froslassite`
- `isNonstandard`: "Future" -> null

### `galaricacuff`
- `isNonstandard`: null -> "Past"

### `galaricawreath`
- `isNonstandard`: null -> "Past"

### `galladite`
- `isNonstandard`: "Past" -> null

### `ganlonberry`
- `isNonstandard`: null -> "Past"

### `garchompite`
- `isNonstandard`: "Past" -> null

### `gardevoirite`
- `isNonstandard`: "Past" -> null

### `gengarite`
- `isNonstandard`: "Past" -> null

### `glalitite`
- `isNonstandard`: "Past" -> null

### `glimmoranite`
- `isNonstandard`: "Future" -> null

### `goldbottlecap`
- `isNonstandard`: null -> "Past"

### `golisopite`
- `isNonstandard`: "Future" -> "Past"

### `golurkite`
- `isNonstandard`: "Future" -> null

### `grassyseed`
- `isNonstandard`: null -> "Past"

### `greatball`
- `isNonstandard`: null -> "Past"

### `greninjite`
- `isNonstandard`: "Future" -> null

### `grepaberry`
- `isNonstandard`: null -> "Past"

### `gripclaw`
- `isNonstandard`: null -> "Past"

### `griseouscore`
- `isNonstandard`: null -> "Past"

### `griseousorb`
- `isNonstandard`: null -> "Past"

### `gyaradosite`
- `isNonstandard`: "Past" -> null

### `hawluchanite`
- `isNonstandard`: "Future" -> null

### `healball`
- `isNonstandard`: null -> "Past"

### `hearthflamemask`
- `isNonstandard`: null -> "Past"

### `heatranite`
- `isNonstandard`: "Future" -> "Past"

### `heavyball`
- `isNonstandard`: null -> "Past"

### `heavydutyboots`
- `isNonstandard`: null -> "Past"

### `heracronite`
- `isNonstandard`: "Past" -> null

### `hondewberry`
- `isNonstandard`: null -> "Past"

### `houndoominite`
- `isNonstandard`: "Past" -> null

### `iapapaberry`
- `isNonstandard`: null -> "Past"

### `icestone`
- `isNonstandard`: null -> "Past"

### `icicleplate`
- `isNonstandard`: null -> "Past"

### `insectplate`
- `isNonstandard`: null -> "Past"

### `ironplate`
- `isNonstandard`: null -> "Past"

### `jabocaberry`
- `isNonstandard`: null -> "Past"

### `kangaskhanite`
- `isNonstandard`: "Past" -> null

### `keeberry`
- `isNonstandard`: null -> "Past"

### `kelpsyberry`
- `isNonstandard`: null -> "Past"

### `laggingtail`
- `isNonstandard`: null -> "Past"

### `lansatberry`
- `isNonstandard`: null -> "Past"

### `leafstone`
- `isNonstandard`: null -> "Past"

### `levelball`
- `isNonstandard`: null -> "Past"

### `liechiberry`
- `isNonstandard`: null -> "Past"

### `loadeddice`
- `isNonstandard`: null -> "Past"

### `lopunnite`
- `isNonstandard`: "Past" -> null

### `loveball`
- `isNonstandard`: null -> "Past"

### `lovesweet`
- `isNonstandard`: null -> "Past"

### `lucarionite`
- `isNonstandard`: "Past" -> null

### `luminousmoss`
- `isNonstandard`: null -> "Past"

### `lureball`
- `isNonstandard`: null -> "Past"

### `lustrousglobe`
- `isNonstandard`: null -> "Past"

### `lustrousorb`
- `isNonstandard`: null -> "Past"

### `luxuryball`
- `isNonstandard`: null -> "Past"

### `magmarizer`
- `isNonstandard`: null -> "Past"

### `magoberry`
- `isNonstandard`: null -> "Past"

### `malamarite`
- `isNonstandard`: "Future" -> null

### `maliciousarmor`
- `isNonstandard`: null -> "Past"

### `manectite`
- `isNonstandard`: "Past" -> null

### `marangaberry`
- `isNonstandard`: null -> "Past"

### `masterball`
- `isNonstandard`: null -> "Past"

### `masterpieceteacup`
- `isNonstandard`: null -> "Past"

### `mawilite`
- `isNonstandard`: "Past" -> null

### `meadowplate`
- `isNonstandard`: null -> "Past"

### `medichamite`
- `isNonstandard`: "Past" -> null

### `meganiumite`
- `isNonstandard`: "Future" -> null

### `meowsticite`
- `isNonstandard`: "Future" -> null

### `metagrossite`
- `isNonstandard`: "Past" -> null

### `metalalloy`
- `isNonstandard`: null -> "Past"

### `micleberry`
- `isNonstandard`: null -> "Past"

### `mindplate`
- `isNonstandard`: null -> "Past"

### `mirrorherb`
- `isNonstandard`: null -> "Past"

### `mistyseed`
- `isNonstandard`: null -> "Past"

### `moonball`
- `isNonstandard`: null -> "Past"

### `moonstone`
- `isNonstandard`: null -> "Past"

### `nestball`
- `isNonstandard`: null -> "Past"

### `netball`
- `isNonstandard`: null -> "Past"

### `normalgem`
- `isNonstandard`: null -> "Past"

### `ovalstone`
- `isNonstandard`: null -> "Past"

### `parkball`
- `isNonstandard`: "Unobtainable" -> "Past"

### `petayaberry`
- `isNonstandard`: null -> "Past"

### `pidgeotite`
- `isNonstandard`: "Past" -> null

### `pinsirite`
- `isNonstandard`: "Past" -> null

### `pixieplate`
- `isNonstandard`: null -> "Past"

### `pokeball`
- `isNonstandard`: null -> "Past"

### `pomegberry`
- `isNonstandard`: null -> "Past"

### `poweranklet`
- `isNonstandard`: null -> "Past"

### `powerband`
- `isNonstandard`: null -> "Past"

### `powerbelt`
- `isNonstandard`: null -> "Past"

### `powerbracer`
- `isNonstandard`: null -> "Past"

### `powerherb`
- `isNonstandard`: null -> "Past"

### `powerlens`
- `isNonstandard`: null -> "Past"

### `powerweight`
- `isNonstandard`: null -> "Past"

### `premierball`
- `isNonstandard`: null -> "Past"

### `prettyfeather`
- `isNonstandard`: null -> "Past"

### `prismscale`
- `isNonstandard`: null -> "Past"

### `protectivepads`
- `isNonstandard`: null -> "Past"

### `protector`
- `isNonstandard`: null -> "Past"

### `psychicseed`
- `isNonstandard`: null -> "Past"

### `punchingglove`
- `isNonstandard`: null -> "Past"

### `pyroarite`
- `isNonstandard`: "Future" -> null

### `qualotberry`
- `isNonstandard`: null -> "Past"

### `quickball`
- `isNonstandard`: null -> "Past"

### `raichunitex`
- `isNonstandard`: "Future" -> null

### `raichunitey`
- `isNonstandard`: "Future" -> null

### `rarebone`
- `isNonstandard`: null -> "Past"

### `razorclaw`
- `isNonstandard`: null -> "Past"

### `razorfang`
- `isNonstandard`: null -> "Past"

### `reapercloth`
- `isNonstandard`: null -> "Past"

### `redcard`
- `isNonstandard`: null -> "Past"

### `repeatball`
- `isNonstandard`: null -> "Past"

### `ribbonsweet`
- `isNonstandard`: null -> "Past"

### `ringtarget`
- `isNonstandard`: null -> "Past"

### `rockyhelmet`
- `isNonstandard`: null -> "Past"

### `roomservice`
- `isNonstandard`: null -> "Past"

### `rowapberry`
- `isNonstandard`: null -> "Past"

### `rustedshield`
- `isNonstandard`: null -> "Past"

### `rustedsword`
- `isNonstandard`: null -> "Past"

### `sablenite`
- `isNonstandard`: "Past" -> null

### `safariball`
- `isNonstandard`: null -> "Past"

### `safetygoggles`
- `isNonstandard`: null -> "Past"

### `salacberry`
- `isNonstandard`: null -> "Past"

### `sceptilite`
- `isNonstandard`: "Past" -> null

### `scizorite`
- `isNonstandard`: "Past" -> null

### `scolipite`
- `isNonstandard`: "Future" -> null

### `scovillainite`
- `isNonstandard`: "Future" -> null

### `scraftinite`
- `isNonstandard`: "Future" -> null

### `sharpedonite`
- `isNonstandard`: "Past" -> null

### `shinystone`
- `isNonstandard`: null -> "Past"

### `skarmorite`
- `isNonstandard`: "Future" -> null

### `skyplate`
- `isNonstandard`: null -> "Past"

### `slowbronite`
- `isNonstandard`: "Past" -> null
- `shortDesc`: null -> "If held by a Slowbro (not Galarian Slowbro), this item allows it to Mega Evolve."

### `snowball`
- `isNonstandard`: null -> "Past"

### `souldew`
- `isNonstandard`: null -> "Past"

### `splashplate`
- `isNonstandard`: null -> "Past"

### `spookyplate`
- `isNonstandard`: null -> "Past"

### `sportball`
- `isNonstandard`: null -> "Past"

### `staraptite`
- `isNonstandard`: "Future" -> null

### `starfberry`
- `isNonstandard`: null -> "Past"

### `starminite`
- `isNonstandard`: "Future" -> null

### `starsweet`
- `isNonstandard`: null -> "Past"

### `steelixite`
- `isNonstandard`: "Past" -> null

### `stickybarb`
- `isNonstandard`: null -> "Past"

### `stoneplate`
- `isNonstandard`: null -> "Past"

### `strangeball`
- `isNonstandard`: "Unobtainable" -> "Past"

### `strawberrysweet`
- `isNonstandard`: null -> "Past"

### `sunstone`
- `isNonstandard`: null -> "Past"

### `swampertite`
- `isNonstandard`: "Past" -> null

### `sweetapple`
- `isNonstandard`: null -> "Past"

### `syrupyapple`
- `isNonstandard`: null -> "Past"

### `tamatoberry`
- `isNonstandard`: null -> "Past"

### `tartapple`
- `isNonstandard`: null -> "Past"

### `terrainextender`
- `isNonstandard`: null -> "Past"

### `throatspray`
- `isNonstandard`: null -> "Past"

### `thunderstone`
- `isNonstandard`: null -> "Past"

### `timerball`
- `isNonstandard`: null -> "Past"

### `toxicorb`
- `isNonstandard`: null -> "Past"

### `toxicplate`
- `isNonstandard`: null -> "Past"

### `tyranitarite`
- `isNonstandard`: "Past" -> null

### `ultraball`
- `isNonstandard`: null -> "Past"

### `unremarkableteacup`
- `isNonstandard`: null -> "Past"

### `upgrade`
- `isNonstandard`: null -> "Past"

### `utilityumbrella`
- `isNonstandard`: null -> "Past"

### `venusaurite`
- `isNonstandard`: "Past" -> null

### `victreebelite`
- `isNonstandard`: "Future" -> null

### `waterstone`
- `isNonstandard`: null -> "Past"

### `weaknesspolicy`
- `isNonstandard`: null -> "Past"

### `wellspringmask`
- `isNonstandard`: null -> "Past"

### `wikiberry`
- `isNonstandard`: null -> "Past"

### `zapplate`
- `isNonstandard`: null -> "Past"

## Abilities (8 modified)

### `dragonize`
- `isNonstandard`: "Future" -> null

### `eelevate`
- `isNonstandard`: "Future" -> null

### `firemane`
- `isNonstandard`: "Future" -> null

### `healer`
- `desc`: null -> "50% chance this Pokemon's ally has its non-volatile status condition cured at the end of each turn."
- `shortDesc`: null -> "50% chance this Pokemon's ally has its status cured at the end of each turn."

### `megasol`
- `isNonstandard`: "Future" -> null

### `piercingdrill`
- `isNonstandard`: "Future" -> null

### `spicyspray`
- `isNonstandard`: "Future" -> null

### `unseenfist`
- `shortDesc`: null -> "This Pokemon's contact moves ignore a target's protection and deal 1/4 the usual damage."
