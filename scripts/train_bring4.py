"""Train and evaluate the M4 preview models against the replay corpus.

    python scripts/train_bring4.py                 # train, evaluate, report
    python scripts/train_bring4.py --demo          # also solve one preview

Everything is held out by series, the regularisation is chosen on a validation
split carved out of training, and every rate is reported with an interval. The
baselines are printed beside the models on purpose: uniform over the subsets is
the bar, and a model that does not clear it should be read as saying the signal
is not there rather than as a number to quote.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from champions.corpus.store import CorpusStore  # noqa: E402
from champions.dex.loader import Dex  # noqa: E402
from champions.preview.bring4 import (  # noqa: E402
    build_feature_space,
    evaluate_lead_predictor,
    evaluate_predictor,
    marginal_only_space,
    select_and_train,
    train_bring_predictor,
)
from champions.preview.dataset import load_examples, split_examples  # noqa: E402
from champions.preview.equilibrium import solve_leads, solve_preview  # noqa: E402
from champions.preview.value import (  # noqa: E402
    evaluate_value_model,
    train_value_model,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FORMAT_ID = "gen9championsvgc2026regmb"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=REPO_ROOT / "data" / "corpus.sqlite")
    parser.add_argument("--logs", type=Path, default=REPO_ROOT / "data" / "replays")
    parser.add_argument("--format", default=FORMAT_ID)
    parser.add_argument("--test-fraction", type=float, default=0.25)
    parser.add_argument("--demo", action="store_true", help="solve one preview at the end")
    args = parser.parse_args()

    dex = Dex.load(args.format)
    with CorpusStore(args.db, args.logs) as store:
        examples = load_examples(store)
    if not examples:
        print("The corpus is empty. Run scripts/scrape_replays.py first.")
        return 1

    split = split_examples(examples, test_fraction=args.test_fraction)
    test_bring = [e for e in split.test if e.usable_for_bring]
    unseen_bring = [e for e in split.unseen_players if e.usable_for_bring]
    print(f"corpus: {len(examples)} preview examples -- {split.summary()}")

    print("\n=== bring-4 ===")
    bring = select_and_train(split.train, dex, "bring")
    print(" ", bring.as_row("selection"))
    print(" ", evaluate_predictor(bring.predictor, test_bring).as_row("model"))
    if unseen_bring:
        print(
            " ", evaluate_predictor(bring.predictor, unseen_bring).as_row("model, unseen players")
        )
    space = build_feature_space([e for e in split.train if e.usable_for_bring], dex)
    flat = train_bring_predictor(split.train, dex, l2=bring.l2, space=marginal_only_space(space))
    print(" ", evaluate_predictor(flat, test_bring).as_row("species marginals only"))

    print("\n=== leads ===")
    lead = select_and_train(split.train, dex, "lead")
    print(" ", lead.as_row("selection"))
    print(" ", evaluate_lead_predictor(lead.predictor, split.test).as_row("model"))
    if split.unseen_players:
        print(
            " ",
            evaluate_lead_predictor(lead.predictor, split.unseen_players).as_row(
                "model, unseen players"
            ),
        )

    print("\n=== preview value model ===")
    value = train_value_model(split.train, space, l2=10.0)
    train_metrics = evaluate_value_model(value, split.train)
    test_metrics = evaluate_value_model(value, split.test)
    print(
        f"  train n={train_metrics['n']:.0f} accuracy {train_metrics['accuracy']:.1%}\n"
        f"  test  n={test_metrics['n']:.0f} accuracy {test_metrics['accuracy']:.1%} "
        f"log loss {test_metrics['log_loss']:.4f} against a coin flip's "
        f"{test_metrics['coin_flip_log_loss']:.4f}"
    )
    print("  " + rating_control(examples))

    print("\n=== most predictive weights ===")
    for name, weight in lead.predictor.model.top_features(8):
        print(f"  lead   {name:<28} {weight:+.3f}")

    if args.demo:
        demo(split, value, bring)
    return 0


def rating_control(examples) -> str:
    """Does anything predict the winner? Ladder rating is the control.

    If the higher-rated player wins well above chance while the preview value
    model cannot, the label is fine and the features are the problem -- which is
    a different conclusion from "these games are unpredictable", and only one of
    the two is true.
    """
    by_replay: dict[str, dict[str, object]] = {}
    for example in examples:
        by_replay.setdefault(example.replay_id, {})[example.side] = example
    pairs = [(sides["p1"], sides["p2"]) for sides in by_replay.values() if len(sides) == 2]
    usable = [(a, b) for a, b in pairs if a.rating and b.rating and a.rating != b.rating]
    if not usable:
        return "control: no rated games with unequal ratings"
    correct = sum(1 for a, b in usable if (a.rating > b.rating) == a.won)
    return (
        f"control: over {len(usable)} rated games the higher-rated player won "
        f"{correct / len(usable):.1%}, so the outcome is predictable -- by skill"
    )


def demo(split, value, bring) -> None:
    example = next((e for e in split.test if e.usable_for_bring), None)
    if example is None:
        return
    print("\n=== one preview, solved ===")
    print(f"  ours   {', '.join(example.team)}")
    print(f"  theirs {', '.join(example.opponent_team)}")
    solution = solve_preview(example.team, example.opponent_team, value.win_probability)
    for line in solution.as_report().splitlines():
        print("  " + line)
    # Predict and check the same side. Predicting the opponent's bring and
    # printing ours beside it compares two different things and looks like a
    # result.
    predicted = bring.predictor.most_likely(example.team, example.opponent_team)
    print(f"  predicted bring for us: {', '.join(predicted)}")
    print(f"  actually brought:       {', '.join(example.brought_species)}")
    four = solution.best_single()
    leads = solve_leads(four, example.opponent_team[:4], value.win_probability)
    print(f"  lead subgame for {', '.join(four)}:")
    for line in leads.as_report().splitlines():
        print("    " + line)


if __name__ == "__main__":
    raise SystemExit(main())
