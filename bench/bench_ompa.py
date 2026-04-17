"""
OMPA benchmark suite.

Generates a synthetic 1000-note vault (brain/, work/active/, work/archive/,
org/people/) with wikilinks and measures KPIs for vault, KG, palace, semantic,
MCP, and full-sync paths. Results saved to bench/results.json.

Usage:
    python bench/bench_ompa.py
    python bench/bench_ompa.py --n 500 --semantic-n 200
"""
from __future__ import annotations

import argparse
import cProfile
import io
import json
import pstats
import random
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

# Make sure repo root is importable when this script is run directly.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ompa import Ompa, Vault, Palace, KnowledgeGraph, SemanticIndex  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic vault generation
# ---------------------------------------------------------------------------

PEOPLE = [f"Person{i}" for i in range(30)]
TOPICS = [
    "postgres", "auth", "cache", "latency", "index", "vault", "palace",
    "graph", "embedding", "search", "hook", "classifier", "memory",
    "sync", "config", "agent", "prompt", "context", "session", "tool",
]
TAGS = ["decision", "insight", "todo", "question", "pattern", "bug", "perf"]


def _rand_content(rng: random.Random, all_names: list[str]) -> str:
    n_links = rng.randint(1, 6)
    links = rng.sample(all_names, min(n_links, len(all_names)))
    tags = rng.sample(TAGS, rng.randint(1, 3))
    body_lines = []
    for _ in range(rng.randint(3, 10)):
        topic = rng.choice(TOPICS)
        linked = rng.choice(links)
        body_lines.append(
            f"Notes on {topic}: we decided to pursue [[{linked}]] — "
            f"see also {rng.choice(TOPICS)}."
        )
    body = "\n".join(body_lines)
    frontmatter_tags = ", ".join(tags)
    return (
        f"---\n"
        f"tags: [{frontmatter_tags}]\n"
        f"date: 2025-0{rng.randint(1,9)}-{rng.randint(10,28)}\n"
        f"---\n\n"
        f"# {rng.choice(TOPICS).title()}\n\n"
        f"{body}\n"
    )


def generate_vault(root: Path, n: int = 1000, seed: int = 42) -> dict:
    """Create a synthetic vault at ``root`` with roughly ``n`` notes."""
    rng = random.Random(seed)
    root.mkdir(parents=True, exist_ok=True)
    (root / "brain").mkdir(exist_ok=True)
    (root / "work" / "active").mkdir(parents=True, exist_ok=True)
    (root / "work" / "archive").mkdir(parents=True, exist_ok=True)
    (root / "org" / "people").mkdir(parents=True, exist_ok=True)

    # Split: 30% brain, 25% work/active, 25% work/archive, 20% org/people
    n_brain = int(n * 0.30)
    n_active = int(n * 0.25)
    n_archive = int(n * 0.25)
    n_people = n - n_brain - n_active - n_archive

    names: list[str] = []
    buckets: list[tuple[Path, str]] = []
    for i in range(n_brain):
        name = f"brain-{TOPICS[i % len(TOPICS)]}-{i:04d}"
        names.append(name)
        buckets.append((root / "brain" / f"{name}.md", name))
    for i in range(n_active):
        name = f"active-{TOPICS[i % len(TOPICS)]}-{i:04d}"
        names.append(name)
        buckets.append((root / "work" / "active" / f"{name}.md", name))
    for i in range(n_archive):
        name = f"archive-{TOPICS[i % len(TOPICS)]}-{i:04d}"
        names.append(name)
        buckets.append((root / "work" / "archive" / f"{name}.md", name))
    for i in range(n_people):
        person = PEOPLE[i % len(PEOPLE)]
        name = f"{person}-note-{i:04d}"
        names.append(name)
        buckets.append((root / "org" / "people" / f"{name}.md", name))

    for path, _name in buckets:
        path.write_text(_rand_content(rng, names), encoding="utf-8")

    return {"n_notes": len(buckets), "root": str(root)}


