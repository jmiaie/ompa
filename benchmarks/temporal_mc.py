"""
Temporal knowledge-graph correctness Monte Carlo (synthetic, 20 seeds).

Each trial gives 30 people 2-5 consecutive project assignments
(``add_triple`` + ``invalidate``); 35% of assignments re-assert the previous
project after a gap.  400 random point-in-time ``query_entity(as_of=...)``
calls per trial are checked against the ground-truth intervals.

Reports mean accuracy with a bootstrap 95% CI and the fraction of validity
intervals lost (rows stored vs true intervals).  OMPA <= 1.0.8: 0.895
accuracy, 36% intervals lost; since #16: 1.000 and 0%.

Usage:
    python benchmarks/temporal_mc.py [--seeds 20]
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import tempfile
from datetime import date, timedelta

from ompa.knowledge_graph import KnowledgeGraph


def trial(seed: int, n_ent: int = 30) -> dict:
    rng = random.Random(seed)
    with tempfile.TemporaryDirectory() as tmp:
        db = f"{tmp}/kg.sqlite3"
        kg = KnowledgeGraph(db_path=db)
        base = date(2020, 1, 1)
        truth = []
        projects = [f"proj{i}" for i in range(8)]
        for e in range(n_ent):
            s = f"person{e}"
            t = base + timedelta(days=rng.randint(0, 100))
            prev = None
            for _ in range(rng.randint(2, 5)):
                obj = rng.choice(projects) if (prev is None or rng.random() > 0.35) else prev
                end = t + timedelta(days=rng.randint(30, 400))
                kg.add_triple(s, "works_on", obj, valid_from=t.isoformat())
                kg.invalidate(s, "works_on", obj, ended=end.isoformat())
                truth.append((s, obj, t, end))
                prev = obj
                t = end + timedelta(days=rng.randint(1, 120))
        ok = 0
        n_q = 400
        for _ in range(n_q):
            s = f"person{rng.randrange(n_ent)}"
            q = base + timedelta(days=rng.randint(0, 2500))
            exp = {o for (ss, o, a, b) in truth if ss == s and a <= q <= b}
            got = {
                t.object
                for t in kg.query_entity(s, as_of=q.isoformat())
                if t.predicate == "works_on"
            }
            ok += exp == got
        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT COUNT(*) FROM triples WHERE predicate='works_on'").fetchone()[0]
        conn.close()
    return {"seed": seed, "accuracy": ok / n_q, "facts_true": len(truth), "rows_stored": rows}


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--seeds", type=int, default=20)
    args = ap.parse_args()
    trials = [trial(s) for s in range(1, args.seeds + 1)]
    acc = [t["accuracy"] for t in trials]
    rng = random.Random(20261020)
    boots = sorted(sum(rng.choice(acc) for _ in acc) / len(acc) for _ in range(10_000))
    lost = sum(1 - t["rows_stored"] / t["facts_true"] for t in trials) / len(trials)
    print(
        json.dumps(
            {
                "seeds": args.seeds,
                "mean_accuracy": round(sum(acc) / len(acc), 4),
                "ci95": [round(boots[249], 4), round(boots[9749], 4)],
                "min_accuracy": min(acc),
                "fraction_intervals_lost": round(lost, 4),
            }
        )
    )


if __name__ == "__main__":
    main()
