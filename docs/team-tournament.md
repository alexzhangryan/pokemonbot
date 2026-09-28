# The team tournament

Generated from `runs/tourney-mc/results.json` (`scripts/team_tournament.py`, D98). Do not edit by hand.

Agent `belief` on both sides (D96 rows, D91 columns, D94 offsets), 8 games a pairing, local self-play, 2026-09-26 to 28. Candidates: the corpus's ten most-played Reg M-C teams as `data/teams/corpus-NN-*.txt` plus `regmc-mence`. Pool: the eight most-played. A candidate never plays itself.

| candidate | won | games | rate | 95% |
| --- | ---: | ---: | ---: | --- |
| `corpus-08-aerodactyl` | 35 | 56 | 0.62 | 0.49-0.74 |
| `corpus-04-arcaninehisui` | 32 | 56 | 0.57 | 0.44-0.69 |
| `corpus-05-arcaninehisui` | 30 | 56 | 0.54 | 0.41-0.66 |
| `corpus-03-excadrill` | 28 | 56 | 0.50 | 0.37-0.63 |
| `corpus-10-archaludon` | 32 | 64 | 0.50 | 0.38-0.62 |
| `regmc-mence` | 32 | 64 | 0.50 | 0.38-0.62 |
| `corpus-02-arcaninehisui` | 27 | 56 | 0.48 | 0.36-0.61 |
| `corpus-01-floetteeternal` | 26 | 56 | 0.46 | 0.34-0.59 |
| `corpus-07-floetteeternal` | 22 | 56 | 0.39 | 0.28-0.52 |
| `corpus-09-archaludon` | 21 | 64 | 0.33 | 0.23-0.45 |
| `corpus-06-ceruledge` | 13 | 56 | 0.23 | 0.14-0.36 |

Mirror self-play answers which team this agent plays best against the teams it will meet, not which team is best; a team whose tools a one-ply opponent handles badly (Trick Room, Tailwind) can score above what it will against people. `regmc-aero` (corpus-08) also has the corpus's third-best human record among teams seen 20 or more times, 14-9.