# ---------------------------------------------------------------------------
# Timing helpers
# ---------------------------------------------------------------------------

def time_ns(fn: Callable[[], Any]) -> tuple[int, Any]:
    t0 = time.perf_counter_ns()
    r = fn()
    return time.perf_counter_ns() - t0, r


def bench(fn: Callable[[], Any], runs: int = 3) -> dict:
    """Run fn ``runs`` times, return {mean_ms, min_ms, runs_ms, last_result}."""
    times_ns: list[int] = []
    result = None
    for _ in range(runs):
        dt, result = time_ns(fn)
        times_ns.append(dt)
    return {
        "runs": runs,
        "mean_ms": statistics.mean(times_ns) / 1e6,
        "min_ms": min(times_ns) / 1e6,
        "max_ms": max(times_ns) / 1e6,
        "runs_ms": [t / 1e6 for t in times_ns],
        "result_summary": _summarize(result),
    }


def _summarize(result: Any) -> Any:
    if isinstance(result, (int, float, str, bool)) or result is None:
        return result
    if isinstance(result, list):
        return {"type": "list", "len": len(result)}
    if isinstance(result, dict):
        return {k: _summarize(v) for k, v in list(result.items())[:10]}
    return str(type(result).__name__)


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------

def bench_vault(vault_root: Path) -> dict:
    results: dict[str, Any] = {}

    # Cold: fresh vault each time. Warm: same vault, cache hit.
    def cold_list():
        v = Vault(vault_root)
        return v.list_notes()

    v_shared = Vault(vault_root)
    # Prime the cache once.
    v_shared.list_notes()

    def warm_list():
        return v_shared.list_notes()

    results["list_notes_cold"] = bench(cold_list, runs=3)
    results["list_notes_warm"] = bench(warm_list, runs=3)

    # Cache hit rate: warm/cold mean ratio (lower = better cache).
    cold = results["list_notes_cold"]["mean_ms"]
    warm = results["list_notes_warm"]["mean_ms"]
    results["cache_speedup"] = cold / warm if warm > 0 else None

    results["find_orphans_warm"] = bench(lambda: v_shared.find_orphans(), runs=3)
    results["get_stats_warm"] = bench(lambda: v_shared.get_stats(), runs=3)

    return results


def bench_kg(vault_root: Path, tmp: Path) -> dict:
    results: dict[str, Any] = {}

    def populate():
        db = tmp / f"kg_{time.perf_counter_ns()}.sqlite3"
        kg = KnowledgeGraph(db_path=str(db))
        n = kg.populate_from_vault(vault_root)
        return n

    results["populate_from_vault"] = bench(populate, runs=3)

    # Now populate once for query benchmarks.
    db = tmp / "kg_query.sqlite3"
    if db.exists():
        db.unlink()
    kg = KnowledgeGraph(db_path=str(db))
    kg.populate_from_vault(vault_root)
    stats = kg.stats()
    results["kg_stats"] = stats

    # Pick entities that actually exist
    sample_entity = TOPICS[0].title()  # e.g. "Postgres" — but entities come from wikilinks/tags
    # Use a wikilink target we know exists — the generated names.
    sample_entity = "brain-postgres-0000"

    results["query_entity"] = bench(lambda: kg.query_entity(sample_entity), runs=3)
    results["query_relation"] = bench(
        lambda: kg.query_relation(sample_entity, "links_to"), runs=3
    )
    results["timeline"] = bench(lambda: kg.timeline(sample_entity), runs=3)

    return results


def bench_palace(vault_root: Path, tmp: Path) -> dict:
    results: dict[str, Any] = {}

    def build_no_batch():
        p = Palace(tmp / f"palace_nb_{time.perf_counter_ns()}")
        return p.auto_build_from_vault(vault_root)

    def build_with_batch():
        p = Palace(tmp / f"palace_b_{time.perf_counter_ns()}")
        with p.batch():
            return p.auto_build_from_vault(vault_root)

    results["auto_build_no_batch"] = bench(build_no_batch, runs=3)
    results["auto_build_with_batch"] = bench(build_with_batch, runs=3)

    nb = results["auto_build_no_batch"]["mean_ms"]
    wb = results["auto_build_with_batch"]["mean_ms"]
    results["batch_speedup"] = nb / wb if wb > 0 else None

    return results


