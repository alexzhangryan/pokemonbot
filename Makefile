# Convenience wrappers around scripts/. See docs/QUICKSTART.md for the full
# walkthrough; this exists so the common commands don't need to be retyped.
#
# Requires: the venv at .venv/ (`make venv` creates it), GNU Make (`winget
# install ezwinports.make`), Node.js, and vendor/showdown built (`make vendor`).
# Works from PowerShell or Git Bash.

PYTHON := .venv/Scripts/python.exe
FORMAT_ID := gen9championsvgc2026regmb
PORT ?= 8090
GAMES ?= 50
SEED ?= 0
TRACES ?= traces
VIEWER_PORT ?= 8100

.PHONY: help venv install vendor dex test lint format typecheck check \
        server play selfplay ladder bench differential trace viewer clean-traces \
        scrape scrape-full corpus

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
	@echo "make play          run a bot that waits for a human challenge (AGENT=greedy|random)"
	@echo "make selfplay      run self-play games (GAMES=$(GAMES))"
	@echo "make ladder        evaluate random vs max-base-power (GAMES=$(GAMES), SEED=$(SEED))"
	@echo "make bench         benchmark the simulator, writes docs/benchmarks.md"
	@echo "make differential  check simulator determinism over random positions (GAMES=1000)"
	@echo "make trace         show the most recent trace (TRACE=path to pick one)"
	@echo ""
	@echo "make scrape        fetch new replays for both formats (incremental)"
	@echo "make scrape-full   backfill the Bo3 corpus to exhaustion (hours)"
	@echo "make corpus        report what the corpus currently holds"
	@echo ""
	@echo "make clean-traces  remove traces/ and runs/"

# -- setup -----------------------------------------------------------------

venv:
	python -m venv .venv
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
