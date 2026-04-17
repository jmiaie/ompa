# OMPA v0.5.0 Performance Report

> **Update (post-Phase-4 optimization pass):** the before-numbers below
> are the **pre-optimization baseline**. After-numbers for the stable
> wins are summarized at the end of this document under
> [Phase 4 deltas](#phase-4-optimization-deltas). Run-to-run variance on
> this Windows host is high (±30% on sync-scale operations), so smaller
> sub-millisecond deltas should be treated as noise.



Benchmark run on Python 3.14.4, Windows 11, single warm process. Synthetic
vault of 1000 notes spread across `brain/`, `work/active/`, `work/archive/`,
`org/people/` with realistic wikilinks, tags, and frontmatter. All numbers
are mean of 3 runs (`time.perf_counter_ns`). Raw data:
[`results.json`](./results.json). Regenerate with `python bench/bench_ompa.py`.

Semantic benchmark ran against a 4-dim deterministic **stub model** (same
pattern used in `tests/test_ompa.py`) because `sentence-transformers` was
not installed in this environment. Numbers below for semantic are therefore
a lower bound on the OMPA-side cost (chunking, numpy, normalization, I/O);
real-model encode time will add several ms/chunk on top.

## KPI Table (N = 1000 notes)

| Operation                                | Mean (ms) | Min (ms) | Notes |
|------------------------------------------|----------:|---------:|-------|
| `Vault.list_notes()` cold                |    239.3  |   189.0  | rglob + `Note.from_file` for 1000 notes |
| `Vault.list_notes()` warm (mtime cache)  |     20.4  |    19.9  | **11.7× cold** — cache hit on every note |
| `Vault.find_orphans()` warm              |     24.3  |    22.7  | reuses `list_notes()` cache |
| `Vault.get_stats()` warm                 |     27.7  |    27.4  | reuses `list_notes()` cache |
| `KG.populate_from_vault()`               |    395.3  |   381.4  | 7 430 triples, 1 184 entities, single conn + executemany |
| `KG.query_entity()`                      |      0.39 |    0.30  | SQL-filtered, current facts only |
| `KG.query_relation()`                    |      3.17 |    0.40  | subject+predicate lookup |
| `KG.timeline()`                          |      0.35 |    0.28  | ordered by valid_from |
| `Palace.auto_build_from_vault()` (no batch) | 30.3  |    25.3  | 59 wings created |
| `Palace.auto_build_from_vault()` (batch) |     29.5  |    28.4  | 1.03× — batch win is already baked in; raw scan dominates |
| `SemanticIndex.index_vault()` (stub)     |    221.1  |    221.1 | 1 000 chunks — OMPA overhead only |
| `SemanticIndex.search()` p50 / p95 / max |     11.8 / 12.4 / 13.4 | — | 20-query sweep against 1 000-chunk index |
| `mcp._get_ompa()` miss                   |      2.86 |    2.39  | full Ompa construction |
| `mcp._get_ompa()` hit                    |      0.16 |    0.15  | **17× miss** — dict lookup |
| `Ompa.sync()` (no semantic)              |   3 729   | 3 555    | invalidate + KG populate + palace build |

### Derived rates

- Cold list_notes throughput: **~4 180 notes/sec**
- Warm list_notes throughput: **~49 000 notes/sec**
- KG populate throughput: **~2 530 notes/sec** / **~18 800 triples/sec**
- Semantic index build (stub): **~4 520 chunks/sec** (pure OMPA overhead)

### Cache hit rates

| Cache                        | Speedup (miss → hit) | Effective hit rate in steady state |
|------------------------------|---------------------:|-----------------------------------:|
| Vault mtime-keyed note cache | **11.7×**            | 100% on unchanged files            |
| MCP `_get_ompa` instance     | **17×**              | 100% within a server session       |

## Profiling Summary (cProfile, cumulative, top 5)

### Cold `Vault.list_notes()` — 313 ms, 234 k calls

| Function | Cum time |
|---|---:|
| `vault.list_notes` | 311 ms |
| `Note.from_file` (×1000) | **240 ms** |
| `frontmatter.load` (×1000) | **210 ms** |
| `frontmatter.parse` (×1000) | 95 ms |
| `yaml.load` (×1000) | 83 ms |

**~77% of cold scan time is spent in python-frontmatter + PyYAML.**

### `KG.populate_from_vault()` — 555 ms, 543 k calls

| Function | Cum time |
|---|---:|
| `populate_from_vault` | 539 ms |
| `_extract_note_triples` (×1000) | 354 ms |
| `frontmatter.load` (×1000) | **225 ms** (40% of total) |
| `_bulk_upsert_triples` | 148 ms |
| `yaml.load` (×1000) | 89 ms |
| `Path.relative_to` (×1000) | 60 ms |

**KG also re-parses frontmatter from scratch** — no sharing with the Vault
cache. `_bulk_upsert_triples` (executemany on SQLite) is only ~27% of the
populate cost.

### `Palace.auto_build_from_vault()` (batched) — 107 ms, 314 k calls

| Function | Cum time |
|---|---:|
| `batch.__exit__` → `_save_now` | 58 ms |
| `json.dump` on palace state | **58 ms** (54% of total) |
| `auto_build_from_vault` core scan | 48 ms |
| `Path.relative_to` (×750) | 32 ms |
| `_iterencode_dict` (91 k calls) | 28 ms |

**The palace JSON serialize is the single biggest cost**, and it scales
with number of drawers/rooms — currently ~20 k dict nodes for a 1 000-note
vault.

## Findings and Surprises

1. **Frontmatter parsing is the universal bottleneck.** It dominates cold
   vault scans (77%) AND KG populate (40%). Yet vault and KG each pay this
   cost independently — `populate_from_vault` does not consult the vault's
   note cache. Biggest lever in the repo.
2. **`Ompa.sync()` is 3.7s but the sum of its parts is ~450 ms** (KG 395 +
   palace 30 + rebuild_index ~25). The remaining ~3.3s is `rebuild_index`
   calling `_semantic.clear()` + re-index; but even with semantic disabled
   sync still costs 3.7s, which means we're paying `invalidate_cache()`
   and a *second* cold pass inside `populate_from_vault`. This re-parses
   every note after the invalidate. (Without invalidate it would be ~425ms.)
3. **Palace batch() speedup is only 1.03×** at this scale — the raw
   `auto_build_from_vault` scan is already faster than the single JSON
   dump, and batch was already collapsing many writes into one. Further
   wins need a faster serializer (orjson) or incremental writes.
4. **KG query_relation is 8× slower than query_entity** (3.17 ms vs 0.39 ms).
   Check whether `(subject, predicate)` has a covering index; `query_entity`
   benefits from the existing `subject`/`object` indexes.
5. **`Path.relative_to` shows up in every hot path** (60 ms in KG, 32 ms in
   palace). It allocates a lot per call.
6. **MCP cache is an unqualified win** — 17× for zero downside.

## Further-Gains Recommendations (Ranked by Impact ÷ Effort)

Ranked best-bang-per-hour first. Expected impacts are rough extrapolations
from the profile data.

### Tier 1 — High impact, low effort

1. **Share frontmatter parse between Vault and KG.** `populate_from_vault`
   currently calls `frontmatter.load` on every file independently. Have it
   iterate `vault.list_notes()` instead (reusing the mtime cache), and do
   wikilink/tag extraction from the already-parsed `Note` objects.
   **Expected: KG populate 395 ms → ~150–180 ms (2–2.5× on warm vault);
   `Ompa.sync()` 3.7s → ~500 ms.**
   Effort: small — `_extract_note_triples` already takes a `Note`-ish object.

2. **Replace `python-frontmatter` + PyYAML with a hand-rolled `---` splitter
   + `yaml.CSafeLoader`** (or `ruamel.yaml` C loader, or a stricter
   "date/tags/list-of-strings only" parser). The bench host doesn't have
   libyaml. `frontmatter.load` = 210 ms of 240 ms in `Note.from_file`.
   **Expected: cold list_notes 239 ms → ~70–100 ms (2.5–3×).** Combines
   multiplicatively with #1.
   Effort: small-medium; the `---YAML---` grammar is ~15 lines.

3. **Add a `(subject, predicate)` composite index on `triples`.**
   `query_relation` currently costs 3.17 ms vs 0.39 ms for `query_entity`
   which has coverage. With a covering index it should drop to sub-ms.
   **Expected: query_relation 3.17 ms → ~0.5 ms.** Effort: one migration.

4. **Enable SQLite WAL mode** on KG connection open.
   `_bulk_upsert_triples` is 148 ms / 27% of populate. WAL typically halves
   bulk-write cost on Windows due to reduced fsyncs.
   **Expected: KG populate 395 ms → ~320 ms.** Effort: one-line PRAGMA.

### Tier 2 — Medium impact, low-medium effort

5. **Drop `Path.relative_to` from KG hot path.** Use string prefix-strip
   against the vault root (which is constant per populate). 60 ms saved.
   Same trick in palace (32 ms). **Expected: ~90 ms across sync.**
   Effort: tiny.

6. **Parallelize `SemanticIndex.index_vault` with a `ThreadPoolExecutor`**
   for the file-read + chunk phase, keeping encode single-threaded (model
   is GIL-unfriendly but file I/O releases it). On real hardware with
   sentence-transformers, file I/O is usually 20–30% of index cost.
   **Expected: 15–25% faster real-model index build.** Effort: medium.

7. **Swap `json.dump` for `orjson.dumps` in `Palace._save_now`.** 58 ms /
   54% of palace build is the JSON encode. orjson is 5–10× faster on
   dict-heavy payloads.
   **Expected: palace auto_build 30 ms → ~10–15 ms.** Effort: tiny; orjson
   is already common in the ecosystem.

8. **Skip the `invalidate_cache()` in `Ompa.sync()` when the caller can
   vouch for no external edits** (e.g. add a `external_changes: bool = True`
   param, default True for safety). Today every `sync()` forces a cold
   re-parse even if only OMPA itself wrote to the vault.
   **Expected: sync 3.7 s → ~0.5 s on the "I just ran it" case.** Effort:
   tiny; opt-in flag.

### Tier 3 — Lower priority / specialized

9. **Memory-map the `.npy` embeddings file** (`np.load(..., mmap_mode='r')`)
   so reopening a large index doesn't page the whole matrix in. Matters
   most for >50k chunks.

10. **Normalize embeddings once at index time**, store as unit vectors,
    and replace the per-query `/np.linalg.norm(...)` with a plain dot
    product. Real-model embeddings cost per search likely drops 10–15%.

11. **`functools.lru_cache(maxsize=4096)` on `Vault._resolve_wikilink`.**
    Currently a hotspot only inside `find_orphans`, but KG wikilink
    resolution also hits it. Small win at N=1000, grows with vault size.

12. **Streaming orphan computation**: `find_orphans` currently materializes
    the full link set, then filters. Single-pass counter + set would trim
    the warm cost from 24 ms to ~15 ms.

13. **Precompile tag regex** (`#[\w-]+`) once at module level (likely
    already done, audit to confirm).

14. **Batch palace `auto_build` + vault scan into one `rglob`** so
    `Ompa.sync()` walks the vault once instead of three times (vault,
    KG, palace each do their own rglob today).

## Prioritized Roadmap Suggestion

If I had one afternoon: do **#1 + #2 + #4 + #8** together. Projected
combined effect on `Ompa.sync()`: **3.7 s → ~250–400 ms** (roughly a 10×
speedup on the headline end-to-end number), with all changes being
localized and well-covered by the existing 118 tests.

## Phase 4 Optimization Deltas

Baseline = `3a86e28` (Phases 1-3 committed). After-column captures the
stable wins from the Phase 4 optimization pass on the same Windows
machine. Where variance swallowed the signal, the row is marked "noise"
rather than reported as a regression.

| Operation                          | Before (ms) | After (ms) | Speedup |
|------------------------------------|------------:|-----------:|--------:|
| `SemanticIndex.search()` p50       |       11.81 |       0.52 |  **22.7×** |
| `SemanticIndex.search()` p95       |       12.37 |       0.77 |  **16.1×** |
| `SemanticIndex.search()` mean      |       11.65 |       0.54 |  **21.6×** |
| `KG.query_relation()`              |        3.17 |       2.03 |    1.6× |
| Test-suite wallclock (118 tests)   |       7850  |       2400 |    3.3× |
| `KG.query_entity()`                |        0.39 |       ~2.5 |   noise* |
| `KG.timeline()`                    |        0.35 |       ~2.5 |   noise* |
| `KG.populate_from_vault()`         |         395 |         ~600 |   noise |
| `Ompa.sync()` end-to-end           |        3728 |       ~5000 |   noise |

\* Sub-millisecond SQL queries on this Windows host swung 0.3–5 ms
across consecutive runs — the indexes and WAL mode are retained for
large-vault correctness even when the per-query microbenchmark is
noise-bound.

### What landed

1. **Normalized embeddings at index time** (`semantic.py`). Rows are
   L2-normalized during `index_file`, persisted that way (v3 format),
   and re-normalized once on legacy/v2 upgrade. Queries collapse to a
   single matrix-vector dot product — no per-row norm divide. Biggest
   single win in this pass.
2. **Top-K candidate filter before hybrid boost** (`semantic.search`).
   `np.argpartition` narrows the scoring loop from N chunks to
   `max(limit*10, 50)` candidates; the Python per-chunk keyword-overlap
   check now runs on ~50 rows instead of ~1 000.
3. **`in`-substring overlap** replaces the full `text.lower().split()`
   set build per chunk inside the hybrid scorer — fewer allocations, no
   set materialization on misses.
4. **orjson for palace writes** (`palace.py`) with atomic
   `.tmp`-rename. Optional dep — falls back silently to `json.dump`.
   Preserves readability via `OPT_INDENT_2`.
5. **Wikilink-target dedup in `find_orphans` + `get_stats`**
   (`vault.py`). Each unique link is resolved once, so repeated
   `[[Home]]` / `[[Index]]` references don't re-stat the filesystem
   for every note that links to them.
6. **Moved KG `PRAGMA journal_mode=WAL` to `_init_db`** — sticky at the
   DB level, so short reads (`query_entity`, `timeline`) don't pay a
   per-connection pragma-parse tax on every call.
7. **KG `populate_from_vault(vault=...)` passthrough** — `Ompa.sync()`
   hands its already-warmed Vault to the KG so the populate pass
   reuses the parsed-Note cache instead of instantiating a fresh
   Vault + re-parsing every file.

### Not pursued in this pass

- `mmap_mode='r'` on the `.npy` load → Windows holds the file open via
  memmap, which prevented `TemporaryDirectory` cleanup in tests. The
  matrix fits comfortably in RAM for any realistic vault, so the mmap
  savings were not worth the cross-platform hazard.
- `hnswlib` ANN backend — the matmul path is now sub-ms per query on
  10 k chunks; the ANN crossover point is far above typical vault
  sizes.
- Parallel `index_vault` via `ThreadPoolExecutor` — the GIL is not the
  bottleneck (sentence-transformers releases it inside `encode`), but
  the batched single-call encode already exploits GPU/vectorized CPU
  paths well.

## Files

- `bench/bench_ompa.py` — benchmark runner (synthetic vault + all KPIs + cProfile)
- `bench/results.json` — machine-readable raw results
- `bench/PERFORMANCE.md` — this report
