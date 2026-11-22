"""
Latency of MCP tool calls on a reference vault (ROADMAP L3).

The MCP server builds a fresh ``Ompa`` for every tool call, so what an agent
waits for is the whole call, not the search or the query inside it.  This
script times ``ompa.mcp_server.handle_call_tool`` (the function the stdio
loop calls) for the tools an agent uses most, on a synthetic vault:

* ``notes``: N notes of ~250 Zipf-distributed words with frontmatter, tags
  and wikilinks, spread over the standard folders (default 1,000 and 10,000).
* ``sessions``: 50 notes of ~1,700 words, the shape of one LongMemEval-S
  haystack (one note per conversation session).

One word in six is a common English function word ("the", "what", "my" ...),
which is fewer than in real prose, and the query is a question that uses
them.  That matters: a question's function words match nearly every note, so
the number of matching notes is the size of the vault, as it is for a real
agent's query.

``ao_search`` is timed three ways, because the server checks the vault for
notes edited outside OMPA at most once per ``OMPA_INDEX_REFRESH_SECONDS``
(default 1 s):

* ``ao_search``            a search with no other search in the last second
                           (includes that check: a walk of the vault)
* ``ao_search (burst)``    searches that follow one another within the second
* ``Ompa.search``          the same query on a resident instance, for reference

Without ``ompa[semantic]`` installed, ``ao_search`` is BM25 only; with it, the
model is loaded before timing starts and each search adds its query encoding.
The header line says which was measured.

Usage:
    pip install numpy
    python benchmarks/mcp_latency.py                      # 1k and 10k notes, sessions
    python benchmarks/mcp_latency.py --sizes 1000 --rounds 15
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import shutil
import statistics
import tempfile
import time
from pathlib import Path

import numpy as np

import ompa
from ompa import Ompa, mcp_server

SEED = 20261122
VOCAB = [f"w{i:05d}" for i in range(20000)]
COMMON = "the i to a and you of my for it is in that what did have with on was me we about".split()  # noqa: SIM905
FOLDERS = ["brain", "work/active", "work/archive", "org/people", "org/teams", "perf"]


def note_text(i: int, words: int, rng: np.random.Generator) -> str:
    tokens = [VOCAB[j] for j in rng.zipf(1.25, words) % len(VOCAB)]
    for k, c in zip(range(0, words, 6), rng.integers(0, len(COMMON), words), strict=False):
        tokens[k] = COMMON[int(c)]
    body = " ".join(tokens)
    links = f"[[note-{(i * 7) % (i + 1)}]] [[note-{(i * 13 + 5) % (i + 1)}]]"
    return (
        f"---\ndate: 2026-{1 + i % 12:02d}-{1 + i % 28:02d}\ntags: [t{i % 13}, area{i % 5}]\n"
        f"type: note\n---\n# Note {i}\n\n{body}\n\nSee {links}. uniq{i:06d}\n"
    )


def build_vault(root: Path, n: int, words: int) -> None:
    rng = np.random.default_rng(SEED + n)
    for folder in FOLDERS:
        (root / folder).mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (root / FOLDERS[i % len(FOLDERS)] / f"note-{i}.md").write_text(
            note_text(i, words, rng), encoding="utf-8"
        )


def timed(fn, rounds: int, before=None) -> dict:
    samples = []
    for _ in range(rounds):
        if before is not None:
            before()
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    samples.sort()
    return {
        "p50_ms": round(statistics.median(samples), 2),
        "p95_ms": round(samples[min(len(samples) - 1, int(len(samples) * 0.95))], 2),
    }


def bench(label: str, n: int, words: int, rounds: int) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ompa_mcp_bench_"))
    previous = os.environ.get("OMPA_VAULT_PATH")
    try:
        vault = work / "vault"
        build_vault(vault, n, words)
        os.environ["OMPA_VAULT_PATH"] = str(vault)
        resident = Ompa(vault_path=str(vault), enable_semantic=True)
        resident.rebuild_index()
        resident.kg.populate_from_vault(vault)

        def call(name: str, **arguments) -> dict:
            result = mcp_server.handle_call_tool(name, {"vault_path": str(vault), **arguments})
            if "error" in result:
                raise RuntimeError(f"{name}: {result}")
            return result

        clock = getattr(mcp_server, "_REFRESH_CLOCK", None)  # absent before 1.2.1

        def expire() -> None:
            if clock is not None:
                clock.clear()

        target = n // 2
        query = f"what did we decide about the w00012 plan for my w00340 uniq{target:06d}"
        hit = call("ao_search", query=query, limit=5)
        found = any(f"note-{target}.md" in str(r) for r in hit.get("results", []))
        slow = max(3, rounds // 3)  # the calls that read the whole vault
        counter = iter(range(10**9))
        rows = {
            "ao_search": timed(lambda: call("ao_search", query=query, limit=5), rounds, expire),
            "ao_search (burst)": timed(lambda: call("ao_search", query=query, limit=5), rounds),
            "Ompa.search": timed(lambda: resident.search(query, 5), rounds),
            "ao_session_start": timed(lambda: call("ao_session_start"), slow),
            "ao_status": timed(lambda: call("ao_status"), slow),
            "ao_orphans": timed(lambda: call("ao_orphans"), slow),
            "ao_wrap_up": timed(lambda: call("ao_wrap_up"), slow),
            "ao_kg_query": timed(lambda: call("ao_kg_query", entity=f"note-{target}"), rounds),
            "ao_kg_stats": timed(lambda: call("ao_kg_stats"), rounds),
            "ao_classify": timed(
                lambda: call("ao_classify", message="We decided to go with Postgres"), rounds
            ),
            "ao_write": timed(
                lambda: call(
                    "ao_write",
                    content=f"Decision {next(counter)}: keep the w00012 plan for uniq{target:06d}",
                    note_type="brain",
                ),
                rounds,
            ),
        }
        return {"vault": label, "notes": n, "words_per_note": words, "target_found": found, **rows}
    finally:
        if previous is None:
            os.environ.pop("OMPA_VAULT_PATH", None)
        else:
            os.environ["OMPA_VAULT_PATH"] = previous
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    logging.disable(logging.WARNING)
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--sizes", type=int, nargs="*", default=[1000, 10000])
    ap.add_argument("--rounds", type=int, default=21)
    ap.add_argument("--no-sessions", action="store_true", help="skip the 50 x 1,700-word vault")
    args = ap.parse_args()

    try:
        import sentence_transformers  # noqa: F401

        mode = "hybrid (BM25 + sentence-transformers)"
    except ImportError:
        mode = "keyword only (BM25; ompa[semantic] not installed)"
    print(
        json.dumps(
            {
                "ompa": ompa.__version__,
                "search_mode": mode,
                "python": platform.python_version(),
                "cpus": os.cpu_count(),
                "rounds": args.rounds,
            }
        ),
        flush=True,
    )
    for n in args.sizes:
        print(json.dumps(bench("notes", n, 250, args.rounds)), flush=True)
    if not args.no_sessions:
        print(json.dumps(bench("sessions", 50, 1700, args.rounds)), flush=True)


if __name__ == "__main__":
    main()