def bench_semantic(vault_root: Path, tmp: Path, semantic_n: int | None) -> dict:
    results: dict[str, Any] = {}
    # Build a possibly-smaller vault for semantic (real model is slow).
    target_root = vault_root
    note_count_used = None
    if semantic_n is not None:
        small = tmp / "vault_semantic_small"
        if small.exists():
            shutil.rmtree(small)
        gen = generate_vault(small, n=semantic_n, seed=7)
        target_root = small
        note_count_used = gen["n_notes"]
    else:
        note_count_used = len(list(vault_root.rglob("*.md")))

    index_path = tmp / "semantic_index"
    if index_path.exists():
        shutil.rmtree(index_path)

    idx = SemanticIndex(index_path=index_path, embedding_dim=4)
    using_stub = False

    # Prefer the real model for realism; fall back to a tiny deterministic
    # stub (same pattern as tests/test_ompa.py) if sentence-transformers is
    # not installed or the model cannot load. This keeps the bench useful
    # even in minimal dev environments.
    try:
        idx._init_model()
    except Exception:
        idx._model = None
    if idx._model is None:
        import numpy as np

        class StubModel:
            def encode(self, texts, convert_to_numpy=True, show_progress_bar=False):
                single = isinstance(texts, str)
                items = [texts] if single else list(texts)
                out = np.zeros((len(items), 4), dtype=np.float32)
                for i, t in enumerate(items):
                    tl = t.lower()
                    out[i, 0] = tl.count("a") + 0.1
                    out[i, 1] = tl.count("e") + 0.1
                    out[i, 2] = tl.count("o") + 0.1
                    out[i, 3] = len(tl) % 7 + 0.1
                if single:
                    return out[0]
                return out

        idx._model = StubModel()
        idx._initialized = True
        using_stub = True

    t0 = time.perf_counter_ns()
    try:
        count = idx.index_vault(target_root)
    except Exception as e:
        results["error"] = f"index_vault failed: {e}"
        results["using_stub"] = using_stub
        return results
    build_ms = (time.perf_counter_ns() - t0) / 1e6
    results["using_stub"] = using_stub

    if count == 0:
        results["error"] = "index_vault returned 0"
        results["note_count_target"] = note_count_used
        return results

    idx.save_index()
    results["note_count_used"] = note_count_used
    results["indexed_chunks"] = count
    results["index_vault_ms"] = build_ms

    # 20 queries, record p50/p95
    queries = [
        f"what did we decide about {t}" for t in TOPICS[:20]
    ]
    lat_ns: list[int] = []
    for q in queries:
        t0 = time.perf_counter_ns()
        idx.search(q, limit=5, hybrid=True)
        lat_ns.append(time.perf_counter_ns() - t0)
    lat_ms = sorted(t / 1e6 for t in lat_ns)
    results["search_queries"] = len(queries)
    results["search_p50_ms"] = lat_ms[len(lat_ms) // 2]
    results["search_p95_ms"] = lat_ms[int(len(lat_ms) * 0.95) - 1]
    results["search_mean_ms"] = statistics.mean(lat_ms)
    results["search_min_ms"] = min(lat_ms)
    results["search_max_ms"] = max(lat_ms)

    return results


def bench_mcp(vault_root: Path) -> dict:
    from ompa import mcp_server as mcp

    results: dict[str, Any] = {}

    mcp._clear_ompa_cache()

    def miss():
        mcp._clear_ompa_cache()
        return mcp._get_ompa(vault_path=str(vault_root), enable_semantic=False)

    def hit():
        return mcp._get_ompa(vault_path=str(vault_root), enable_semantic=False)

    # Prime once so hit() hits
    mcp._get_ompa(vault_path=str(vault_root), enable_semantic=False)

    results["get_ompa_miss"] = bench(miss, runs=3)
    results["get_ompa_hit"] = bench(hit, runs=3)

    miss_ms = results["get_ompa_miss"]["mean_ms"]
    hit_ms = results["get_ompa_hit"]["mean_ms"]
    results["cache_speedup"] = miss_ms / hit_ms if hit_ms > 0 else None

    mcp._clear_ompa_cache()
    return results


def bench_full_sync(vault_root: Path, tmp: Path) -> dict:
    """End-to-end Ompa.sync() — fresh everything, no semantic for speed baseline,
    and one with semantic to see total cost."""
    results: dict[str, Any] = {}

    def sync_no_semantic():
        # Clone vault to avoid polluting the shared fixture with .palace/
        clone = tmp / f"sync_ns_{time.perf_counter_ns()}"
        shutil.copytree(vault_root, clone)
        ao = Ompa(vault_path=clone, enable_semantic=False)
        r = ao.sync()
        shutil.rmtree(clone, ignore_errors=True)
        return r

    results["sync_no_semantic"] = bench(sync_no_semantic, runs=3)
    return results


# ---------------------------------------------------------------------------
# Profiling
# ---------------------------------------------------------------------------

def profile_callable(fn: Callable[[], Any], top: int = 15) -> str:
    pr = cProfile.Profile()
    pr.enable()
    fn()
    pr.disable()
    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
    ps.print_stats(top)
    return s.getvalue()


def profile_hotspots(vault_root: Path, tmp: Path) -> dict:
    results: dict[str, str] = {}

    # 1. Cold list_notes (Note.from_file / frontmatter parse cost)
    def p_cold_list():
        Vault(vault_root).list_notes()
    results["cold_list_notes"] = profile_callable(p_cold_list, top=15)

    # 2. KG populate_from_vault
    def p_kg_pop():
        db = tmp / f"kg_prof_{time.perf_counter_ns()}.sqlite3"
        kg = KnowledgeGraph(db_path=str(db))
        kg.populate_from_vault(vault_root)
    results["kg_populate_from_vault"] = profile_callable(p_kg_pop, top=15)

    # 3. Palace auto-build (with batch, as sync uses it)
    def p_palace():
        p = Palace(tmp / f"palace_prof_{time.perf_counter_ns()}")
        with p.batch():
            p.auto_build_from_vault(vault_root)
    results["palace_auto_build"] = profile_callable(p_palace, top=15)

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=1000, help="notes in main vault")
    parser.add_argument(
        "--semantic-n",
        type=int,
        default=200,
        help="notes for semantic bench (smaller for speed); 0 = use main",
    )
    parser.add_argument(
        "--skip-semantic", action="store_true", help="skip semantic bench"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).parent / "results.json",
        help="JSON output path",
    )
    args = parser.parse_args()

    tmp_root = Path(tempfile.mkdtemp(prefix="ompa-bench-"))
    print(f"[bench] tmp root: {tmp_root}")

    vault_root = tmp_root / "vault"
    print(f"[bench] generating vault n={args.n}...")
    t0 = time.perf_counter_ns()
    gen_info = generate_vault(vault_root, n=args.n)
    gen_ms = (time.perf_counter_ns() - t0) / 1e6
    print(f"[bench]   generated {gen_info['n_notes']} notes in {gen_ms:.0f}ms")

    all_results: dict[str, Any] = {
        "config": {
            "n_notes": gen_info["n_notes"],
            "semantic_n": args.semantic_n if not args.skip_semantic else None,
            "python": sys.version.split()[0],
        },
        "generation_ms": gen_ms,
    }

    print("[bench] vault...")
    all_results["vault"] = bench_vault(vault_root)

    print("[bench] kg...")
    all_results["kg"] = bench_kg(vault_root, tmp_root)

    print("[bench] palace...")
    all_results["palace"] = bench_palace(vault_root, tmp_root)

    if not args.skip_semantic:
        print("[bench] semantic (may download model on first run)...")
        semantic_n = args.semantic_n if args.semantic_n > 0 else None
        all_results["semantic"] = bench_semantic(vault_root, tmp_root, semantic_n)
    else:
        all_results["semantic"] = {"skipped": True}

    print("[bench] mcp...")
    all_results["mcp"] = bench_mcp(vault_root)

    print("[bench] full sync (no semantic)...")
    all_results["full_sync"] = bench_full_sync(vault_root, tmp_root)

    print("[bench] profiling hotspots...")
    all_results["profiles"] = profile_hotspots(vault_root, tmp_root)

    # Write JSON (drop profile strings from JSON to keep it compact)
    json_safe = dict(all_results)
    json_safe["profiles"] = {
        k: v.splitlines()[:25] for k, v in all_results["profiles"].items()
    }
    args.out.write_text(json.dumps(json_safe, indent=2, default=str), encoding="utf-8")
    print(f"[bench] wrote {args.out}")

    # Human-readable summary
    print()
    print("=" * 70)
    print("OMPA BENCHMARK SUMMARY")
    print("=" * 70)
    print(f"Vault: {gen_info['n_notes']} notes")
    print()
    v = all_results["vault"]
    print(
        f"list_notes  cold={v['list_notes_cold']['mean_ms']:.1f}ms  "
        f"warm={v['list_notes_warm']['mean_ms']:.2f}ms  "
        f"speedup={v['cache_speedup']:.1f}x"
    )
    print(f"find_orphans (warm): {v['find_orphans_warm']['mean_ms']:.1f}ms")
    print(f"get_stats    (warm): {v['get_stats_warm']['mean_ms']:.1f}ms")
    print()
    kg = all_results["kg"]
    print(f"kg populate_from_vault: {kg['populate_from_vault']['mean_ms']:.0f}ms "
          f"-> stats={kg['kg_stats']}")
    print(f"kg query_entity   : {kg['query_entity']['mean_ms']:.2f}ms")
    print(f"kg query_relation : {kg['query_relation']['mean_ms']:.2f}ms")
    print(f"kg timeline       : {kg['timeline']['mean_ms']:.2f}ms")
    print()
    p = all_results["palace"]
    print(f"palace auto_build no_batch={p['auto_build_no_batch']['mean_ms']:.0f}ms "
          f"batch={p['auto_build_with_batch']['mean_ms']:.0f}ms "
          f"speedup={p['batch_speedup']:.1f}x")
    print()
    s = all_results["semantic"]
    if "error" in s:
        print(f"semantic: SKIPPED - {s['error']}")
    elif s.get("skipped"):
        print("semantic: skipped by flag")
    else:
        print(
            f"semantic index_vault (n={s['note_count_used']}): "
            f"{s['index_vault_ms']:.0f}ms  chunks={s['indexed_chunks']}"
        )
        print(
            f"semantic search  p50={s['search_p50_ms']:.2f}ms  "
            f"p95={s['search_p95_ms']:.2f}ms  "
            f"mean={s['search_mean_ms']:.2f}ms"
        )
    print()
    m = all_results["mcp"]
    print(f"mcp _get_ompa miss={m['get_ompa_miss']['mean_ms']:.2f}ms "
          f"hit={m['get_ompa_hit']['mean_ms']:.4f}ms "
          f"speedup={m['cache_speedup']:.0f}x")
    print()
    fs = all_results["full_sync"]
    print(f"Ompa.sync() (no semantic): {fs['sync_no_semantic']['mean_ms']:.0f}ms")
    print("=" * 70)

    # Intentionally leave tmp_root for manual inspection if run fails; clean otherwise.
    try:
        shutil.rmtree(tmp_root, ignore_errors=True)
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
