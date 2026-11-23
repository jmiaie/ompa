# OMPA benchmarks

Every number OMPA publishes comes from a script in this directory. Each one can be re-run.
Measured 2026-10-20 on an 8-vCPU Intel Xeon VM (16 GB RAM, Linux) with Python 3.12, numpy 2.5,
torch 2.14 (CPU), sentence-transformers 6.1 (`all-MiniLM-L6-v2`) and SQLite 3.53.
**Caveat:** the box was shared with other jobs (load average 3–13), which inflates latency tails.
Recall numbers are deterministic and unaffected by load.

| Script | What it measures |
|---|---|
| `longmemeval_s.py` | Retrieval on the full LongMemEval-S, n=470, per search mode, bootstrap 95% CIs |
| `search_latency.py` | Search p50/p95, index size, build time and cold load at 1k / 10k / 50k notes |
| `temporal_mc.py` | Temporal knowledge-graph point-in-time correctness, 20-seed Monte Carlo |
| `mcp_latency.py` | What an agent waits for: whole MCP tool calls (search, session start, status ...) on 1k / 10k notes |
| `smoke_sample.py` | **Smoke test only:** 20 hand-written items. This is *not* LongMemEval, and its score means nothing beyond "search works" |

## 1. LongMemEval-S (session-level retrieval)

Protocol (`longmemeval_s.py`):

- Each question gets a fresh vault. Each haystack session (~50 per question, user + assistant
  turns) is written as one note, then `rebuild_index()` runs.
- The question is the query. A hit is any evidence session in the top k.
- The 30 abstention questions have no evidence session and are excluded, giving n = 470.
- CIs are 10,000 bootstrap resamples over questions, seed 20261020.

| Mode | R@5 [95% CI] | R_all@5 | nDCG@10 | R@1 | R@10 |
|---|---|---|---|---|---|
| **hybrid** (default `ao.search`; BM25 + MiniLM, RRF) | **0.979** [0.964, 0.991] | 0.874 | **0.929** | **0.891** | **0.996** |
| semantic only (`hybrid=False`) | 0.974 [0.960, 0.987] | 0.845 | 0.896 | 0.849 | 0.983 |
| keyword only (FTS5 BM25, **no ML deps**) | 0.970 [0.953, 0.985] | 0.851 | 0.910 | 0.877 | 0.989 |
| OMPA ≤ 1.0.8 default (cosine + keyword-overlap boost) | 0.981 [0.968, 0.991] | 0.874 | 0.910 | 0.851 | 0.989 |
| OMPA ≤ 1.0.8 without `ompa[semantic]` (file-name match) | n/a: matches file names only, never content | | | | |

Paired against the ≤ 1.0.8 default, same 470 questions:

- Hybrid: R@5 −0.002 [−0.015, +0.011] (not significant). nDCG@10 +0.019 [+0.008, +0.030]
  (significant: better ranking of the evidence sessions).
- Semantic only: R_all@5 −0.030 [−0.053, −0.009] (significant).

These are OMPA's own numbers. The "96.6% R@5" figure that earlier READMEs quoted is MemPalace's result
for its own pipeline. OMPA never measured it, so it was removed.

```bash
pip install "ompa[semantic]"
# longmemeval_s_cleaned.json from https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned
for s in 0 1; do python benchmarks/longmemeval_s.py run --data longmemeval_s_cleaned.json \
    --out lme_shard$s.jsonl --shard $s --nshards 2 & done; wait      # ~1 h on 8 vCPU
python benchmarks/longmemeval_s.py analyze 'lme_shard*.jsonl'
```

Each shard peaks at about 1.2 GB RSS (more if the 270 MB JSON is loaded whole; see the script
docstring). Rebuilding one ~50-session vault takes p50 9.3 s on CPU (batched embedding), versus
15.5 s in ≤ 1.0.8.

## 2. Search latency and index size (synthetic, `search_latency.py`)

Each note is one chunk of ~83 Zipf-distributed words plus 2 unique words. Queries are 3 common words
plus a target note's 2 unique words; *content hit@5* means the target note is in the top 5. A
deterministic stub embedder replaces the model, which isolates OMPA's own code. A real query adds
~5 ms of MiniLM encoding on an idle CPU. Each cell is the median of 2 rounds × 30 queries
(10 queries for 1.0.8 at 50k).

