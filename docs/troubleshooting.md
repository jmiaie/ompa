# Troubleshooting

## `sentence-transformers` first-run download hangs

First call to any semantic feature (`ao_search` with semantic enabled,
`ao_sync`, `rebuild-index`) downloads the `all-MiniLM-L6-v2` model (~90 MB)
to `~/.cache/huggingface/`. On a slow connection this can take minutes.

**Fix:** pre-download once:

```bash
python -c "from sentence_transformers import SentenceTransformer; \
           SentenceTransformer('all-MiniLM-L6-v2')"
```

Or disable semantic search entirely:

```python
ao = Ompa(vault_path="./my-vault", enable_semantic=False)
```

## `sqlite3.OperationalError: database is locked`

WAL mode usually prevents this, but it can happen if:

- Multiple `Ompa` instances write to the same KG concurrently from
  different processes.
- A long-running `populate_from_vault` overlaps with a read query.

**Fix:** the MCP server caches a single `Ompa` per vault path — use it
instead of instantiating `Ompa` yourself in multiple processes. For
library callers, share one `Ompa` instance across your codebase.

## `PermissionError: [WinError 32]` on Windows during tests

Windows holds file handles on memory-mapped files longer than POSIX.
If you see this in `TemporaryDirectory` teardown, something is keeping
an `np.load(..., mmap_mode='r')` handle open. OMPA already avoids this
by using plain `np.load()` for the semantic index — if you extend
semantic.py, don't reintroduce mmap mode on Windows.

## "Vault path rejected: /etc"

`_validate_vault_path` refuses well-known system roots (`/etc`, `/root`,
`/usr`, `C:\Windows`, `C:\Program Files`, filesystem roots). This is
intentional — OMPA creates `.palace/` under the vault path and will
happily pollute system dirs if allowed. Use a user-owned directory.

## Semantic index size seems huge

If you upgraded from v0.4.x and the index is still JSON (`semantic_index.json`),
OMPA will auto-migrate to the v2 binary format (`embeddings.npy` +
`meta.json`) on first load and delete the legacy file. Expected size
drop: ~5× (a 1 K-note vault: 7 MB JSON → 1.5 MB binary).

If migration doesn't happen, delete `semantic_index/` and run
`ao rebuild-index`.

## `classify()` returns `unknown` for obvious messages

Classifier uses English-only regex patterns. Non-English messages,
heavy jargon, or very short messages (< 5 words) all produce low
confidence. This is by design — OMPA prefers `unknown` over a bad
route. Add custom patterns to `MessageClassifier.PATTERNS` if you
need domain-specific types.

## KG has stale `links_to` triples after editing a note

Fixed in v0.5.0 — `populate_from_note` now deletes the note's
derived triples (`links_to`, `has_tag`, `in_folder`, `in_subfolder`,
`created_on`) before re-inserting. If you're on v0.4.x, run `ao sync`
to rebuild fully.

## Palace rooms / halls got wiped after a save

Fixed in v0.5.0 — `create_wing` and `create_room` now use `setdefault`
semantics, so re-creating an existing wing no longer clobbers its
rooms, halls, or drawers. Upgrade.
