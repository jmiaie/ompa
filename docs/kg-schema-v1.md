# OMPA knowledge-graph schema, v1

Status: **proposed contract**. The tables below describe exactly what OMPA writes today (`ompa/knowledge_graph.py`).
OMPA owns this schema. locus, pliamem and AegisFlow **read** it. None of them write to it.

`tests/test_kg_contract.py` builds a real OMPA vault and checks it against the tables in this file. If the code and
this document disagree, CI fails. Change both in the same PR.

Code constants: `ompa/kg_schema.py` (`KG_SCHEMA_VERSION`, `TRIPLES_COLUMNS`, `ENTITIES_COLUMNS`,
`RESERVED_PREDICATES`, `check_kg_schema()`). That module only imports the standard library, so readers can vendor it.

## 1. Where the database lives

| Vault mode | Path |
|---|---|
| Single vault | `<vault>/.palace/knowledge_graph.sqlite3` |
| Dual vault | `<shared>/.palace/knowledge_graph.sqlite3` and `<personal>/.palace/knowledge_graph.sqlite3` (two separate graphs) |
| Bare `KnowledgeGraph()` with no path | `~/.ompa/knowledge_graph.sqlite3` |

The file is a SQLite 3 database in WAL journal mode (`PRAGMA journal_mode=WAL`, `synchronous=NORMAL`).

## 2. Version stamp

<!-- contract:version -->
schema_version: 1
<!-- /contract:version -->

* The contract version is stored as `PRAGMA user_version`.
* `user_version = 0` means the file was written by OMPA before stamping existed (≤ 1.0.8). It has the same shape as v1.
  Readers must treat 0 as 1. OMPA stamps a 0 file as 1 the next time it opens it.
* This number is **not** the vault migration version in `.palace/schema_version` (`ompa.migration.CURRENT_SCHEMA_VERSION`,
  currently 3). That number tracks OMPA's internal layout (indexes, WAL). This number tracks only what readers can rely on.

## 3. Table `triples`

One row per (subject, predicate, object) fact.

<!-- contract:triples -->
| Column | SQLite type | NOT NULL | Meaning |
|---|---|---|---|
| `id` | TEXT | no | Primary key. `sha256("{subject}\|{predicate}\|{object}")` as hex, first 16 characters. |
| `subject` | TEXT | yes | Subject entity name, verbatim (case-sensitive). |
| `predicate` | TEXT | yes | Relationship name, e.g. `works_on`, `links_to`. |
| `object` | TEXT | yes | Object entity name or literal (dates, tags, folder names). |
| `valid_from` | TEXT | no | `YYYY-MM-DD`. Defaults to the writer's local date when the caller gives none. |
| `valid_to` | TEXT | no | `YYYY-MM-DD`. `NULL` means the fact is still valid. |
| `confidence` | REAL | no | 0.0 to 1.0. Default 1.0. |
| `source_file` | TEXT | no | Provenance: where the fact came from. See section 5. |
| `extracted_at` | TEXT | no | SQLite `CURRENT_TIMESTAMP`: `YYYY-MM-DD HH:MM:SS`, UTC, set on every insert or replace. |
<!-- /contract:triples -->

Identity and history:

* Because `id` is derived from (subject, predicate, object) and writes use `INSERT OR REPLACE`, the table holds **at most
  one row per (subject, predicate, object)**. Re-adding a fact overwrites its validity window, confidence, source and
  `extracted_at`. The table is a "current view with validity windows", not an append-only log.
* `invalidate()` closes a fact by setting `valid_to` on rows where it is `NULL`. Rows are never deleted by OMPA.
* A fact is valid on date `D` when `(valid_from IS NULL OR valid_from <= D) AND (valid_to IS NULL OR valid_to >= D)`.
  Both bounds are inclusive. Comparisons are plain string comparisons, which is correct only because dates are
  zero-padded `YYYY-MM-DD`.

## 4. Table `entities`

<!-- contract:entities -->
| Column | SQLite type | NOT NULL | Meaning |
|---|---|---|---|
| `id` | TEXT | no | Primary key. `sha256(name)` as hex, first 16 characters. |
| `name` | TEXT | yes | Entity name, same string as used in `triples.subject` / `triples.object`. |
| `type` | TEXT | no | Default `unknown`. OMPA sets `note` for notes with a frontmatter description, but only if the entity did not already exist (`INSERT OR IGNORE`). |
| `created_at` | TEXT | no | `YYYY-MM-DD HH:MM:SS`, UTC. |
<!-- /contract:entities -->

