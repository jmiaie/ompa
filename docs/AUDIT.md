# Audit memory

OMPA can keep a **tamper-evident, append-only audit log** of everything that
changes agent memory. It can attach **provenance** to every retrieval and every
fact, answer **"what did the agent know at time T"**, enforce **retention, legal
hold and right-to-erasure** policies, and enforce **per-vault access control**.

Everything here is stdlib-only (SQLite + `hashlib`/`hmac`). Ed25519 signing
needs the optional `cryptography` package: `pip install ompa[audit]`.

!!! note "Supports, not certifies"
    OMPA provides technical controls that **support** compliance programmes
    (see [Compliance mapping](#compliance-mapping)). Using OMPA does not make a
    system compliant or certified with any regulation or standard. Certification
    and attestation depend on your whole system, your processes and an assessor.

---

## Quick start

```python
from ompa import Ompa

ao = Ompa(vault_path="./vault", audit=True, principal="agent-7")   # audit on
ao.write("Kai owns the Orion budget. [[Kai]]", file_path="work/orion.md")
ao.kg_add("Kai", "works_on", "Orion")

hit = ao.search("Orion budget")[0]
hit.citation      # path, line/byte span, content_sha256, author, recorded_at, valid_from/to
ao.kg_query("Kai")[0].citation

report = ao.audit_verify()          # {"ok": True, "shared": {...chain report + drift...}}
head = ao.audit_head()              # store this OUTSIDE the vault (anchor)

with ao.as_of("2026-10-21T08:00:00Z") as view:   # transaction time, UTC
    view.facts("Kai")                             # what was known then

ao.forget("Zorblatt Quince")        # right to erasure (admin), tombstone kept
```

CLI:

```bash
ao audit enable --vault-path ./vault          # migrate an existing vault (baseline snapshot)
ao audit keygen ~/.ompa/audit.pem             # Ed25519 (needs ompa[audit]); --alg hmac for stdlib
ao audit verify --vault-path ./vault [--key ~/.ompa/audit.pem.pub] [--anchor head.json]
ao audit head  --vault-path ./vault > ~/anchors/$(date +%F).json
ao audit export bundle.json --vault-path ./vault --proof 42
ao audit verify-bundle bundle.json --key audit.pem.pub
ao audit as-of 2026-10-21T08:00:00Z Kai --vault-path ./vault
ao audit log --vault-path ./vault --last 20
ao forget "Zorblatt Quince" --alias "Z. Quince" --vault-path ./vault [--dry-run]
ao retention add 30 --tag scratch ; ao retention apply [--dry-run] ; ao retention list
ao hold add "Acme" --kind subject --reason "case 42" ; ao hold release h-1234abcd
ao acl init alice --reader team:eng --team eng=bob,carol ; ao acl show
```

MCP tools: `ao_audit_verify`, `ao_audit_head`, `ao_audit_export`, `ao_forget`
(dry run unless `dry_run: false`) and `ao_as_of`. Every tool accepts a
`principal` argument (see the [trust model](#identity-and-access-control-trust-model)).
`ao_search` and `ao_kg_query` results carry `citation`.

---

## What is recorded

Every memory mutation appends one entry to `.palace/audit.sqlite3`:

| action | when |
|---|---|
| `note.write` / `note.edit` / `note.delete` | `write()`, `update_brain()`, `export_to_shared()`, `import_to_personal()`, `delete_note()` |
| `note.observed` | `post_tool()` saw an agent write/edit a note outside the API |
| `note.baseline`, `fact.baseline`, `audit.baseline` | `enable_audit()` snapshot of pre-existing data |
| `fact.add` / `fact.merge` / `fact.invalidate` / `fact.delete` | knowledge-graph mutations, including facts derived from notes |
| `forget`, `retention.apply` | erasure / expiry **tombstones** (list the redacted entry seqs) |
| `policy.change`, `acl.change`, `audit.export` | governance changes |
| `access.denied`, `hold.blocked`, `forget.blocked` | refused attempts (who, what, which vault) |
| `audit.genesis`, `audit.key` | chain start; signing key registration |

Note entries store the path, size and **SHA-256 of the note**, not its text.
Fact entries store the fact (subject, predicate, object, validity interval,
source path/line/hash). One `write()` produces one audit transaction: the note
entry plus every fact derived from it. That is one SQLite commit and one head
update.

### Entry format

```text
entry_hash = SHA-256( canonical_json({v, seq, ts, actor, action, body_hash, prev}) )
body_hash  = SHA-256( canonical_json(body + {"_salt": 128 random bits}) )
```

* `canonical_json` = sorted keys, no whitespace, UTF-8.
* `prev` is the previous entry's hash (64 zeros for genesis).
* The **salt** makes every body commitment unguessable, so a redacted body cannot
  be confirmed by brute force ("was it Alice?").
* Every `checkpoint_interval` entries (default 1000, fixed at genesis) a
  **Merkle checkpoint** (RFC 6962 hashing) over that range is stored and chained
  to the previous checkpoint. It allows O(log n) **inclusion proofs** in
  evidence bundles.
* `audit.sqlite3.head.json` holds the latest `{seq, hash}` (signed when signing
  is on). It is written atomically in the same transaction as the entries, and
  it is how tail truncation is detected.

### Verification (`verify`, `ao audit verify`, `ao_audit_verify`)

Verification re-derives everything and reports each problem with its kind and seq:

| detects | how | issue kind |
|---|---|---|
| any edited field of any entry | header hash, body hash | `hash_mismatch`, `body_mismatch` |
| deleted entry | seq gap + broken prev link | `gap`, `prev_mismatch` |
| reordered entries | prev links / hashes | `prev_mismatch`, `hash_mismatch` |
| inserted (forged) entry | prev links of the next entry | `prev_mismatch`, `gap` |
| appended forged entry | head file behind the chain | `head_stale` |
| truncated tail | head file / anchors | `head_mismatch`, `anchor_mismatch` |
| deleted / emptied log | refuses to start a new chain over an existing head file | `AuditError` on open |
| body removed without a tombstone | redaction accounting | `unaccounted_redaction` |
| checkpoint edited / deleted | checkpoint chain, Merkle root, expected boundaries | `checkpoint_*` |
| corrupt, cut or padded database file | `PRAGMA quick_check`, read errors, file size vs page size and header page count | `db_error`, `db_size` |
| (with key) forged or re-hashed entries, stripped or re-keyed signatures | Ed25519 / HMAC signatures on entries, checkpoints, head | `bad_signature`, `key_changed`, `wrong_key`, `head_signature` |

`verify` also reports **drift**: notes whose current SHA-256 differs from the
last audited version (edited outside OMPA), notes never audited, and audited
notes that disappeared.

**Measured** (tests and `benchmarks/audit_tamper_mc.py`): see
[Test results](#test-results-and-measurements) below.

---

## Threat model

**Protects against.** Someone with write access to the vault files *after the
fact* who wants to silently change, remove, reorder or insert history. This
covers a compromised agent process, a careless script, a sync conflict, or a
malicious insider who edits SQLite directly. Any such change is detected on the
next `verify`, subject to the conditions below.

**Detection depends on what the attacker lacks:**

1. **Without a key and without external anchors**, the attacker can rewrite the
   whole database and head file consistently: re-hash every entry, rebuild
   every checkpoint, rewrite the head. **This cannot be detected from the files
   alone.** No local-only hash chain can prevent it, and we test this limit
   explicitly (`test_full_rehash_needs_anchor_or_key`, MC mode
   `unsigned_rehash`: 0% detected without an anchor, 100% with one).
2. **External anchors defeat full rewrites.** Periodically store `ao audit
   head` output somewhere the attacker cannot write: a ticket, a WORM bucket,
   another system's log, email to yourself. Pass the saved heads to
   `verify --anchor`. Any rewrite of history before an anchored head is
   detected.
3. **Signing defeats rewrites by anyone without the key.** HMAC needs the
   verifier to hold the same secret. Ed25519 lets auditors verify with only the
   public key. Keep the private key off the agent's machine, or at least
   outside the vault, with mode 0600. **An attacker who has the signing key can
   forge everything after the point where they obtained it.** Combine signing
   with anchors.

**Does not protect against:**

* Actions never routed through OMPA. Edits made with an editor or `git pull`
  are not logged as they happen; `verify` reports them as **drift** afterwards.
* Lies told *before* logging. The log records what the agent wrote, not
  whether it was true.
* Availability. An attacker can delete the vault. Deletion is detectable,
  not preventable.
* **Crash window.** Mutations are logged *after* the underlying write succeeds
  (note file / KG commit), in a separate SQLite database. A crash between the
  two leaves an unlogged change. `verify` reports it as drift for notes; it is
  not reported for KG facts. A crash between the head write and the commit
  reports `head_mismatch` (a false alarm, distinguishable by inspection).
* **Key rotation within one log** is not supported: a key change after the
  verified segment is reported as `key_changed`. To rotate, start a new log and
  anchor the old head in it.
* Confidentiality. The log is not encrypted; protect it with filesystem
  permissions or disk encryption.
* Multi-writer sync. A vault synced between machines that write concurrently
  forks the chain. Verify detects the fork; it is not merged.

### Identity and access control trust model

ACLs are enforced in the **API layer** (`Ompa` methods, the KG guard, and the
MCP dispatcher). They are not an OS boundary: a process with filesystem access
can read files directly. Principals are **self-asserted**: the `principal=`
argument, `$OMPA_PRINCIPAL`, or the MCP `principal` field. MCP over stdio has no
authentication. To pin the identity for an MCP server, set `$OMPA_PRINCIPAL` in
the server's environment. A client-supplied `principal` that differs from it is
then rejected (and logged). Treat ACLs as protection against *accidental*
cross-agent or cross-team access and as an accountability record, not as a
defence against a malicious local user. For hard isolation, use separate OS
users or containers per vault.

Denied attempts are always logged, even when they occur inside an audit
transaction that rolls back. A flood of denied requests therefore grows the
log; rate-limit untrusted callers upstream.

---

## Provenance and citations

`SearchResult.citation` (also in MCP `ao_search`):

```json
{"path": "/v/work/orion.md", "line_start": 9, "line_end": 9,
 "byte_start": 3087, "byte_end": 3160,
 "content_sha256": "…", "author": "agent-7",
 "recorded_at": "2026-10-21T07:16:35.123456Z", "recorded_source": "audit",
 "recorded_by": "agent-7", "audited_sha256": "…", "matches_audit": true,
 "valid_from": "2026-10-21", "valid_to": null, "stale": false}
```

* The span is for the matched chunk (semantic) or the line containing the most
  query terms (keyword). Byte offsets index the raw file bytes:
  `raw[byte_start:byte_end]`.
* `content_sha256` is the hash of the file *as read now*. `matches_audit`
  says whether that equals the last audited version. `stale` means the note
  changed after it was indexed.
* `recorded_at`/`recorded_by` (transaction time) come from the audit log, or
  from the file mtime when audit is off (`recorded_source: "mtime"`).
  `valid_from`/`valid_to` (valid time) come from frontmatter
  (`date`/`valid_from`, `valid_to`/`expires`). `author` comes from frontmatter
  `author`/`agent`, else the recording principal.

`Triple.citation` (`kg_query`, `timeline`, MCP `ao_kg_query`): source note path,
**line**, source `content_sha256`, `author` (agent id), `recorded_at`
(transaction time) and `valid_from`/`valid_to`. The new KG columns (`agent`,
`source_line`, `source_sha256`) are added in place to existing databases. Old
rows have them as `NULL`.

### Point in time: `as_of(t)`

`ao.as_of(t)` replays the audit chain up to transaction time `t` into a
temporary knowledge graph:

* `view.facts(entity, valid_at=None)`: facts *as recorded by* `t`. This is
  bitemporal: `valid_at` selects the valid-time instant and defaults to the
  date of `t`.
* `view.notes()`: note versions (path → sha256, ts, actor) that existed at `t`.
* `view.stats()`: entries replayed and redacted entries skipped.

`t` is ISO 8601; with no offset it is taken as UTC, and a bare date means the
end of that day (UTC). Erased or expired data is **not** reconstructable by
design. A redacted entry is skipped, including any *other* facts that shared
that entry's body.

---

## Data policies

`.palace/policies.json` (mode 0600). Changes require `admin` and are logged as
`policy.change`.

**Retention**: `add_retention_rule(ttl_days, tag=… | folder=… | predicate=… |
all=True)`, then `apply_retention(dry_run=False)` (CLI: `ao retention apply`).
Run it from cron or CI; nothing expires automatically in the background.

* Notes are aged by frontmatter `date`, else file mtime. Facts (`predicate=`)
  are aged by recorded time.
* Expired notes are deleted together with the facts derived from them and
  their index entries.
* The matching audit entry bodies are redacted, the `retention.apply`
  tombstone is appended, and all SQLite files are vacuumed.
* Held items are skipped and reported.

**Legal hold**: `add_legal_hold(value, kind="subject"|"path"|"tag",
reason=…)`. A covered note cannot be deleted, overwritten, expired or erased.
Blocked attempts raise `LegalHoldError` and are logged (`hold.blocked`,
`forget.blocked`). Release with `remove_policy(hold_id)`. Holds do **not**
stop new notes being written.

**Right to erasure**: `forget(subject | [subject, alias, …])`. Requires `admin`;
blocked by a covering hold. Matching is whole-word and case-insensitive; spaces,
`-` and `_` are interchangeable ("Ada Lovelace" ≈ "ada-lovelace"). It:

1. Deletes notes *named* after the subject, renames paths containing it, and
   redacts mentions (`[REDACTED]`) in every other note. It also scrubs other
   text files in the vault, including `palace.json` and PreCompact session-log
   JSON. `acl.json`, `policies.json` and the head file are never rewritten;
   matches in them are reported as `protected_matches`.
2. Deletes matching KG facts and entities, plus facts sourced from deleted
   notes, then re-derives facts for rewritten notes.
3. Drops the affected keyword (FTS5) and semantic (vectors + excerpts) index
   entries.
4. Redacts every matching audit-entry body. Hashes stay, so the chain still
   verifies. It then appends a `forget` tombstone listing the redacted seqs,
   with `subject_commitment = SHA-256(nonce ‖ 0x00 ‖ casefold(subject))`.
   The `nonce` is returned in the receipt and **stored nowhere else**: the
   requester can later prove which subject a tombstone covers, but the log
   alone does not reveal it.
5. Runs `VACUUM` with `secure_delete` and truncates the WAL of every SQLite
   file, so no deleted bytes remain in the database files.

The tests grep **every byte of every file** in the vault, including `.sqlite3`,
`-wal`, FTS and vector files, for the subject (case-insensitive). They expect
zero hits after `forget`.

**Not covered by erasure:** copies outside the vault (backups, snapshots, git
history, synced replicas, the LLM provider's logs), the agent's own context
window, filesystem-level remnants (unallocated blocks, SSD wear levelling,
journaling filesystems), and mentions that don't match the given names
(nicknames, misspellings, pronouns). Pass every alias, and erase copies outside
the vault with their own tools.

---

## Access control

`.palace/acl.json` (absent = open vault, the legacy behaviour):

```json
{"version": 1, "owner": "alice",
 "teams": {"eng": ["alice", "bob"]},
 "read": ["team:eng"], "write": ["alice"], "admin": ["alice"]}
```

* Entries are principal ids, `team:<name>` or `*`. `admin` ⊇ `write` ⊇ `read`.
* A malformed file **fails closed**: opening the vault raises.
* Each vault has its own ACL. In dual-vault mode, give the personal vault
  `AccessPolicy.template(owner)` (private) and the shared vault team grants.
* `set_acl()` / `ao acl init` installs one. Once an ACL exists, changing it
  requires `admin`. **Bootstrapping is unauthenticated**: whoever creates the
  first ACL owns the vault.
* An ACL forces audit on, because denials must be recorded.

| needs `read` | needs `write` | needs `admin` |
|---|---|---|
| `search`, `get_brain_note`, `kg_query`/`timeline`, `as_of`, `audit_verify`/`head`/`export`, `policies` | `write`, `update_brain`, `delete_note`, `kg_add`/`invalidate`, `kg_populate`, `sync`, `rebuild_index`, `post_tool` (raises after the fact) | `forget`, `apply_retention`, holds, retention rules, `set_acl`, `enable_audit` |

---

## Migration (existing vaults)

Audit is **off by default**, so nothing changes for existing users. A vault is
audited if `Ompa(audit=True)`, or if it already has `.palace/audit.sqlite3`
(or its head file, or `acl.json`).

1. Back up the vault.
2. Run `ao audit enable --vault-path VAULT [--key KEY]`, or call
   `Ompa(...).enable_audit()`. This creates the log and records a **baseline**
   (`note.baseline` per note with its SHA-256, `fact.baseline` per KG row).
   History *before* enabling is not reconstructable; the baseline is the
   audited starting state.
3. Optional: `ao audit keygen` and pass `--key` / `audit_key=` (or set
   `$OMPA_AUDIT_KEY_FILE`). Enabling signing on an existing log appends an
   `audit.key` entry. Earlier entries remain covered by the chain and are
   reported as an unsigned prefix.
4. Optional: `ao acl init OWNER …` and `ao retention add …`.
5. Schedule `ao audit verify` plus anchor storage (e.g. daily `ao audit head`
   to an external location), and `ao retention apply` if you use retention.

KG schema changes are additive (three nullable columns) and happen
automatically. The semantic index format is unchanged; citations are computed
at query time.

---

## Compliance mapping

OMPA **supports** the following controls. It does not by itself satisfy them,
and it is **not certified** under any of them.

| framework | requirement (paraphrased) | how OMPA supports it | what you still need |
|---|---|---|---|
| SOC 2 (TSC CC7.2/CC7.3, CC6.1, CC8.1) | monitor and log system activity, detect anomalies, logical access, change management | complete mutation log with actor + time; tamper-evident chain; denial logging; per-vault ACL | log review process, alerting, identity provider, auditor attestation |
| HIPAA Security Rule §164.312(b), (c)(1), (d) | audit controls; integrity of ePHI; person/entity authentication | audit controls + integrity verification (hash chain, signatures) | authentication (principals are self-asserted), encryption at rest/in transit, BAAs, risk analysis |
| GDPR Art. 17 (erasure), Art. 5(1)(e) (storage limitation), Art. 30 (records), Art. 5(2) (accountability) | erase personal data on request; keep no longer than necessary; demonstrate compliance | `forget()` with a verifiable tombstone and commitment; TTL retention; evidence bundles | erasure in backups and processors, lawful-basis assessment, DPIA, request handling |
| SEC Rule 17a-4(f) / FINRA 4511 | records preserved in non-rewriteable, non-erasable form or with a complete time-stamped audit trail; legal hold | append-only, signed, anchorable audit trail; legal hold blocks deletion | a compliant storage medium/third party (WORM or audit-trail system per 17a-4(f)(2)), designated third-party access, retention periods |
| ISO/IEC 27001 A.8.15 (logging), A.8.10 (information deletion) | produce, protect and analyse logs; delete information when no longer required | protected logs; deletion with proof | ISMS, log monitoring |

For 17a-4 in particular: SQLite on an ordinary filesystem is **not** WORM
storage. Ship the evidence bundles and anchors to a compliant store.

---

## Test results and measurements

See `benchmarks/README.md` (section *Audit memory*) for the full method. The
numbers below are copied from that run.

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


Interpretation:

* Audit adds about **0.5 ms per keyword-only write** (unsigned), 0.6 ms with HMAC and 0.8 ms with Ed25519. Most of this is the atomic head-file replace plus ~7 chain entries per write (the note and every fact derived from it). With semantic indexing on, writes are dominated by embedding.
* A standalone `kg_add` goes from ~40 µs to ~200 µs because each call is its own audited transaction. Batch with `ao.gov.txn()` (one head update per batch) when bulk loading.
* Verify is linear: ~7–9 µs/entry unsigned/HMAC (0.77 s / 0.92 s at 100k) and ~100 µs/entry with Ed25519 (~10 s at 100k). Storage is ~550–650 B/entry.
* Citations add ~0.2 ms per search. Pass `citations=False` to skip them.

**Regression check vs main** (default config, audit off; harness in `results/audit_20261021/harness`):

**fix_eval** (temporal MC 20 seeds, freshness, write latency, KG throughput, concurrency; rounds alternate main/branch):

| metric | main 2229175 (r0, r1) | branch, audit off (r0, r1) |
|---|---|---|
| temporal MC mean accuracy | 1.000, 1.000 | 1.000, 1.000 |
| temporal rows stored / true facts | 2114/2114, 2114/2114 | 2114/2114, 2114/2114 |
| freshness checks passed | 4/4, 4/4 | 4/4, 4/4 |
| write p50 (ms, semantic on, local model) | 6.03, 6.03 | 6.28, 5.82 |
| `Ompa.search` p50 (ms, semantic on) | 3.89, 3.68 | 4.80, 4.05 |
| KG writes/s | 28154, 29676 | 26219, 28481 |
| concurrency rows/attempted, errors (8 + 16 procs) | 4000/4000 e=0; 8000/8000 e=0, 4000/4000 e=0; 8000/8000 e=0 | 4000/4000 e=0; 8000/8000 e=0, 4000/4000 e=0; 8000/8000 e=0 |

**search_bench** (synthetic, stub embedder; p50 ms, median over rounds; hit@5 = content findability):

| N | rounds | default hybrid main → branch | keyword-only | semantic-only | `Ompa.search` (no semantic extra; branch adds citations) | hit@5 main/branch |
|---|---|---|---|---|---|---|
| 1,000 | 2 | 2.27 → 2.08 (-9%) | 1.10 → 1.04 (-6%) | 0.12 → 0.11 (-7%) | 1.12 → 1.21 (+8%) | 1.0/1.0 |
| 10,000 | 2 | 9.40 → 8.80 (-6%) | 7.09 → 6.88 (-3%) | 0.68 → 0.66 (-3%) | 7.05 → 7.55 (+7%) | 1.0/1.0 |
| 50,000 | 2 | 39.33 → 37.57 (-4%) | 31.75 → 31.28 (-1%) | 2.85 → 2.80 (-2%) | 33.92 → 33.98 (+0%) | 1.0/1.0 |


Within run-to-run noise except `Ompa.search`, which computes citations by default: +7–8% at 1k/10k notes and +17% in the fix_eval search loop (~+0.5 ms). Temporal accuracy, freshness, concurrency and findability are unchanged. One 50k round showed +24 MB cold-start RSS for the branch. It did not reproduce in five follow-up runs (crossed build/load trees: 419.4–419.7 MB for all four combinations; rerun: 419.0 vs 419.1 MB).

**Tests**: `tests/test_audit.py` and `tests/test_audit_property.py` (Hypothesis, derandomized, 150 examples each: any single-character edit of any column of any entry, any non-empty set of deleted rows, any non-identity reorder of any window, any file truncation; all detected).

