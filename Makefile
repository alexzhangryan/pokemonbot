# Convenience wrappers around scripts/. See docs/QUICKSTART.md for the full
# walkthrough; this exists so the common commands don't need to be retyped.
#
# Requires: the venv at .venv/ (`make venv` creates it), GNU Make (`winget
# install ezwinports.make`), Node.js, and vendor/showdown built (`make vendor`).
# Works from PowerShell or Git Bash.

# Windows venvs put the interpreter under Scripts/, POSIX ones under bin/.
ifeq ($(OS),Windows_NT)
PYTHON := .venv/Scripts/python.exe
else
PYTHON := .venv/bin/python
endif
FORMAT_ID := gen9championsvgc2026regmc
PORT ?= 8090
GAMES ?= 50
SEED ?= 0
TRACES ?= traces
VIEWER_PORT ?= 8100
EVAL_TRACES ?= runs/m6-selfplay

.PHONY: help venv install vendor dex test lint format typecheck check \
        server play selfplay ladder bench differential trace viewer clean-traces \
        scrape scrape-full corpus priors eval-belief eval-games fit-eval fit-policy discard \
        llm-smoke discard-llm gate review calibrate-coach ladder-live viewer-live ladder-summary

help:
	@echo "make venv          create .venv and install dependencies"
	@echo "make vendor        clone and build vendor/showdown at the pinned commit"
	@echo "make dex           build the resolved Champions dex + mainline delta"
	@echo ""
	@echo "make test          run the test suite"
	@echo "make lint          ruff check"
	@echo "make format        ruff format"
	@echo "make typecheck     mypy"
	@echo "make check         lint + typecheck + test"
	@echo ""
	@echo "make viewer        open the viewer; it starts the simulator and runs everything"
	@echo ""
	@echo "make server        start the local Showdown server (PORT=$(PORT))"
	@echo "make play          run a bot that waits for a human challenge (AGENT=belief|oneply|greedy|random)"
	@echo "make selfplay      run self-play games (GAMES=$(GAMES))"
	@echo "make ladder        evaluate random vs max-base-power (GAMES=$(GAMES), SEED=$(SEED))"
	@echo "make bench         benchmark the simulator, writes docs/benchmarks.md"
	@echo "make differential  check simulator determinism over random positions (GAMES=1000)"
	@echo "make trace         show the most recent trace (TRACE=path to pick one)"
	@echo ""
	@echo "make eval-games    generate self-play for the M6 fit (EVAL_GAMES=750; hours)"
	@echo "make fit-eval      fit the evaluation function, write its reliability diagram"
	@echo "make fit-policy    fit the learned candidate prior, write its recall table"
	@echo "make discard       measure what candidate pruning throws away"
	@echo "make llm-smoke     exercise the language-model provider (C) against local Ollama"
	@echo "make discard-llm   run the pruning guard on C only (LIMIT=$(or $(LIMIT),20), needs Ollama)"
	@echo "make gate          run the M8 engine gate (GATE_GAMES=$(or $(GATE_GAMES),200) per arm per team)"
	@echo "make review        review a game with the coach (GAME=trace .jsonl, replay .log, or replay id/URL)"
	@echo "make calibrate-coach  fit the coach's label bands and check its loss against rating (CAL_GAMES=80)"
	@echo "make ladder-live   play rated games on the official ladder with adaptive-belief, the coach between games (LIVE_GAMES=10; account in .env)"
	@echo "make viewer-live   watch the live ladder games as they are played (runs/live/)"
	@echo "make ladder-summary  the record so far from the live ladder's ledger"
	@echo ""
	@echo "make scrape        fetch new replays for both formats (incremental)"
	@echo "make scrape-full   backfill the Bo3 corpus to exhaustion (hours)"
	@echo "make corpus        report what the corpus currently holds"
	@echo "make priors        distil the corpus into the belief filter's set prior"
	@echo ""
	@echo "make eval-belief   measure the belief filter (TRACES=$(TRACES) TEAM=regmb-beta)"
	@echo ""
	@echo "make clean-traces  remove traces/ and runs/"

# -- setup -----------------------------------------------------------------

ifeq ($(OS),Windows_NT)
SYSTEM_PYTHON := python
else
SYSTEM_PYTHON := python3
endif

venv:
	$(SYSTEM_PYTHON) -m venv .venv
	$(PYTHON) -m pip install -e ".[dev]"

vendor:
	git clone https://github.com/smogon/pokemon-showdown.git vendor/showdown
	cd vendor/showdown && git checkout "$$(cat ../SHOWDOWN_COMMIT)" && npm install && node build

dex:
	$(PYTHON) scripts/build_dex.py $(FORMAT_ID) --delta

# -- quality -----------------------------------------------------------------

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

typecheck:
	$(PYTHON) -m mypy .

check: lint typecheck test

# -- running -----------------------------------------------------------------

server:
	$(PYTHON) scripts/run_local_server.py $(PORT)

play:
	$(PYTHON) scripts/play_human.py --agent $(or $(AGENT),greedy) --port $(PORT)

selfplay:
	$(PYTHON) scripts/selfplay.py $(GAMES) --port $(PORT) --seed $(SEED)

ladder:
	$(PYTHON) scripts/run_ladder.py $(GAMES) --port $(PORT) --seed $(SEED)

bench:
	$(PYTHON) scripts/bench.py

differential:
	$(PYTHON) scripts/differential.py $(or $(GAMES),1000) --seed $(SEED)

trace:
	$(PYTHON) scripts/show_trace.py $(TRACE)