| Notes | 1.0.8 search p50 / p95 | **now: hybrid** p50 / p95 | semantic only p50 | keyword only p50 |
|---|---|---|---|---|
| 1k | 17.4 / 19.8 ms | **2.1 / 2.9 ms** | 0.12 ms | 1.1 ms |
| 10k | 185 / 236 ms | **9.4 / 13.9 ms** | 0.75 ms | 7.0 ms |
| 50k | 1,061 / 1,241 ms | **44 / 59 ms** | 3.1 ms | 32 ms |

| Notes | index on disk 1.0.8 → now (semantic + keyword) | load index | cold start, fresh process (import + load + 1 query) | peak RSS of that process |
|---|---|---|---|---|
| 1k | 8.2 MB → 1.2 + 1.0 MB | 0.09 → 0.003 s | 0.31 → 0.18 s | 81 → 51 MB |
| 10k | 82 MB → 11.9 + 10.2 MB | 0.97 → 0.03 s | 1.51 → 0.21 s | 443 → 125 MB |
| 50k | 411 MB → 59.7 + 49.5 MB | 5.2 → 0.34 s | 6.8 → 0.48 s | 2,060 → 443 MB |

Content hit@5 at 10k and 50k:

- Default search: 0.40 in 1.0.8, **1.00** now.
- Without `ompa[semantic]`: **0.00** in 1.0.8 (file names only), **1.00** now, at a p50 of 7 ms (10k)
  and 34 ms (50k).

Hybrid latency at 50k is mostly BM25 over very common query words. Semantic scoring by itself is
3 ms. A full index build at 50k takes 3.0 s (semantic, stub embedder) plus 2.0 s (keyword).

```bash
pip install numpy
python benchmarks/search_latency.py --sizes 1000 10000 50000
```

## 3. Temporal knowledge graph (`temporal_mc.py`)

Each of the 20 seeds runs 30 entities with 2–5 sequential assignments each. 35% of assignments
re-assert an earlier fact. Each seed checks 400 point-in-time queries.

