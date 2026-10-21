"""
Governance for OMPA vaults: access control, data policies, right-to-erasure
and point-in-time views, all recorded in the tamper-evident audit chain
(:mod:`ompa.audit`).

Files (per vault, under ``<vault>/.palace/``):

* ``audit.sqlite3`` (+ ``.head.json``): the hash-chained audit log.
* ``acl.json``: access control.  Absent = open vault (legacy behaviour).
* ``policies.json``: retention rules and legal holds.

See docs/AUDIT.md for the threat model and what is / is not guaranteed.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .audit import AuditLog, sha256_hex, utc_now
from .knowledge_graph import AccessDeniedError, KnowledgeGraph

logger = logging.getLogger(__name__)

AUDIT_FILE = "audit.sqlite3"
ACL_FILE = "acl.json"
POLICY_FILE = "policies.json"
PERMS = ("read", "write", "admin")  # each level implies the ones before it
REDACTION = "[REDACTED]"
# Text files scrubbed by forget() besides notes (palace.json, session logs, ...)
TEXT_SUFFIXES = {".md", ".json", ".jsonl", ".txt", ".yaml", ".yml", ".csv"}
# Never rewritten by forget(): security config and the audit head (integrity data)
_PROTECTED = {ACL_FILE, POLICY_FILE, AUDIT_FILE + ".head.json"}

__all__ = [
    "AccessDeniedError",
    "AccessPolicy",
    "LegalHoldError",
    "Policies",
    "PointInTimeView",
    "VaultGovernance",
]


class LegalHoldError(PermissionError):
    """Raised when a deletion/erasure would touch data under legal hold."""


def _atomic_json(path: Path, data: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _subjects(subject: str | list[str]) -> list[str]:
    items = [subject] if isinstance(subject, str) else list(subject)
    items = [str(x).strip() for x in items if str(x).strip()]
    if not items or any(len(x) < 2 for x in items):
        raise ValueError("forget() subject(s) must be at least 2 characters")
    return items


def subject_regex(subject: str | list[str]) -> re.Pattern:
    """Case-insensitive whole-word matcher for an erasure subject (or aliases).

    Words may be separated by any run of whitespace, ``-`` or ``_``, so
    "Ada Lovelace" also matches "ada-lovelace" and "Ada_Lovelace".  Letters
    and digits delimit words; ``_`` counts as a separator.
    """
    alts = []
    for item in _subjects(subject):
        words = [re.escape(w) for w in re.split(r"[\s_\-]+", item) if w]
        alts.append(r"[\s_\-]+".join(words))
    return re.compile(r"(?<![^\W_])(?:" + "|".join(alts) + r")(?![^\W_])", re.IGNORECASE)


def parse_time(t: str | datetime) -> str:
    """Normalise a time to the audit log's UTC ``...Z`` microsecond format.

    A bare date ``YYYY-MM-DD`` means the *end* of that day (UTC); a naive
    datetime is taken as UTC.
    """
    if isinstance(t, datetime):
        dt = t
    else:
        s = str(t).strip()
        if len(s) == 10:
            s += "T23:59:59.999999"
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


class AccessPolicy:
    """Per-vault ACL.

    ``acl.json``::

        {"version": 1,
         "owner": "alice",
         "teams": {"eng": ["alice", "bob"]},
         "read":  ["team:eng"],
         "write": ["alice"],
         "admin": ["alice"]}

    Entries are a principal id, ``team:<name>`` or ``*`` (anyone).
    ``admin`` implies ``write`` implies ``read``.  A malformed file fails
    closed (raises), it never silently opens the vault.
    """

    def __init__(self, data: dict, path: Path | None = None):
        if not isinstance(data, dict):
            raise ValueError("acl.json must be a JSON object")
        for k in PERMS:
            v = data.get(k, [])
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise ValueError(f"acl.json: '{k}' must be a list of strings")
        teams = data.get("teams", {})
        if not isinstance(teams, dict) or not all(
            isinstance(m, list) and all(isinstance(x, str) for x in m) for m in teams.values()
        ):
            raise ValueError("acl.json: 'teams' must map names to lists of principals")
        self.data = data
        self.path = path

    @classmethod
    def load(cls, path: Path) -> AccessPolicy | None:
        path = Path(path)
        if not path.exists():
            return None
        return cls(json.loads(path.read_text(encoding="utf-8")), path)

    @staticmethod
    def template(
        owner: str,
        readers: list[str] | None = None,
        writers: list[str] | None = None,
        admins: list[str] | None = None,
        teams: dict[str, list[str]] | None = None,
    ) -> dict:
        """A private vault for ``owner`` (plus any explicit grants)."""
        return {
            "version": 1,
            "owner": owner,
            "teams": teams or {},
            "read": list(readers or []),
            "write": list(writers or []),
            "admin": sorted({owner, *(admins or [])}),
        }

    def _match(self, principal: str, entry: str) -> bool:
        if entry == "*" or entry == principal:
            return True
        if entry.startswith("team:"):
            return principal in self.data.get("teams", {}).get(entry[5:], [])
        return False

    def allows(self, principal: str, perm: str) -> bool:
        if perm not in PERMS:
            raise ValueError(f"unknown permission {perm!r}")
        for level in PERMS[PERMS.index(perm) :]:
            if any(self._match(principal, e) for e in self.data.get(level, [])):
                return True
        return False

    def save(self, path: Path | None = None) -> None:
        _atomic_json(Path(path or self.path), self.data)


# ---------------------------------------------------------------------------
# Data policies: retention + legal holds
# ---------------------------------------------------------------------------


class Policies:
    """Retention rules and legal holds for one vault (``policies.json``).

    Retention rule: ``{"id", "ttl_days", "scope": {...}}`` where scope is one of
    ``{"tag": t}``, ``{"folder": "work/"}``, ``{"predicate": p}`` (KG facts,
    by recorded time) or ``{"all": true}`` (every note).  Notes are aged by
    frontmatter ``date`` if present, else file mtime.

    Legal hold: ``{"id", "kind": "subject"|"path"|"tag", "value", "reason"}``.
    A held note cannot be deleted, overwritten, expired or erased.
    """

    def __init__(self, data: dict | None = None, path: Path | None = None):
        self.data = data or {"version": 1, "retention": [], "holds": []}
        self.data.setdefault("retention", [])
        self.data.setdefault("holds", [])
        self.path = path

    @classmethod
    def load(cls, path: Path) -> Policies:
        path = Path(path)
        if path.exists():
            return cls(json.loads(path.read_text(encoding="utf-8")), path)
        return cls(None, path)

    def save(self) -> None:
        _atomic_json(self.path, self.data)

    @property
    def retention(self) -> list[dict]:
        return self.data["retention"]

    @property
    def holds(self) -> list[dict]:
        return self.data["holds"]

    def add_retention(self, ttl_days: float, **scope) -> dict:
        if (
            not scope
            or len(scope) != 1
            or next(iter(scope))
            not in (
                "tag",
                "folder",
                "predicate",
                "all",
            )
        ):
            raise ValueError("scope must be exactly one of tag=, folder=, predicate=, all=True")
        if float(ttl_days) <= 0:
            raise ValueError("ttl_days must be > 0")
        rule = {
            "id": "r-" + secrets.token_hex(4),
            "ttl_days": float(ttl_days),
            "scope": scope,
            "created": utc_now(),
        }
        self.retention.append(rule)
        return rule

    def add_hold(self, kind: str, value: str, reason: str = "") -> dict:
        if kind not in ("subject", "path", "tag"):
            raise ValueError("hold kind must be subject, path or tag")
        if not value:
            raise ValueError("hold value required")
        hold = {
            "id": "h-" + secrets.token_hex(4),
            "kind": kind,
            "value": value,
            "reason": reason,
            "created": utc_now(),
        }
        self.holds.append(hold)
        return hold

    def remove(self, rid: str) -> dict | None:
        for lst in (self.retention, self.holds):
            for i, r in enumerate(lst):
                if r["id"] == rid:
                    return lst.pop(i)
        return None

    def hold_for(self, rel_path: str, tags: list[str] | None = None, text: str = "") -> dict | None:
        """The first hold covering a note (by path prefix, tag or subject mention)."""
        tags = [str(t).lower() for t in (tags or [])]
        for h in self.holds:
            v = h["value"]
            if h["kind"] == "path" and (rel_path == v or rel_path.startswith(v.rstrip("/") + "/")):
                return h
            if h["kind"] == "tag" and v.lower() in tags:
                return h
            if h["kind"] == "subject":
                rx = subject_regex(v)
                if rx.search(rel_path) or rx.search(text):
                    return h
        return None

    def fact_held(self, *terms: str) -> dict | None:
        for h in self.holds:
            if h["kind"] == "subject":
                rx = subject_regex(h["value"])
                if any(t and rx.search(t) for t in terms):
                    return h
        return None


def _note_tags(text: str) -> list[str]:
    try:
        import frontmatter as fm

        tags = fm.loads(text).metadata.get("tags", [])
    except Exception:
        return []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    return [str(t) for t in tags if t] if isinstance(tags, list) else []


def _note_date(text: str, path: Path) -> datetime:
    m = re.search(r"^date:\s*['\"]?(\d{4}-\d{2}-\d{2})", text[:2000], re.MULTILINE)
    if m:
        with contextlib.suppress(ValueError):
            return datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)


# ---------------------------------------------------------------------------
# Per-vault governance context
# ---------------------------------------------------------------------------


class VaultGovernance:
    """Audit log + ACL + policies for one vault, acting as ``principal``."""

    def __init__(
        self,
        vault_path: Path,
        principal: str,
        audit: bool | None = None,
        signer=None,
        name: str = "shared",
    ):
        self.vault_path = Path(vault_path).resolve()
        self._root_prefix = str(self.vault_path) + os.sep
        self.palace = self.vault_path / ".palace"
        self.principal = principal
        self.name = name
        self.acl = AccessPolicy.load(self.palace / ACL_FILE)
        db = self.palace / AUDIT_FILE
        head = db.with_name(db.name + ".head.json")
        # a head file without its DB means the log was deleted: open it so that
        # AuditLog refuses loudly instead of silently running unaudited
        want = (
            audit if audit is not None else (db.exists() or head.exists() or self.acl is not None)
        )
        if not want and self.acl is not None:
            logger.warning("acl.json present: audit forced on for %s (denials are logged)", name)
            want = True
        self.created = bool(want) and not db.exists()
        self.audit: AuditLog | None = AuditLog(db, signer=signer, actor=principal) if want else None
        self.policies = Policies.load(self.palace / POLICY_FILE)

    # ---------------------------------------------------------------- helpers

    def rel(self, path: Path | str) -> str:
        """Vault-relative POSIX path (fast prefix path; resolve() only if needed)."""
        s = str(path)
        root = self._root_prefix
        if s.startswith(root):
            return s[len(root) :].replace(os.sep, "/")
        p = Path(path)
        with contextlib.suppress(ValueError, OSError):
            return p.resolve().relative_to(self.vault_path).as_posix()
        return p.as_posix()

    def txn(self):
        return self.audit.transaction() if self.audit is not None else contextlib.nullcontext()

    def log(self, action: str, body: dict, index=None):
        if self.audit is not None:
            return self.audit.append(action, body, actor=self.principal, index=index)
        return None

    def log_durable(self, action: str, body: dict) -> None:
        """Log an event that must persist even if the surrounding transaction rolls back."""
        if self.audit is not None:
            self.audit.append_durable(action, body, actor=self.principal)

    def require(self, perm: str, op: str, target: str | Path | None = None) -> None:
        """Raise AccessDeniedError (after logging ``access.denied``) unless allowed."""
        if self.acl is None or self.acl.allows(self.principal, perm):
            return
        if isinstance(target, Path):
            target = self.rel(target)
        self.log_durable(
            "access.denied", {"perm": perm, "op": op, "vault": self.name, "target": target}
        )
        raise AccessDeniedError(f"{self.principal!r} lacks {perm!r} on {self.name} vault ({op})")

    def kg_guard(self, op: str) -> None:
        self.require(op, "kg." + op)

    def check_hold_note(self, path: Path, op: str) -> None:
        if not self.policies.holds or not path.exists():
            return
        text = path.read_text(encoding="utf-8", errors="replace")
        h = self.policies.hold_for(self.rel(path), _note_tags(text), text)
        if h is not None:
            self.log_durable("hold.blocked", {"op": op, "hold": h["id"], "path": self.rel(path)})
            raise LegalHoldError(f"{self.rel(path)} is under legal hold {h['id']} ({op} blocked)")

    def log_note(self, action: str, path: Path, extra: dict | None = None) -> None:
        if self.audit is None:
            return
        rel = self.rel(path)
        if action == "note.delete":
            self.log(action, {"path": rel, "vault": self.name}, index=("note", rel, None))
            return
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        body = {"path": rel, "vault": self.name, "sha256": sha, "bytes": len(raw)}
        body.update(extra or {})
        self.log(action, body, index=("note", rel, sha))

    # ------------------------------------------------------------- migration

    def baseline(self, kg: KnowledgeGraph | None, exclude: list | None = None) -> dict:
        """Record the current notes and facts as the audited starting state."""
        from .semantic import scan_vault

        if self.audit is None:
            raise RuntimeError("audit is not enabled for this vault")
        notes = sorted(scan_vault(self.vault_path, exclude))
        n_facts = 0
        with self.txn():
            for p in notes:
                self.log_note("note.baseline", Path(p))
            if kg is not None:
                with kg._conn() as conn:
                    rows = conn.execute(
                        "SELECT id, subject, predicate, object, valid_from, valid_to, "
                        "confidence, source_file, extracted_at, agent, source_line, "
                        "source_sha256 FROM triples ORDER BY rowid"
                    ).fetchall()
                for r in rows:
                    self.log("fact.baseline", dict(zip(r.keys(), tuple(r), strict=True)))
                    n_facts += 1
            self.log("audit.baseline", {"vault": self.name, "notes": len(notes), "facts": n_facts})
        return {"notes": len(notes), "facts": n_facts}

    # ---------------------------------------------------------------- drift

    def drift(self, exclude: list | None = None) -> dict:
        """Compare files on disk with the latest audited state (from the chain)."""
        from .semantic import scan_vault

        state: dict[str, str | None] = {}
        if self.audit is not None:
            for e in self.audit.entries():
                if e.body is None or not e.action.startswith("note."):
                    continue
                rel = e.body.get("path")
                if rel is None:
                    continue
                state[rel] = None if e.action == "note.delete" else e.body.get("sha256")
        changed, unaudited = [], []
        seen = set()
        for p in scan_vault(self.vault_path, exclude):
            rel = self.rel(p)
            seen.add(rel)
            sha = hashlib.sha256(Path(p).read_bytes()).hexdigest()
            if rel not in state or state[rel] is None:
                unaudited.append(rel)
            elif state[rel] != sha:
                changed.append(rel)
        missing = [r for r, s in state.items() if s is not None and r not in seen]
        return {
            "changed_outside_audit": sorted(changed),
            "unaudited": sorted(unaudited),
            "missing": sorted(missing),
        }


# ---------------------------------------------------------------------------
# Right to erasure
# ---------------------------------------------------------------------------


def _iter_text_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        dirnames[:] = [x for x in dirnames if x not in (".git", "semantic_index")]
        for fn in filenames:
            p = d / fn
            # never follow symlinks: a link could point outside the vault
            if p.suffix.lower() in TEXT_SUFFIXES and fn not in _PROTECTED and not p.is_symlink():
                yield p


def _resolved(p) -> str:
    try:
        return str(Path(p).resolve())
    except OSError:
        return str(p)


def purge_index_paths(keyword, sem, targets: set[str], rx: re.Pattern | None = None) -> int:
    """Drop index entries whose path resolves into ``targets`` (or matches ``rx``).

    Index keys may be relative or absolute depending on how the vault path was
    given, so entries are matched by resolved path, never by string equality.
    """
    n = 0

    def hit(path: str) -> bool:
        return _resolved(path) in targets or (rx is not None and bool(rx.search(path)))

    if keyword is not None and getattr(keyword, "_conn", None) is not None:
        with keyword._lock:
            keys = [r[0] for r in keyword._conn.execute("SELECT path FROM files")]
        for k in keys:
            if hit(k):
                n += bool(keyword.remove_file(k))
    if sem is not None:
        with sem._lock:
            paths = set(sem.file_mtimes) | {m["path"] for m in sem._store.meta}
            for k in paths:
                if hit(k):
                    sem._store.remove_path(k)
                    sem.file_mtimes.pop(k, None)
                    sem._dirty = True
                    n += 1
    return n


def _redact_path(rel: str, rx: re.Pattern) -> str:
    return "/".join(rx.sub("REDACTED", part) for part in rel.split("/"))


def plan_forget(gov: VaultGovernance, subject: str) -> dict:
    """What forget() would touch in one vault (no changes)."""
    rx = subject_regex(subject)
    delete, redact, rename, scrub = [], [], [], []
    for p in _iter_text_files(gov.vault_path):
        rel = gov.rel(p)
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if p.suffix == ".md" and not rel.startswith(".palace/"):
            if rx.fullmatch(p.stem):
                delete.append(p)
                continue
            if rx.search(rel):
                rename.append(p)
            elif rx.search(text):
                redact.append(p)
        elif rx.search(text):
            scrub.append(p)
    protected = [
        n
        for n in _PROTECTED
        if (gov.palace / n).exists() and rx.search((gov.palace / n).read_text())
    ]
    return {
        "rx": rx,
        "delete": delete,
        "redact": redact,
        "rename": rename,
        "scrub": scrub,
        "protected_matches": protected,
    }


def forget_vault(
    ao, gov: VaultGovernance, kg, keyword, semantic_attr: str, subject: str, dry_run: bool = False
) -> dict:
    """Erase ``subject`` from one vault.  See :meth:`ompa.Ompa.forget`."""
    gov.require("admin", "forget")
    plan = plan_forget(gov, subject)
    rx = plan["rx"]
    nonce = secrets.token_hex(16)
    primary = _subjects(subject)[0]
    commitment = sha256_hex(nonce + "\0" + primary.casefold())

    # Legal holds: the subject itself, or any note we would touch.
    hold = gov.policies.fact_held(*_subjects(subject))
    if hold is None:
        for p in plan["delete"] + plan["redact"] + plan["rename"]:
            text = p.read_text(encoding="utf-8", errors="replace")
            hold = gov.policies.hold_for(gov.rel(p), _note_tags(text), text)
            if hold is not None:
                break
    if hold is not None:
        gov.log_durable("forget.blocked", {"subject_commitment": commitment, "hold": hold["id"]})
        raise LegalHoldError(f"erasure blocked by legal hold {hold['id']}")

    counts = {k: len(plan[k]) for k in ("delete", "redact", "rename", "scrub")}
    if dry_run:
        return {
            "vault": gov.name,
            "dry_run": True,
            **counts,
            "protected_matches": plan["protected_matches"],
        }

    vault = gov.vault_path
    key = lambda p: vault / gov.rel(p)  # noqa: E731 - index key form
    reindex: list[Path] = []
    removed: list[Path] = []
    # 1. files
    for p in plan["delete"]:
        removed.append(key(p))
        p.unlink()
    for p in plan["redact"] + plan["scrub"]:
        p.write_text(rx.sub(REDACTION, p.read_text(encoding="utf-8")), encoding="utf-8")
        if p in plan["redact"]:
            reindex.append(key(p))
    for p in plan["rename"]:
        new = vault / _redact_path(gov.rel(p), rx)
        new.parent.mkdir(parents=True, exist_ok=True)
        text = rx.sub(REDACTION, p.read_text(encoding="utf-8"))
        removed.append(key(p))
        p.unlink()
        new.write_text(text, encoding="utf-8")
        reindex.append(new)
    # remove now-empty directories whose names carried the subject
    for dirpath, _dirs, _files in sorted(os.walk(vault), key=lambda t: -len(t[0])):
        d = Path(dirpath)
        if d != vault and rx.search(d.name) and not any(d.iterdir()):
            d.rmdir()

    # 2. search indexes (keyword: FTS5; semantic: vectors + excerpts)
    targets = {_resolved(p) for p in removed + reindex}
    sem = getattr(ao, semantic_attr, None)
    sem_dir = vault / ".palace" / "semantic_index"
    if sem is None and (sem_dir / "index_meta.json").exists():
        from .semantic import SemanticIndex

        sem = SemanticIndex(sem_dir, embedding_backend=getattr(ao, "_embedding_backend", None))
        sem.load_index()
    if sem is not None:
        # also drop any chunk whose excerpt still mentions the subject
        with sem._lock:
            for m in list(sem._store.meta):
                if rx.search(m.get("text", "")):
                    targets.add(_resolved(m["path"]))
    purge_index_paths(keyword, sem, targets, rx)
    if keyword is not None:
        for p in reindex:
            keyword.update_file(p)
        keyword.compact()
    if sem is not None:
        if getattr(ao, semantic_attr, None) is not None and sem._model is not None:
            for p in reindex:
                sem.index_file(p)
        sem.save_index()

    # 3. knowledge graph
    facts_deleted = entities_deleted = 0
    if kg is not None:
        with kg._conn() as conn:
            rows = conn.execute("SELECT id, subject, object, source_file FROM triples").fetchall()
            names = [r[0] for r in conn.execute("SELECT name FROM entities")]
        ids = [
            r["id"]
            for r in rows
            if rx.search(r["subject"])
            or rx.search(r["object"])
            or rx.search(r["source_file"] or "")
        ]
        removed_strs = {_resolved(p) for p in removed}
        have = set(ids)
        ids += [
            r["id"]
            for r in rows
            if r["source_file"]
            and r["id"] not in have
            and _resolved(r["source_file"]) in removed_strs
        ]
        facts_deleted = kg.delete_ids(ids)
        entities_deleted = kg.delete_entities([n for n in names if rx.search(n)])

    # 4. audit chain: redact matching bodies + tombstone, then re-record new state
    receipt: dict[str, Any] = {"vault": gov.name}
    if gov.audit is not None:
        audit = gov.audit
        with audit.transaction():
            seqs = audit.find(rx.search)
            audit.redact(seqs)
            stale_keys = [k for k in audit.index_all("note") if rx.search(k)]
            audit.index_delete("note", stale_keys)
            tomb = audit.append(
                "forget",
                {
                    "subject_commitment": commitment,
                    "redacted_seqs": seqs,
                    "notes_deleted": counts["delete"],
                    "notes_redacted": counts["redact"],
                    "notes_renamed": counts["rename"],
                    "files_scrubbed": counts["scrub"],
                    "facts_deleted": facts_deleted,
                    "entities_deleted": entities_deleted,
                    "vault": gov.name,
                },
                actor=gov.principal,
            )
            for p in reindex:
                gov.log_note("note.edit", p, {"reason": "forget"})
        receipt.update(
            {"tombstone_seq": tomb.seq, "tombstone_hash": tomb.hash, "redacted_entries": len(seqs)}
        )
    # re-derive facts for the rewritten notes (now without the subject)
    if kg is not None:
        for p in reindex:
            kg.populate_from_note(p, vault)

    # 5. physically purge freed pages (SQLite) so nothing is recoverable from the files
    if kg is not None:
        kg.compact()
    if gov.audit is not None:
        gov.audit.compact()

    receipt.update(
        {
            **counts,
            "facts_deleted": facts_deleted,
            "entities_deleted": entities_deleted,
            "subject_commitment": commitment,
            # The nonce is NOT stored anywhere: keep it to later prove which
            # subject this tombstone refers to (sha256(nonce + "\\0" + subject)).
            "nonce": nonce,
            "protected_matches": plan["protected_matches"],
        }
    )
    return receipt


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def apply_retention_vault(
    ao,
    gov: VaultGovernance,
    kg,
    keyword,
    semantic_attr: str,
    now: datetime | None = None,
    dry_run: bool = False,
) -> dict:
    from .semantic import scan_vault

    gov.require("admin", "retention.apply")
    now = now or datetime.now(timezone.utc)
    notes_del: dict[str, Path] = {}
    held: list[str] = []
    fact_ids: list[str] = []
    fact_spo: set[tuple[str, str, str]] = set()
    applied = []
    for rule in gov.policies.retention:
        cutoff = now - timedelta(days=float(rule["ttl_days"]))
        scope = rule["scope"]
        applied.append({"id": rule["id"], "cutoff": cutoff.isoformat(timespec="seconds")})
        if "predicate" in scope and kg is not None:
            cut_sql = cutoff.strftime("%Y-%m-%d %H:%M:%S")
            with kg._conn() as conn:
                rows = conn.execute(
                    "SELECT id, subject, predicate, object FROM triples "
                    "WHERE predicate = ? AND extracted_at < ?",
                    (scope["predicate"], cut_sql),
                ).fetchall()
            for r in rows:
                if gov.policies.fact_held(r["subject"], r["object"]):
                    held.append(f"fact:{r['id']}")
                    continue
                fact_ids.append(r["id"])
                fact_spo.add((r["subject"], r["predicate"], r["object"]))
            continue
        for p in map(Path, scan_vault(gov.vault_path)):
            rel = gov.rel(p)
            if rel in notes_del:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            tags = _note_tags(text)
            if "tag" in scope and scope["tag"].lower() not in [t.lower() for t in tags]:
                continue
            if "folder" in scope and not rel.startswith(scope["folder"].rstrip("/") + "/"):
                continue
            if _note_date(text, p) >= cutoff:
                continue
            if gov.policies.hold_for(rel, tags, text) is not None:
                held.append(rel)
                continue
            notes_del[rel] = p
    if dry_run:
        return {
            "vault": gov.name,
            "dry_run": True,
            "notes": sorted(notes_del),
            "facts": len(fact_ids),
            "held_skipped": held,
            "rules": applied,
        }

    sem = getattr(ao, semantic_attr, None)
    srcs = {_resolved(p) for p in notes_del.values()}
    for p in notes_del.values():
        p.unlink()
    purge_index_paths(keyword, sem, srcs)
    if sem is not None:
        sem.save_index()
    if kg is not None:
        if notes_del:
            with kg._conn() as conn:
                rows = conn.execute(
                    "SELECT id, subject, predicate, object, source_file FROM triples"
                ).fetchall()
            for r in rows:
                if r["source_file"] and _resolved(r["source_file"]) in srcs:
                    fact_ids.append(r["id"])
                    fact_spo.add((r["subject"], r["predicate"], r["object"]))
        n_facts = kg.delete_ids(sorted(set(fact_ids)))
    else:
        n_facts = 0
    receipt: dict[str, Any] = {
        "vault": gov.name,
        "notes_deleted": len(notes_del),
        "facts_deleted": n_facts,
        "held_skipped": held,
        "rules": applied,
    }
    if gov.audit is not None and (notes_del or n_facts):
        paths = set(notes_del)

        def expired(body: str) -> bool:
            b = json.loads(body)
            if b.get("path") in paths:
                return True
            return (b.get("s"), b.get("p"), b.get("o")) in fact_spo

        with gov.audit.transaction():
            seqs = gov.audit.find(expired)
            gov.audit.redact(seqs)
            gov.audit.index_delete("note", sorted(paths))
            tomb = gov.audit.append(
                "retention.apply",
                {
                    "rules": [a["id"] for a in applied],
                    "notes_deleted": len(notes_del),
                    "facts_deleted": n_facts,
                    "redacted_seqs": seqs,
                    "vault": gov.name,
                },
                actor=gov.principal,
            )
        receipt.update({"tombstone_seq": tomb.seq, "redacted_entries": len(seqs)})
        if keyword is not None:
            keyword.compact()
        if kg is not None:
            kg.compact()
        gov.audit.compact()
    return receipt


# ---------------------------------------------------------------------------
# Point-in-time view ("what did the agent know at time T")
# ---------------------------------------------------------------------------


_BASELINE_COLS = (
    "id",
    "subject",
    "predicate",
    "object",
    "valid_from",
    "valid_to",
    "confidence",
    "source_file",
    "extracted_at",
    "agent",
    "source_line",
    "source_sha256",
)


class PointInTimeView:
    """Replay of the audit chain up to transaction time ``t``.

    ``facts(entity)`` answers from the knowledge graph *as recorded by* ``t``
    (bitemporal: pass ``valid_at`` to also pick the valid-time instant; it
    defaults to the date of ``t``).  ``notes()`` lists note versions
    (path -> sha256/ts/actor) that existed at ``t``.  Redacted (erased/expired)
    entries are skipped by design: erased data is not reconstructable.
    """

    def __init__(self, audit: AuditLog, t: str | datetime):
        self.t = parse_time(t)
        self._tmp = tempfile.mkdtemp(prefix="ompa-asof-")
        self.kg = KnowledgeGraph(db_path=str(Path(self._tmp) / "kg.sqlite3"))
        self._notes: dict[str, dict] = {}
        self.entries_replayed = 0
        self.redacted_skipped = 0
        self.last_seq = 0
        for e in audit.entries(until_ts=self.t):
            self.last_seq = e.seq
            if e.redacted or e.body is None:
                self.redacted_skipped += 1
                continue
            self._apply(e)
        self.kg.agent = None

    def _apply(self, e) -> None:
        b, a = e.body, e.action
        if a in ("fact.add", "fact.merge"):
            self.kg.add_triple(
                b["s"],
                b["p"],
                b["o"],
                valid_from=b.get("valid_from"),
                valid_to=b.get("valid_to"),
                confidence=b.get("confidence", 1.0),
                source=b.get("source"),
                source_line=b.get("source_line"),
                source_sha256=b.get("source_sha256"),
                agent=e.actor,
                recorded_at=e.ts,
            )
        elif a == "fact.invalidate":
            self.kg.invalidate(b["s"], b["p"], b["o"], ended=b.get("ended"))
        elif a == "fact.baseline":
            cols = [k for k in _BASELINE_COLS if k in b]
            with self.kg._conn() as conn:
                conn.execute(
                    f"INSERT OR REPLACE INTO triples ({', '.join(cols)}) "  # nosec B608
                    f"VALUES ({', '.join('?' * len(cols))})",
                    [b[c] for c in cols],
                )
        elif a == "fact.delete":
            self.kg.delete_ids(list(b.get("ids", [])))
        elif a.startswith("note.") and "path" in b:
            if a == "note.delete":
                self._notes.pop(b["path"], None)
            else:
                self._notes[b["path"]] = {
                    "sha256": b.get("sha256"),
                    "ts": e.ts,
                    "actor": e.actor,
                    "seq": e.seq,
                }
        else:
            return
        self.entries_replayed += 1

    def facts(self, entity: str, valid_at: str | None = None):
        return self.kg.query_entity(entity, as_of=valid_at or self.t[:10])

    def timeline(self, entity: str) -> list[dict]:
        return self.kg.timeline(entity)

    def notes(self) -> dict[str, dict]:
        return dict(self._notes)

    def stats(self) -> dict:
        return {
            "as_of": self.t,
            "last_seq": self.last_seq,
            "entries_replayed": self.entries_replayed,
            "redacted_skipped": self.redacted_skipped,
            "notes": len(self._notes),
            **self.kg.stats(),
        }

    def close(self) -> None:
        self.kg.close()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def __enter__(self) -> PointInTimeView:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