viewer:
	$(PYTHON) scripts/viewer.py $(TRACES) --port $(VIEWER_PORT)

# -- cleanup -----------------------------------------------------------------

clean-traces:
	rm -rf traces runs

scrape:
	$(PYTHON) scripts/scrape_replays.py

scrape-full:
	$(PYTHON) scripts/scrape_replays.py --format $(FORMAT_ID)bo3 --full

corpus:
	$(PYTHON) scripts/scrape_replays.py --stats

priors:
	$(PYTHON) scripts/build_priors.py

eval-belief:
	$(PYTHON) scripts/eval_belief.py traces --trace-dir $(TRACES) --team $(or $(TEAM),regmb-beta)

# M6. `eval-games` generates the self-play the fit reads; it is separate because
# it takes hours and `fit-eval` takes seconds, and the fit is the part that gets
# re-run. Both sides of one team on purpose: a head-to-head between different
# teams measures the teams (D30), and here the distribution of positions is the
# product, not the win rate.
eval-games:
	$(PYTHON) scripts/selfplay.py $(or $(EVAL_GAMES),750) --port $(PORT) 	  --trace-dir $(EVAL_TRACES) --seed $(SEED) 	  --agent-a $(or $(EVAL_AGENT),oneply) --agent-b $(or $(EVAL_AGENT),oneply) 	  --team-a regmb-alpha --team-b regmb-alpha

fit-eval:
	$(PYTHON) scripts/fit_eval.py --traces $(EVAL_TRACES)

# The pruning guard `docs/04-decision-engine.md` section 3 requires. Reads the
# same self-play traces the fit does and rebuilds the unpruned game at every
# decision, so it is minutes rather than seconds. Run it after `fit-eval`: the
# payoffs it measures come from the shipping evaluation weights.
# M7, implementation B. Reads the replay corpus rather than the traces, so it
# needs `make scrape` to have run and nothing else. About four minutes: most of
# it is reconstructing each player's view from the logs, not the fit.
fit-policy:
	$(PYTHON) scripts/fit_policy.py --json data/policy/fit.$(FORMAT_ID).json

discard:
	$(PYTHON) scripts/discard_rate.py --traces $(EVAL_TRACES) --json data/eval/discard.$(FORMAT_ID).json

# M7, implementation C: the language-model provider, mocked with a local Ollama
# model (D68). `llm-smoke` proves the prompt -> model -> parse loop end to end
# with no simulator, dex or traces. `discard-llm` measures C on the guard the same
# way A and B are measured, but only C and on a sample (LIMIT), because it calls a
# model once per position; it prints rather than writing docs/pruning-guard.md so
# a partial C run cannot clobber the committed A/B numbers. Both need `ollama
# serve` running and the model pulled (`ollama pull qwen2.5:3b-instruct`).
llm-smoke:
	$(PYTHON) scripts/llm_smoke.py

discard-llm:
	$(PYTHON) scripts/discard_rate.py --traces $(EVAL_TRACES) --policy language-model \
	  --limit $(or $(LIMIT),20) --no-report

# M8, the engine gate (`docs/specs/2026-09-13-engine-gate.md`, D70). Four arms
# against `oneply` in a mirror match on each checked-in team, the rule applied
# mechanically, and `docs/engine-gate.md` written. Starts its own Showdown
# server on PORT. Hours at the default; `GATE_GAMES=20` is a smoke run, and an
# interrupted run continues with `make gate GATE_ARGS=--resume`.
gate:
	$(PYTHON) scripts/engine_gate.py --games $(or $(GATE_GAMES),200) --port $(PORT) \
	  --seed $(SEED) $(GATE_ARGS)

# M9, the coach (`docs/specs/2026-09-13-coach.md`, D76). Re-solves every turn
# of a finished game offline and writes the analysis overlay beside it. GAME
# defaults to the newest trace under traces/; a replay id or URL is fetched.
# `REVIEW_ARGS="--side alice --opponent-team data/teams/regmb-alpha.txt --llm"`.
review:
	$(PYTHON) scripts/review.py $(or $(GAME),$(TRACES)) $(REVIEW_ARGS)

# The coach's calibration (`docs/06-coach-and-evaluation.md` sections 2 and 8,
# D77): the label bands fitted on the top rating quartile, and whether ex-ante
# loss tracks rating where ex-post loss does not. Needs the corpus (`make
# scrape`). About a second a turn; CAL_GAMES games from both sides.
calibrate-coach:
	$(PYTHON) scripts/calibrate_coach.py --limit $(or $(CAL_GAMES),80) --seed $(SEED) $(CAL_ARGS)

# The official ladder. Needs a registered account in .env (see .env.example);
# one battle at a time, the coach reviewing each before the next is searched,
# traces under runs/live/. `LIVE_ARGS="--agent adaptive --no-review"`.
ladder-live:
	$(PYTHON) scripts/ladder_live.py $(or $(LIVE_GAMES),10) --team $(or $(TEAM),regmb-worlds) $(LIVE_ARGS)

# The viewer on the live ladder's traces, in a second terminal while
# `make ladder-live` plays. No local simulator: the games are on the official
# server, and the viewer only tails the files the bot writes.
viewer-live:
	$(PYTHON) scripts/viewer.py $(or $(LIVE_TRACES),runs/live) --no-server --port $(VIEWER_PORT) $(VIEWER_ARGS)

ladder-summary:
	$(PYTHON) scripts/ladder_live.py --summary $(LIVE_ARGS)