| | point-in-time accuracy [95% CI] | validity intervals lost |
|---|---|---|
| OMPA ≤ 1.0.8 | 0.895 [0.886, 0.904] | 36% |
| now (since #16) | **1.000** [1.000, 1.000] | **0%** |

```bash
python benchmarks/temporal_mc.py --seeds 20
```

## 4. Audit memory (`audit_overhead.py`, `audit_tamper_mc.py`)

```bash
python benchmarks/audit_overhead.py --out audit_overhead.json          # ~1.5 min
python benchmarks/audit_tamper_mc.py --trials 5000 --seed 20261021 --seed 7 --seed 1337 --seed 42 --seed 99 --mode unsigned
python benchmarks/audit_tamper_mc.py ... --mode hmac
python benchmarks/audit_tamper_mc.py --trials 500 ... --mode unsigned_rehash
```

Tamper ops: flip_byte, delete_row, delete_tail, reorder, insert_forged, append_forged, duplicate_row, redact_no_tombstone, unredact_flag, truncate_file, drop_table_rows, checkpoint_flip, checkpoint_delete, head_tamper, head_delete. HMAC mode adds sig_flip, sig_strip_all, rehash_suffix and rekey_suffix (an attacker without the key). A tamper that leaves the file byte-identical (a 0-byte truncation) counts as no_effect, not as a detection. `unsigned_rehash` is an attacker who rewrites the whole chain consistently. It is **undetectable without a key or an external anchor, by design**; the table shows the same trials verified against a head published before the attack.

An earlier 2000×3-seed run found one miss: a 26-byte cut of a trailing page holding no live rows. Verify now checks file size against page size and the header page count (`db_size`). The table is from the run after that fix.

Measured 2026-10-21 (PT) on the shared 16 GB Linux box, Python 3.12, while another worker shared the CPU. Numbers vary run to run; compare within a table only.

**Write latency** (`Ompa.write`, keyword-only vault, `enable_semantic=False`, n=900 writes per mode, rotated rounds):

| mode | write p50 (ms) | p95 (ms) | overhead p50 | `kg_add` p50 (µs) |
|---|---|---|---|---|
| audit off | 0.92 | 5.20 | — | 42 |
| audit on | 1.42 | 6.09 | +0.50 ms (54%) | 205 |
| audit + hmac | 1.50 | 6.55 | +0.57 ms (62%) | 213 |
| audit + ed25519 | 1.77 | 6.70 | +0.84 ms (91%) | 256 |

Single `AuditLog.append` (own transaction + head update): p50 101 µs, 8131 appends/s.

**Verify** (full chain + checkpoints + head, median of 3; log built in 1000-entry batches):

| entries | mode | verify (s) | µs/entry | DB size | as_of full replay (s) |
|---|---|---|---|---|---|
| 10,000 | unsigned | 0.072 | 7.2 | 5.5 MB (551 B/entry) | 0.54 |
| 10,000 | hmac | 0.090 | 9.0 | 6.3 MB (627 B/entry) | 0.53 |
| 10,000 | ed25519 | 1.001 | 100.1 | 6.5 MB (647 B/entry) | 0.53 |
| 100,000 | unsigned | 0.767 | 7.7 | 55.1 MB (551 B/entry) | 5.91 |
| 100,000 | hmac | 0.923 | 9.2 | 62.6 MB (626 B/entry) | 6.21 |
| 100,000 | ed25519 | 10.067 | 100.7 | 64.8 MB (648 B/entry) | 6.32 |

**Citations on search** (keyword search, p50, n=200 queries):

| notes | without (ms) | with (ms) | overhead |
|---|---|---|---|
| 1,000 | 0.247 | 0.423 | +0.176 ms |
| 10,000 | 0.781 | 0.987 | +0.206 ms |

**Tamper Monte Carlo** (`benchmarks/audit_tamper_mc.py`; one random tamper per trial, every 5th trial also verifies the untouched log as a false-positive control):

| mode | seeds | trials | effective tampers | detected | rate | false positives |
|---|---|---|---|---|---|---|
| unsigned | 20261021, 7, 1337, 42, 99 | 25,000 | 24,998 | 24,998 | 1.0000 | 0/5,000 |
| hmac | 20261021, 7, 1337, 42, 99 | 25,000 | 25,000 | 25,000 | 1.0000 | 0/5,000 |
| unsigned_rehash | 20261021, 7, 1337 | 1,500 | 1,500 | 0 | 0.0000 | 0/300 |
| unsigned_rehash + external anchor | 20261021, 7, 1337 | 1,500 | 1,500 | 1,500 | 1.0000 | — |

## 5. MCP tool calls (`mcp_latency.py`)

The MCP server builds a fresh `Ompa` for every tool call, so sections 1–2 (a resident index) are not
what an agent waits for. This script times `handle_call_tool`, the function the stdio loop calls.
Notes are ~250 words with frontmatter, tags and two wikilinks; one word in six is a common English
word and the query is a question, so it matches most notes, as a real one does.

Measured 2026-11-22 on a 4-vCPU cloud sandbox, Python 3.11, 21 calls per cell (7 for the four
vault-wide tools), p50 in ms. 1.1.1 is `main` before this release. The two versions were run one
after the other in the same session; numbers from different sessions on this sandbox differ by up
to 20%, so compare within a table, not with earlier ones.

**Keyword-only** (`ompa[semantic]` not installed, so no model). Each version was run twice,
alternating, and a cell is the mean of its two runs, which were within 10% of each other.

| Tool call | 1,000 notes: 1.1.1 → 1.2.1 | 10,000 notes: 1.1.1 → 1.2.1 |
|---|---|---|
| `ao_session_start` | 169 → **33** (5.2×) | 1,769 → **308** (5.7×) |
| `ao_wrap_up` | 167 → **28** (6.0×) | 1,704 → **269** (6.3×) |
| `ao_status` | 85 → **24** (3.5×) | 906 → **268** (3.4×) |
| `ao_orphans` | 82 → **22** (3.7×) | 837 → **218** (3.8×) |
| `ao_search`, searches within a second of each other | 15 → **5.0** (3.1×) | 128 → **14** (9.5×) |
| `ao_search`, no search in the last second | 15 → **12** (1.2×) | 132 → **53** (2.5×) |
| the same search on a resident `Ompa` | 3.7 → 3.5 | 23 → **7.8** (3.0×) |
| `ao_write` | 2.7 → 4.0 | 3.0 → 3.1 |
| `ao_kg_query` / `ao_kg_stats` / `ao_classify` | 0.5–0.9 → 0.7–1.0 | 0.9–4.8 → 0.7–3.8 |

On a vault shaped like one LongMemEval-S haystack (50 notes of ~1,700 words): `ao_search` 6.5 →
**5.1** (7.3 → **3.9** in a burst), `ao_session_start` 19 → **5.1**, the resident search 5.2 → **2.3**.

**With `ompa[semantic]`** (`all-MiniLM-L6-v2` loaded before timing; hybrid search; one run each):

| Tool call | 1,000 notes: 1.1.1 → 1.2.1 | 10,000 notes: 1.1.1 → 1.2.1 |
|---|---|---|
| `ao_search`, searches within a second of each other | 30 → **13** (2.3×) | 253 → **26** (9.9×) |
| `ao_search`, no search in the last second | 30 → **18** (1.7×) | 174 → **67** (2.6×) |
| the same search on a resident `Ompa` | 13 → 12 | 40 → **22** (1.9×) |

On the 50-session vault: `ao_search` 23 → **13**, the resident search 20 → **14**. The vault-wide
calls do not search and measure as in the keyword-only table. 1.1.1 has no refresh interval, so
its two `ao_search` rows are the same code path and differ only by noise. On the 50-session vault
neither version returns the note the query names in its top 5 in hybrid mode: these notes are
random words, so the semantic half of the fusion is noise there. Section 1 is the measure of
ranking quality.

What changed, and what did not:

- **Vault-wide calls** parsed every note's YAML, twice for session start and wrap-up. They now
  read each note once and parse YAML only where it can change the answer.
- **`ao_search`** walked the whole vault before every search to look for notes edited outside
  OMPA. The walk is about 3× faster, and the server runs it at most once per
  `OMPA_INDEX_REFRESH_SECONDS` (default 1). The first search row is a burst of searches; the
  second is a search that pays for the walk.
- **Keyword ranking** scored every note a question's common words matched, which at 10,000 notes
  is every note. From 2,000 notes up it ranks by the rarer words first and scores in full only
  the notes that could still reach the results: 21.6 → 7.3 ms for the ranking alone, with the
  same notes, scores and excerpts. Below 2,000 notes nothing changes, which is why the resident
  search at 1,000 notes is as it was.
- **The semantic index** was read and parsed again by every call: 32 ms at 10,000 notes. The
  server keeps it between calls and reuses it while it still matches the files on disk.
- **With long notes, keyword ranking no longer builds excerpts for notes it does not return.** In
  hybrid mode the keyword half is ranked without excerpts at all. On 50 notes of 1,700 words,
  ranking 20 notes takes 0.4 ms where ranking with excerpts took 7 ms. On the first 40
  LongMemEval-S histories with the model loaded, hybrid search went from 22.1 to 10.3 ms with
  the same top 10 for every question.
- **Small calls are not faster.** Every call now checks the vault path against the configured
  vaults (about 0.1 ms), and a write is saved atomically and checks that an automatically chosen
  name is free. `ao_write` varied from 2.5 to 4.5 ms across runs of 1.2.1 and from 2.6 to 3.1 on
  1.1.1.
- **What is left** of a search at 10,000 notes with no search in the last second is the walk of
  the vault (about 28 ms) and comparing its result with both indexes.

```bash
pip install numpy
python benchmarks/mcp_latency.py            # 1k and 10k notes, then the 50-session vault
```

## Notes and limits

- LongMemEval-S tests haystacks of about 50 sessions per question. That is retrieval over small
  vaults, not a 50k-note vault. The synthetic latency benchmark covers scale but not relevance.
- Chunks are 512 words, but MiniLM reads only the first 256 tokens of each, so semantic recall on
  long notes relies on the BM25 half of hybrid search. Chunks of 180 words were tried on
  2026-11-22 with a script that is not in this repository, and the default was left alone: on
  LongMemEval-S (n=470) hybrid R@5 stays 0.981 and R@10 goes from 0.996 to 0.989, both within
  noise, while indexing takes 2.5× as long (179 → 464 chunks per history). Semantic-only search
  does gain there (all answer sessions in the top 5: 0.847 → 0.872). On book-length sections (the
  public SearchTome set, 6,005 questions) hybrid R@1 goes from 64.9 to 69.7, so smaller chunks
  would suit vaults of long documents.
- The ≤ 1.0.8 LongMemEval baseline was measured on 2026-10-20 from the same harness. Its search
  code is unchanged in #16, and a fresh 40-question re-run gave identical top-5 lists.
