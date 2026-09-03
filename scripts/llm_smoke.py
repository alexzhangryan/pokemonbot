"""End-to-end smoke of the language-model candidate loop against local Ollama.

    python scripts/llm_smoke.py                       # default model
    CHAMPIONS_LLM_MODEL=deepseek-r1:latest python scripts/llm_smoke.py
    python scripts/llm_smoke.py --no-cache            # force a fresh call

This exercises the whole decision half of implementation C -- build a prompt from
candidates that already carry their computed numbers, call the model, parse the
reply back into an ordering -- *without* the built simulator, the dex, poke-env or
a trace directory. It is the cheapest proof that the pipeline runs before any of
the heavier machinery is stood up, and before a paid API is wired in.

The candidate briefs here are hand-written to look exactly like the ones
`champions.search.language.LanguagePolicy` builds off a real `Board`: a label, the
per-move damage the engine computed, and the heuristic's own verdict in brackets.
The model's job is only to order them. A sensible model puts the guaranteed
knockout first and the idle Protect last.
"""

from __future__ import annotations

import argparse
import os
import time

from champions.search import llm

#: A plausible Reg M-B doubles turn, already reduced to computed consequences.
#: Incineroar + Flutter Mane out; the opponent has a weakened Amoonguss and a
#: healthy Flutter Mane. The "right" ordering is not subtle -- a KO and a strong
#: hit lead, the idle Protect trails -- which is what makes it a smoke test.
HEADER = (
    "Turn 3. Your active: Incineroar 78%, Flutter Mane 100%. "
    "Opponent active: Amoonguss 41%, Flutter Mane 63%. Field: your tailwind up."
)

BRIEFS = [
    "Flutter Mane Moonblast: 68% to foe Flutter Mane [attack]",
    "Flutter Mane Moonblast: 100% to foe Amoonguss KO [knockout]",
    "Incineroar Flare Blitz: 52% to foe Amoonguss [attack]",
    "Incineroar Knock Off: 47% to foe Flutter Mane [attack]",
    "Incineroar Fake Out: 9% to foe Flutter Mane [fake out]",
    "Incineroar Protect (status) [protect idle]",
    "bring Rillaboom",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=None, help="override CHAMPIONS_LLM_MODEL")
    parser.add_argument("--host", default=None, help="override OLLAMA_HOST")
    parser.add_argument(
        "--no-cache", action="store_true", help="do not read or write the reply cache"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    # Route --model/--host through the env so `client_from_env` is the one place
    # a client is built, exactly as the guard and the agent build theirs.
    if args.model:
        os.environ["CHAMPIONS_LLM_MODEL"] = args.model
    if args.host:
        os.environ["OLLAMA_HOST"] = args.host
    client = llm.client_from_env(cache=not args.no_cache)

    print(f"model: {client.name}\n")
    prompt = llm.build_prompt(HEADER, BRIEFS)
    print("=== prompt ===")
    print(prompt)
    print()

    started = time.perf_counter()
    try:
        reply = client.complete(prompt)
    except llm.LLMError as error:
        raise SystemExit(f"\nLLM call failed: {error}") from error
    elapsed = time.perf_counter() - started

    print(f"=== raw reply ({elapsed:.1f}s) ===")
    print(reply.strip() or "(empty)")
    print()

    ranking = llm.parse_ranking(reply, len(BRIEFS))
    order = llm.order_indices(ranking, len(BRIEFS))
    print("=== parsed ranking ===")
    print(f"model ordered: {ranking}")
    print(f"completed to a full order (heuristic fills the rest): {order}")
    print()
    print("=== candidates, best first ===")
    for rank_position, index in enumerate(order, start=1):
        source = "model" if index in ranking else "heuristic-fallback"
        print(f"{rank_position:>2}. [{source}] {BRIEFS[index]}")

    if not ranking:
        print(
            "\nThe model returned nothing parseable -- the provider would fall back to the "
            "heuristic ordering here, which is the honest floor for C."
        )


if __name__ == "__main__":
    main()