Every subject and object written through `add_triple()` also gets an `entities` row. Literals such as dates and tag
names therefore appear as entities too. Readers should not assume every entity is a person, project or note.

## 5. Provenance: `source_file`

`source_file` is how a reader explains where a fact came from. In v1 it holds one of:

| Writer | Value |
|---|---|
| `populate_from_note()` / `populate_from_vault()` / `ao_kg_populate` / `ao sync` | `str(note_path)`: the note path as OMPA saw it. Absolute when the vault was opened with an absolute path, relative to the OMPA process working directory otherwise. |
| `add_triple(..., source=...)` / `ao_kg_add` | Whatever the caller passed. Free text, may be `NULL`. |

Known weakness: v1 does not normalize this column. Readers that need a vault-relative path should strip the vault root
when the value starts with it, and otherwise treat the value as an opaque label. Normalizing it to a vault-relative
POSIX path is a planned **v2** change (see [ROADMAP](ROADMAP.md)).

## 6. Reserved predicates

OMPA writes these predicates when it builds the graph from vault notes. Other writers must not use them with a different
meaning.

<!-- contract:predicates -->
| Predicate | Subject | Object | Written from |
|---|---|---|---|
| `links_to` | note stem | wikilink target (text before any `\|`) | `[[wikilinks]]` in the note body |
| `has_tag` | note stem | tag | frontmatter `tags` (list or comma-separated string) |
| `in_folder` | note stem | top-level vault folder, e.g. `work` | note path |
| `in_subfolder` | note stem | `folder/subfolder`, e.g. `work/active` | note path |
| `created_on` | note stem | `YYYY-MM-DD` | frontmatter `date`; `valid_from` is set to the same date |
<!-- /contract:predicates -->

Any other predicate string is allowed (for example the ones agents add through `ao_kg_add`). Predicates are
case-sensitive and not normalized.

## 7. Indexes (informative, not part of the contract)

OMPA currently creates `idx_triples_subject`, `idx_triples_predicate`, `idx_triples_object`,
`idx_triples_subject_date (subject, valid_from)`, `idx_triples_object_pred (object, predicate)` and
`idx_triples_validity (valid_from, valid_to)`. They may change in any release. Readers must not depend on them.

## 8. Rules for readers (locus, pliamem, AegisFlow)

1. Open the file read-only: `sqlite3.connect("file:<path>?mode=ro", uri=True)`. Never write. In WAL mode a read-only
   reader still needs the `-wal`/`-shm` files to be readable and the directory to exist.
2. Check `PRAGMA user_version`. Accept 0 and 1. For any higher number, refuse or warn. Do not guess.
   `ompa.kg_schema.check_kg_schema(path)` does both steps and returns a list of problems.
3. Select columns **by name** (`SELECT subject, predicate, object, ... FROM triples`), never `SELECT *` by position.
   Ignore columns and tables you do not know.
4. Apply the validity rule from section 3 yourself if you answer "as of" questions.
5. Show `source_file` (or a vault-relative form of it) alongside any fact you return, so the answer can be audited.

## 9. Versioning and migration rules

**Additive changes. No version bump.** Readers that follow section 8 keep working.

* a new nullable column, or a column with a default, on `triples` or `entities`
* a new table or index
* a new reserved predicate (documented here in the same PR)

**Breaking changes. Bump `user_version` to 2 and publish `docs/kg-schema-v2.md`.**

* removing or renaming a contract column, or changing its type or NOT NULL flag
* changing what a column means: the `id` derivation, the date format, the timezone of `extracted_at`, the
  `source_file` format (see section 5), or the meaning of an existing reserved predicate
* changing the identity rule (one row per subject, predicate, object)

**How a breaking change ships.**

1. Write `docs/kg-schema-v2.md` and update `ompa/kg_schema.py` and `tests/test_kg_contract.py` in the same PR.
2. Add the data migration as a numbered step in `ompa/migration.py` (`VaultMigrator`), so `ao migrate-vault` upgrades
   old vaults, and so `--dry-run` shows the change first. The step sets `user_version` last, after the data is rewritten.
3. Release it in a new **minor** OMPA version at the earliest. Keep the v1 doc in the repo.
4. Open tracking issues in locus, pliamem and AegisFlow before release. Readers should accept both N and N+1 before OMPA
   starts writing N+1.
5. OMPA never downgrades `user_version`. An OMPA build that finds a higher version than it knows logs a warning and does
   not stamp the file.

**What counts as the contract.** Sections 2 to 6 and 8 to 9. Everything else in this document is explanation.
