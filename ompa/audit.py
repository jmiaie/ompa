"""
Tamper-evident audit log for OMPA memory mutations.

Every mutation (note write/edit/delete, fact add/merge/invalidate, forget,
retention, ACL denials, ...) is appended to a hash chain stored in SQLite
(``.palace/audit.sqlite3``):

* Each entry's *body* is canonical JSON (sorted keys, no whitespace, UTF-8)
  plus a random 128-bit salt.  The chain commits only to ``body_hash =
  SHA-256(body)``, so a body can later be *redacted* (right to erasure)
  without breaking the chain, and the salt stops anyone from brute-forcing
  the redacted content back out of the hash.
* The entry hash is ``SHA-256(canonical({v, seq, ts, actor, action,
  body_hash, prev}))``.  Any edit, deletion, reorder or insertion breaks the
  ``prev`` links or the hashes.
* Every ``checkpoint_interval`` entries a Merkle checkpoint (RFC 6962 tree
  over the entry hashes of that range) is written and chained to the
  previous checkpoint.  Checkpoints enable compact inclusion proofs in
  evidence bundles.
* A head file next to the database (``audit.sqlite3.head.json``) records the
  latest ``(seq, hash)``.  Removing entries from the tail of the database
  (truncation) no longer matches the head.
* Optional signing: Ed25519 (``pip install ompa[audit]`` -> ``cryptography``)
  or HMAC-SHA256 (stdlib).  Signatures cover every entry hash, checkpoint
  and the head.  Without a key that the attacker cannot read, someone who can
  rewrite the database *and* the head file can forge a consistent chain; see
  docs/AUDIT.md for the threat model and for external anchoring.

Stdlib only unless Ed25519 is used.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1
GENESIS_PREV = "0" * 64
BUNDLE_FORMAT = "ompa-audit-bundle/1"
DEFAULT_CHECKPOINT_INTERVAL = 1000
# actions whose bodies list the seqs they redacted (erasure tombstones)
TOMBSTONE_ACTIONS = ("forget", "retention.apply", "note.delete.redact")


class AuditError(Exception):
    """Raised when the audit log cannot be written."""


def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8 (no ASCII escaping)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def entry_hash(seq: int, ts: str, actor: str, action: str, body_hash: str, prev: str) -> str:
    header = {
        "v": FORMAT_VERSION,
        "seq": seq,
        "ts": ts,
        "actor": actor,
        "action": action,
        "body_hash": body_hash,
        "prev": prev,
    }
    return sha256_hex(canonical_json(header))


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


class HMACSigner:
    """HMAC-SHA256 signer (stdlib).  The same secret signs and verifies."""

    alg = "hmac-sha256"

    def __init__(self, key: bytes):
        if len(key) < 32:
            raise ValueError("HMAC audit key must be at least 32 bytes")
        self._key = key
        self.key_id = sha256_hex(b"ompa-audit-key-id:" + key)[:16]

    def sign(self, message: bytes) -> str:
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    def verify(self, message: bytes, sig: str | None) -> bool:
        return bool(sig) and hmac.compare_digest(self.sign(message), sig)

    def public_info(self) -> dict:
        return {"alg": self.alg, "key_id": self.key_id}


class Ed25519Verifier:
    """Verifies Ed25519 signatures with a public key (needs ``cryptography``)."""

    alg = "ed25519"

    def __init__(self, public_key):
        from cryptography.hazmat.primitives import serialization

        self._pub = public_key
        raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.key_id = sha256_hex(b"ompa-audit-key-id:" + raw)[:16]
        self.public_key_pem = public_key.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()

    @classmethod
    def from_pem(cls, pem: bytes | str) -> Ed25519Verifier:
        from cryptography.hazmat.primitives import serialization

        if isinstance(pem, str):
            pem = pem.encode()
        return cls(serialization.load_pem_public_key(pem))

    def verify(self, message: bytes, sig: str | None) -> bool:
        if not sig:
            return False
        from cryptography.exceptions import InvalidSignature

        try:
            self._pub.verify(base64.b64decode(sig), message)
            return True
        except (InvalidSignature, ValueError):
            return False

    def public_info(self) -> dict:
        return {"alg": self.alg, "key_id": self.key_id, "public_key_pem": self.public_key_pem}


class Ed25519Signer(Ed25519Verifier):
    def __init__(self, private_key):
        self._priv = private_key
        super().__init__(private_key.public_key())

    def sign(self, message: bytes) -> str:
        return base64.b64encode(self._priv.sign(message)).decode()


def generate_key(path: str | Path, alg: str = "ed25519") -> Path:
    """Create a new audit signing key at ``path`` (mode 0600).  Returns the path.

    ``alg="ed25519"`` needs the ``cryptography`` package; ``alg="hmac"`` is stdlib.
    Keep the key *outside* the vault (and ideally off the machine that runs the agent).
    """
    path = Path(path).expanduser()
    if path.exists():
        raise FileExistsError(path)
    if alg == "ed25519":
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        data = Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    elif alg == "hmac":
        data = secrets.token_hex(32).encode() + b"\n"
    else:
        raise ValueError(f"unknown alg {alg!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    if alg == "ed25519":
        # public half for verifiers / auditors (safe to distribute)
        pub = load_key(path).public_info()["public_key_pem"]
        path.with_name(path.name + ".pub").write_text(pub, encoding="utf-8")
    return path


def load_key(path: str | Path):
    """Load a signer (private Ed25519 PEM / HMAC secret) or a verifier (public PEM)."""
    data = Path(path).expanduser().read_bytes()
    if b"PRIVATE KEY" in data:
        from cryptography.hazmat.primitives import serialization

        return Ed25519Signer(serialization.load_pem_private_key(data, password=None))
    if b"PUBLIC KEY" in data:
        return Ed25519Verifier.from_pem(data)
    text = data.strip()
    try:
        key = bytes.fromhex(text.decode())
    except ValueError:
        key = text
    return HMACSigner(key)


# ---------------------------------------------------------------------------
# Merkle tree (RFC 6962 hashing)
# ---------------------------------------------------------------------------


def _leaf(h: bytes) -> bytes:
    return hashlib.sha256(b"\x00" + h).digest()


def _node(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


def _split(n: int) -> int:
    k = 1
    while k * 2 < n:
        k *= 2
    return k


def merkle_root(entry_hashes: list[str]) -> str:
    """RFC 6962 Merkle tree hash over hex entry hashes."""
    if not entry_hashes:
        return sha256_hex(b"")
    level = [_leaf(bytes.fromhex(h)) for h in entry_hashes]

    def mth(lo: int, hi: int) -> bytes:
        if hi - lo == 1:
            return level[lo]
        k = _split(hi - lo)
        return _node(mth(lo, lo + k), mth(lo + k, hi))

    return mth(0, len(level)).hex()


def merkle_proof(entry_hashes: list[str], index: int) -> list[list[str]]:
    """Audit path for leaf ``index``: list of ``[side, sibling_hex]``, bottom-up."""
    leaves = [_leaf(bytes.fromhex(h)) for h in entry_hashes]

    def mth(lo: int, hi: int) -> bytes:
        if hi - lo == 1:
            return leaves[lo]
        k = _split(hi - lo)
        return _node(mth(lo, lo + k), mth(lo + k, hi))

    def path(m: int, lo: int, hi: int) -> list[list[str]]:
        if hi - lo == 1:
            return []
        k = _split(hi - lo)
        if m < lo + k:
            return path(m, lo, lo + k) + [["R", mth(lo + k, hi).hex()]]
        return path(m, lo + k, hi) + [["L", mth(lo, lo + k).hex()]]

    return path(index, 0, len(leaves))


def verify_merkle_proof(entry_hash_hex: str, proof: list[list[str]], root_hex: str) -> bool:
    cur = _leaf(bytes.fromhex(entry_hash_hex))
    for side, sib in proof:
        s = bytes.fromhex(sib)
        cur = _node(cur, s) if side == "R" else _node(s, cur)
    return hmac.compare_digest(cur.hex(), root_hex)


def checkpoint_hash(cp_id: int, start: int, end: int, root: str, prev_cp: str, ts: str) -> str:
    return sha256_hex(
        canonical_json(
            {"id": cp_id, "start": start, "end": end, "root": root, "prev_cp": prev_cp, "ts": ts}
        )
    )


# ---------------------------------------------------------------------------
# Verification report
# ---------------------------------------------------------------------------


@dataclass
class VerifyIssue:
    kind: str
    seq: int | None
    detail: str


@dataclass
class VerifyReport:
    ok: bool = True
    entries: int = 0
    checkpoints: int = 0
    head_seq: int | None = None
    head_hash: str | None = None
    signatures_checked: bool = False
    sign_from: int | None = None
    redacted_entries: int = 0
    issues: list[VerifyIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0

    def add(self, kind: str, seq: int | None, detail: str) -> None:
        self.ok = False
        if len(self.issues) < 1000:
            self.issues.append(VerifyIssue(kind, seq, detail))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditEntry:
    seq: int
    ts: str
    actor: str
    action: str
    body: dict | None
    hash: str
    redacted: bool = False


# ---------------------------------------------------------------------------
# The log
# ---------------------------------------------------------------------------


class AuditLog:
    """Append-only, hash-chained audit log in SQLite.  Thread- and process-safe."""

    def __init__(
        self,
        db_path: str | Path,
        signer=None,
        actor: str = "agent",
        checkpoint_interval: int = DEFAULT_CHECKPOINT_INTERVAL,
        readonly: bool = False,
    ):
        """Open (or create) the log.

        ``readonly=True`` opens an existing log for verification only: no schema
        changes, no genesis/key entries, appends fail.  An empty log whose head
        file records a later entry is refused (the log was deleted/truncated);
        it is never silently re-created.
        """
        self.db_path = Path(db_path)
        self.head_path = self.db_path.with_name(self.db_path.name + ".head.json")
        self.readonly = readonly
        self.signer = signer
        self.actor = actor
        self.checkpoint_interval = max(1, int(checkpoint_interval))
        self._lock = threading.RLock()
        self._depth = 0
        self._pending_head: tuple[int, str] | None = None
        self._deferred: list[tuple[str, dict | None, str | None]] = []
        # (seq, hash) of the last entry, valid only inside a write transaction
        # (BEGIN IMMEDIATE holds SQLite's write lock, so nobody else can append)
        self._tail: tuple[int, str] | None = None
        self._tail_valid = False
        if readonly:
            if not self.db_path.exists():
                raise AuditError(f"{self.db_path} does not exist")
            self._conn = sqlite3.connect(
                self.db_path.resolve().as_uri() + "?mode=ro",
                uri=True,
                check_same_thread=False,
                timeout=60,
                isolation_level=None,
            )
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, timeout=60, isolation_level=None
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA secure_delete=ON")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS audit_entries (
                seq INTEGER PRIMARY KEY,
                ts TEXT NOT NULL,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                body TEXT,
                body_hash TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                hash TEXT NOT NULL,
                sig TEXT,
                redacted INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_entries(ts);
            CREATE TABLE IF NOT EXISTS audit_checkpoints (
                id INTEGER PRIMARY KEY,
                start_seq INTEGER NOT NULL,
                end_seq INTEGER NOT NULL,
                root TEXT NOT NULL,
                prev_cp TEXT NOT NULL,
                cp_hash TEXT NOT NULL,
                ts TEXT NOT NULL,
                sig TEXT
            );
            -- Derived lookup cache (NOT part of the chain): latest entry per key,
            -- e.g. ("note", <path>) -> who/when/sha of the last audited write.
            -- verify() recomputes it from the chain and reports any divergence.
            CREATE TABLE IF NOT EXISTS audit_index (
                kind TEXT NOT NULL,
                key TEXT NOT NULL,
                seq INTEGER NOT NULL,
                ts TEXT NOT NULL,
                actor TEXT NOT NULL,
                digest TEXT,
                PRIMARY KEY (kind, key)
            );
            """
        )
        with self._lock:
            info = signer.public_info() if signer is not None else None
            if info is not None:
                info = {k: v for k, v in info.items() if k != "public_key_pem"}
            if self._last() is None:
                recorded = self._head_seq_on_disk()
                if recorded:
                    self._conn.close()
                    raise AuditError(
                        f"{self.db_path.name} is empty but {self.head_path.name} records "
                        f"seq {recorded}: the audit log was deleted or truncated. Refusing "
                        "to start a new chain over it (move both files aside to reset)."
                    )
                self.append(
                    "audit.genesis",
                    {
                        "format": FORMAT_VERSION,
                        "signing": info,
                        "checkpoint_interval": self.checkpoint_interval,
                    },
                )
            else:
                self._adopt_interval()
            if (
                info is not None
                and hasattr(signer, "sign")
                and self.current_key_id() != info["key_id"]
            ):
                # signing enabled on an existing (unsigned or differently keyed) log
                self.append("audit.key", {"signing": info})

    # ------------------------------------------------------------ internals

    def _adopt_interval(self) -> None:
        """The checkpoint interval is fixed at genesis (verify relies on it)."""
        row = self._conn.execute(
            "SELECT body FROM audit_entries WHERE seq = 1 AND action = 'audit.genesis'"
        ).fetchone()
        if row and row[0]:
            with contextlib.suppress(ValueError, TypeError):
                iv = json.loads(row[0]).get("checkpoint_interval")
                if iv:
                    self.checkpoint_interval = int(iv)

    def _head_seq_on_disk(self) -> int | None:
        try:
            return int(json.loads(self.head_path.read_text(encoding="utf-8"))["seq"])
        except FileNotFoundError:
            return None
        except (ValueError, KeyError, TypeError, OSError):
            return -1  # unreadable head: treat as "something was here"

    def _last(self) -> tuple[int, str] | None:
        row = self._conn.execute(
            "SELECT seq, hash FROM audit_entries ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return (row[0], row[1]) if row else None

    def _sign(self, hex_hash: str) -> str | None:
        if self.signer is None or not hasattr(self.signer, "sign"):
            return None
        return self.signer.sign(bytes.fromhex(hex_hash))

    def _head_record(self, seq: int, h: str) -> dict:
        rec = {"seq": seq, "hash": h, "ts": utc_now()}
        if self.signer is not None and hasattr(self.signer, "sign"):
            rec["key_id"] = self.signer.key_id
            rec["sig"] = self.signer.sign(canonical_json({"seq": seq, "hash": h}).encode())
        return rec

    def _write_head(self, seq: int, h: str) -> bytes | None:
        """Atomically replace the head file; returns the previous content (for rollback)."""
        old = self.head_path.read_bytes() if self.head_path.exists() else None
        tmp = self.head_path.with_name(self.head_path.name + f".{os.getpid()}.tmp")
        tmp.write_text(canonical_json(self._head_record(seq, h)), encoding="utf-8")
        os.replace(tmp, self.head_path)
        return old

    def _restore_head(self, old: bytes | None) -> None:
        try:
            if old is None:
                self.head_path.unlink(missing_ok=True)
            else:
                self.head_path.write_bytes(old)
        except OSError:
            pass

    @contextmanager
    def transaction(self) -> Iterator[AuditLog]:
        """Group several appends into one SQLite transaction (one commit, one head write)."""
        with self._lock:
            outer = self._depth == 0
            if outer:
                self._conn.execute("BEGIN IMMEDIATE")
                self._pending_head = None
                self._tail_valid = False
            self._depth += 1
            try:
                yield self
            except BaseException:
                self._depth -= 1
                if outer:
                    self._tail_valid = False
                    self._conn.execute("ROLLBACK")
                    self._pending_head = None
                    self._flush_deferred()
                raise
            self._depth -= 1
            if outer:
                old = None
                wrote = False
                try:
                    if self._pending_head is not None:
                        old = self._write_head(*self._pending_head)
                        wrote = True
                    self._conn.execute("COMMIT")
                except BaseException:
                    if wrote:
                        self._restore_head(old)
                    with contextlib.suppress(sqlite3.Error):
                        self._conn.execute("ROLLBACK")
                    raise
                finally:
                    self._pending_head = None
                    self._tail_valid = False
                    self._flush_deferred()

    def _flush_deferred(self) -> None:
        while self._deferred:
            action, body, actor = self._deferred.pop(0)
            try:
                self.append(action, body, actor)
            except AuditError as e:  # never mask the caller's own exception
                logger.error("deferred audit append %s failed: %s", action, e)

    def append_durable(self, action: str, body: dict | None = None, actor: str | None = None):
        """Append an entry that must survive even if the enclosing transaction rolls back
        (e.g. ``access.denied``).  Inside a transaction it is queued and written right
        after the transaction ends; otherwise it is written immediately."""
        with self._lock:
            if self._depth == 0:
                return self.append(action, body, actor)
            self._deferred.append((action, body, actor))
            return None

    def _maybe_checkpoint(self, seq: int) -> None:
        if seq % self.checkpoint_interval == 0:
            self._checkpoint_locked(seq)

    def _checkpoint_locked(self, end: int) -> dict | None:
        row = self._conn.execute(
            "SELECT id, end_seq, cp_hash FROM audit_checkpoints ORDER BY id DESC LIMIT 1"
        ).fetchone()
        cp_id, start, prev_cp = (row[0] + 1, row[1] + 1, row[2]) if row else (1, 1, GENESIS_PREV)
        if end < start:
            return None
        hashes = [
            r[0]
            for r in self._conn.execute(
                "SELECT hash FROM audit_entries WHERE seq BETWEEN ? AND ? ORDER BY seq",
                (start, end),
            )
        ]
        root = merkle_root(hashes)
        ts = utc_now()
        cph = checkpoint_hash(cp_id, start, end, root, prev_cp, ts)
        self._conn.execute(
            "INSERT INTO audit_checkpoints VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (cp_id, start, end, root, prev_cp, cph, ts, self._sign(cph)),
        )
        return {"id": cp_id, "start": start, "end": end, "root": root, "cp_hash": cph}

    # ------------------------------------------------------------ public API

    def append(
        self,
        action: str,
        body: dict | None = None,
        actor: str | None = None,
        index: tuple[str, str, str | None] | None = None,
    ) -> AuditEntry:
        """Append one entry and return it.  Raises :class:`AuditError` on failure.

        ``index=(kind, key, digest)`` also updates the derived lookup cache
        (``digest=None`` with action ending in ``.delete`` removes the key).
        """
        if self.readonly:
            raise AuditError("audit log opened read-only")
        body = dict(body or {})
        body["_salt"] = secrets.token_hex(16)
        body_text = canonical_json(body)
        body_hash = sha256_hex(body_text)
        actor = actor or self.actor
        try:
            with self.transaction():
                if not self._tail_valid:
                    self._tail, self._tail_valid = self._last(), True
                last = self._tail
                seq, prev = (last[0] + 1, last[1]) if last else (1, GENESIS_PREV)
                ts = utc_now()
                h = entry_hash(seq, ts, actor, action, body_hash, prev)
                self._conn.execute(
                    "INSERT INTO audit_entries "
                    "(seq, ts, actor, action, body, body_hash, prev_hash, hash, sig) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (seq, ts, actor, action, body_text, body_hash, prev, h, self._sign(h)),
                )
                if index is not None:
                    kind, key, digest = index
                    if action.endswith(".delete"):
                        self._conn.execute(
                            "DELETE FROM audit_index WHERE kind = ? AND key = ?", (kind, key)
                        )
                    else:
                        self._conn.execute(
                            "INSERT OR REPLACE INTO audit_index VALUES (?, ?, ?, ?, ?, ?)",
                            (kind, key, seq, ts, actor, digest),
                        )
                self._maybe_checkpoint(seq)
                self._pending_head = (seq, h)
                self._tail = (seq, h)
        except sqlite3.Error as e:
            raise AuditError(f"audit append failed: {e}") from e
        return AuditEntry(seq, ts, actor, action, body, h)

    def checkpoint(self) -> dict | None:
        """Write a Merkle checkpoint for all entries since the last one (if any)."""
        with self.transaction():
            last = self._last()
            return self._checkpoint_locked(last[0]) if last else None

    def current_key_id(self) -> str | None:
        """Key id registered by the latest genesis/``audit.key`` entry (None = unsigned)."""
        row = self._conn.execute(
            "SELECT body FROM audit_entries WHERE action IN ('audit.genesis', 'audit.key') "
            "AND body IS NOT NULL ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return (json.loads(row[0]).get("signing") or {}).get("key_id")

    def index_get(self, kind: str, key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT seq, ts, actor, digest FROM audit_index WHERE kind = ? AND key = ?",
            (kind, key),
        ).fetchone()
        return {"seq": row[0], "ts": row[1], "actor": row[2], "digest": row[3]} if row else None

    def index_all(self, kind: str) -> dict[str, dict]:
        return {
            k: {"seq": s, "ts": t, "actor": a, "digest": d}
            for k, s, t, a, d in self._conn.execute(
                "SELECT key, seq, ts, actor, digest FROM audit_index WHERE kind = ?", (kind,)
            )
        }

    def index_delete(self, kind: str, keys: list[str]) -> None:
        with self.transaction():
            for k in keys:
                self._conn.execute("DELETE FROM audit_index WHERE kind = ? AND key = ?", (kind, k))

    def head(self) -> dict:
        """Latest ``{seq, hash, ts}`` -- publish/store it elsewhere to anchor the log."""
        last = self._last()
        return {"seq": last[0], "hash": last[1], "ts": utc_now()} if last else {}

    def __len__(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM audit_entries").fetchone()[0]

    def entries(
        self, start: int = 1, end: int | None = None, until_ts: str | None = None
    ) -> Iterator[AuditEntry]:
        q = "SELECT seq, ts, actor, action, body, hash, redacted FROM audit_entries WHERE seq >= ?"
        args: list[Any] = [start]
        if end is not None:
            q += " AND seq <= ?"
            args.append(end)
        q += " ORDER BY seq"
        for seq, ts, actor, action, body, h, red in self._conn.execute(q, args).fetchall():
            if until_ts is not None and ts > until_ts:
                continue
            yield AuditEntry(
                seq, ts, actor, action, json.loads(body) if body else None, h, bool(red)
            )

    def redact(self, seqs: list[int]) -> int:
        """Remove the bodies of ``seqs`` (hash commitments stay).  Caller must log a
        tombstone listing these seqs (see :data:`TOMBSTONE_ACTIONS`)."""
        if not seqs:
            return 0
        with self.transaction():
            n = 0
            for s in seqs:
                cur = self._conn.execute(
                    "UPDATE audit_entries SET body = NULL, redacted = 1 "
                    "WHERE seq = ? AND redacted = 0",
                    (s,),
                )
                n += cur.rowcount
        return n

    def find(self, predicate) -> list[int]:
        """Seqs of entries whose (unredacted) body text satisfies ``predicate(text)``.

        Genesis/key entries and tombstones are never returned: redacting them
        would break signature-key tracking or redaction accounting.
        """
        skip = ("audit.genesis", "audit.key", *TOMBSTONE_ACTIONS)
        return [
            seq
            for seq, action, body in self._conn.execute(
                "SELECT seq, action, body FROM audit_entries WHERE body IS NOT NULL ORDER BY seq"
            )
            if action not in skip and predicate(body)
        ]

    def compact(self) -> None:
        """VACUUM + WAL truncate so redacted bodies leave no recoverable bytes behind."""
        with self._lock:
            self._conn.execute("VACUUM")
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------ verification

    def _file_size_issue(self) -> str | None:
        """SQLite only ever writes whole pages, so a file that is not a whole number of
        pages, or (with no WAL pending) disagrees with the page count in its own
        header, was cut or padded outside SQLite."""
        try:
            size = self.db_path.stat().st_size
            with open(self.db_path, "rb") as f:
                hdr = f.read(100)
        except OSError as e:
            return f"cannot stat database file: {e}"
        if len(hdr) < 100 or not hdr.startswith(b"SQLite format 3\x00"):
            return "database file header missing or invalid"
        page_size = int.from_bytes(hdr[16:18], "big")
        page_size = 65536 if page_size == 1 else page_size
        if page_size < 512 or size % page_size:
            return f"file size {size} is not a multiple of the page size {page_size}"
        wal = self.db_path.with_name(self.db_path.name + "-wal")
        wal_pending = wal.exists() and wal.stat().st_size > 0
        change_counter, valid_for = hdr[24:28], hdr[92:96]
        header_pages = int.from_bytes(hdr[28:32], "big")
        if (
            not wal_pending
            and change_counter == valid_for
            and header_pages
            and header_pages * page_size != size
        ):
            return f"file has {size // page_size} pages but its header records {header_pages}"
        return None

    def verify(
        self,
        verifier=None,
        anchors: list[dict] | None = None,
        check_head: bool = True,
    ) -> VerifyReport:
        """Re-check the whole chain, checkpoints, head file and (optionally) signatures.

        Detects edited, deleted, reordered or inserted entries, edited bodies,
        unaccounted redactions, tampered checkpoints and tail truncation (via the
        head file and any ``anchors`` = earlier :meth:`head` values).
        """
        import time

        t0 = time.perf_counter()
        rep = VerifyReport()
        verifier = verifier if verifier is not None else self.signer
        size_issue = self._file_size_issue()
        if size_issue:
            rep.add("db_size", None, size_issue)
        try:
            qc = [r[0] for r in self._conn.execute("PRAGMA quick_check")]
            if qc != ["ok"]:
                rep.add("db_error", None, "SQLite quick_check: " + "; ".join(qc[:3]))
            rows = self._conn.execute(
                "SELECT seq, ts, actor, action, body, body_hash, prev_hash, hash, sig, redacted "
                "FROM audit_entries ORDER BY seq"
            )
            info = _verify_rows(rows, rep, verifier)
            cps = self._conn.execute(
                "SELECT id, start_seq, end_seq, root, prev_cp, cp_hash, ts, sig "
                "FROM audit_checkpoints ORDER BY id"
            ).fetchall()
            hashes = {r[0]: r[1] for r in self._conn.execute("SELECT seq, hash FROM audit_entries")}
            _verify_checkpoints(
                cps,
                hashes,
                rep,
                verifier if rep.signatures_checked else None,
                rep.sign_from,
                interval=info.get("checkpoint_interval"),
                last_seq=rep.head_seq,
            )
        except sqlite3.DatabaseError as e:
            rep.add("db_error", None, str(e))
            rep.seconds = time.perf_counter() - t0
            return rep

        if check_head:
            if not self.head_path.exists():
                rep.add("head_missing", None, f"{self.head_path.name} is missing")
            else:
                try:
                    head = json.loads(self.head_path.read_text(encoding="utf-8"))
                    _check_anchor(head, hashes, rep, "head_mismatch")
                    if rep.head_seq is not None and int(head["seq"]) < rep.head_seq:
                        rep.add(
                            "head_stale",
                            int(head["seq"]),
                            f"{rep.head_seq - int(head['seq'])} entries after the recorded "
                            "head (appended without updating the head: forged?)",
                        )
                    if rep.signatures_checked:
                        msg = canonical_json({"seq": head["seq"], "hash": head["hash"]}).encode()
                        if not verifier.verify(msg, head.get("sig")):
                            rep.add("head_signature", head.get("seq"), "head signature invalid")
                except (ValueError, KeyError, TypeError) as e:
                    rep.add("head_corrupt", None, str(e))
        for a in anchors or []:
            _check_anchor(a, hashes, rep, "anchor_mismatch")
        rep.seconds = time.perf_counter() - t0
        return rep

    # ------------------------------------------------------------ evidence

    def export_bundle(
        self, start: int = 1, end: int | None = None, proofs_for: list[int] | None = None
    ) -> dict:
        """Self-contained JSON evidence bundle for entries ``start..end``.

        Includes the entries (redacted ones without bodies), the hash preceding
        ``start``, overlapping checkpoints, Merkle inclusion proofs for
        ``proofs_for`` and the signing algorithm / key id (and Ed25519 public
        key).  Check it offline with :func:`verify_bundle`.
        """
        with self._lock:
            last = self._last()
            end = end if end is not None else (last[0] if last else 0)
            prev = GENESIS_PREV
            if start > 1:
                row = self._conn.execute(
                    "SELECT hash FROM audit_entries WHERE seq = ?", (start - 1,)
                ).fetchone()
                prev = row[0] if row else GENESIS_PREV
            cols = (
                "seq",
                "ts",
                "actor",
                "action",
                "body",
                "body_hash",
                "prev_hash",
                "hash",
                "sig",
                "redacted",
            )
            entries = [
                dict(zip(cols, r, strict=True))
                for r in self._conn.execute(
                    f"SELECT {', '.join(cols)} FROM audit_entries "  # nosec B608 - fixed columns
                    "WHERE seq BETWEEN ? AND ? ORDER BY seq",
                    (start, end),
                )
            ]
            cp_cols = ("id", "start_seq", "end_seq", "root", "prev_cp", "cp_hash", "ts", "sig")
            cps = [
                dict(zip(cp_cols, r, strict=True))
                for r in self._conn.execute(
                    "SELECT id, start_seq, end_seq, root, prev_cp, cp_hash, ts, sig "
                    "FROM audit_checkpoints WHERE end_seq >= ? AND start_seq <= ? ORDER BY id",
                    (start, end),
                )
            ]
            proofs = {}
            for s in proofs_for or []:
                for cp in cps:
                    if cp["start_seq"] <= s <= cp["end_seq"]:
                        hs = [
                            r[0]
                            for r in self._conn.execute(
                                "SELECT hash FROM audit_entries WHERE seq BETWEEN ? AND ? ORDER BY seq",
                                (cp["start_seq"], cp["end_seq"]),
                            )
                        ]
                        proofs[str(s)] = {
                            "checkpoint_id": cp["id"],
                            "path": merkle_proof(hs, s - cp["start_seq"]),
                        }
        for e in entries:
            e["redacted"] = bool(e["redacted"])
        signing = self.signer.public_info() if self.signer is not None else None
        seal = None
        if self.signer is not None and hasattr(self.signer, "sign"):
            seal = self.signer.sign(_bundle_seal_msg([start, end], prev, entries))
        return {
            "format": BUNDLE_FORMAT,
            "seal": seal,
            "generated_at": utc_now(),
            "db_head": {"seq": last[0], "hash": last[1]} if last else None,
            "range": [start, end],
            "prev_hash": prev,
            "signing": signing,
            "entries": entries,
            "checkpoints": cps,
            "proofs": proofs,
            "how_to_verify": "ompa.audit.verify_bundle(bundle, verifier) or `ao audit verify-bundle FILE`",
        }


def _bundle_seal_msg(rng: list, prev: str, entries: list[dict]) -> bytes:
    """What a bundle seal signs: the range, the preceding hash and the last hash."""
    last = entries[-1]["hash"] if entries else prev
    return canonical_json({"range": list(rng), "prev_hash": prev, "last_hash": last}).encode()


def _check_anchor(anchor: dict, hashes: dict[int, str], rep: VerifyReport, kind: str) -> None:
    seq, h = anchor.get("seq"), anchor.get("hash")
    if seq is None:
        return
    have = hashes.get(int(seq))
    if have is None:
        rep.add(kind, seq, f"entry {seq} recorded in head/anchor is missing (truncation?)")
    elif have != h:
        rep.add(kind, seq, f"entry {seq} hash differs from head/anchor")


def _verify_rows(rows, rep: VerifyReport, verifier) -> dict:
    info: dict[str, Any] = {}
    prev_hash = None
    expect_seq = None
    redacted: set[int] = set()
    accounted: set[int] = set()
    current_key = None  # key id registered by the latest genesis / audit.key entry
    sign_from = None  # first seq signed under the verifier's key
    unsigned_prefix = 0
    for seq, ts, actor, action, body, body_hash, prev, h, sig, red in rows:
        rep.entries += 1
        if expect_seq is None:
            if seq != 1 or action != "audit.genesis":
                rep.add("gap", seq, "log does not start with the genesis entry at seq 1")
            if prev != GENESIS_PREV:
                rep.add("prev_mismatch", seq, "genesis prev hash is not all zeros")
        else:
            if seq != expect_seq:
                rep.add("gap", seq, f"expected seq {expect_seq}, found {seq} (deleted/reordered)")
            if prev != prev_hash:
                rep.add("prev_mismatch", seq, "prev_hash does not match the previous entry")
        if entry_hash(seq, ts, actor, action, body_hash, prev) != h:
            rep.add("hash_mismatch", seq, "entry header was modified")
        if body is None:
            if red and action not in ("audit.genesis", "audit.key"):
                redacted.add(seq)
            else:
                rep.add("body_missing", seq, "body removed without redaction flag")
        else:
            if red:
                rep.add("body_mismatch", seq, "entry flagged redacted still has a body")
            if sha256_hex(body) != body_hash:
                rep.add("body_mismatch", seq, "body does not match its committed hash")
            elif action in TOMBSTONE_ACTIONS or action in ("audit.genesis", "audit.key"):
                try:
                    b = json.loads(body)
                except ValueError:
                    b = {}
                accounted.update(int(s) for s in b.get("redacted_seqs", []))
                if action == "audit.genesis" and seq == 1:
                    info["checkpoint_interval"] = b.get("checkpoint_interval")
                if action in ("audit.genesis", "audit.key"):
                    new_key = (b.get("signing") or {}).get("key_id")
                    if sign_from is not None and new_key != current_key:
                        rep.add(
                            "key_changed", seq, "signing key changed after the verified segment"
                        )
                    current_key = new_key
        if verifier is not None:
            if sign_from is None and current_key == verifier.key_id:
                sign_from = seq
            if sign_from is not None:
                if not verifier.verify(bytes.fromhex(h), sig):
                    rep.add("bad_signature", seq, "missing or invalid signature")
            else:
                unsigned_prefix += 1
        rep.head_seq, rep.head_hash = seq, h
        prev_hash, expect_seq = h, seq + 1
    rep.redacted_entries = len(redacted)
    for s in sorted(redacted - accounted):
        rep.add("unaccounted_redaction", s, "body redacted without an erasure tombstone")
    if rep.entries == 0:
        rep.add("empty", None, "audit log has no entries")
    if verifier is not None:
        rep.signatures_checked = sign_from is not None
        rep.sign_from = sign_from
        if sign_from is None and rep.entries:
            rep.add("wrong_key", None, f"no entry is registered under key {verifier.key_id}")
        elif unsigned_prefix:
            rep.warnings.append(
                f"{unsigned_prefix} entries before seq {sign_from} predate signing (not signed)"
            )
    elif current_key:
        rep.warnings.append(f"log is signed (key {current_key}) but no key was given")
    return info


def _verify_checkpoints(
    cps,
    hashes: dict[int, str],
    rep: VerifyReport,
    verifier,
    sign_from: int | None = None,
    interval: int | None = None,
    last_seq: int | None = None,
) -> None:
    prev_cp = GENESIS_PREV
    expect_start = 1
    ends: set[int] = set()
    for cp_id, start, end, root, prev, cph, ts, sig in cps:
        ends.add(end)
        rep.checkpoints += 1
        if start != expect_start:
            rep.add("checkpoint_chain", start, f"checkpoint {cp_id} starts at {start}")
        if prev != prev_cp:
            rep.add("checkpoint_chain", start, f"checkpoint {cp_id} prev link broken")
        if checkpoint_hash(cp_id, start, end, root, prev, ts) != cph:
            rep.add("checkpoint_mismatch", start, f"checkpoint {cp_id} record modified")
        hs = [hashes.get(s) for s in range(start, end + 1)]
        if any(x is None for x in hs):
            rep.add("checkpoint_mismatch", start, f"entries of checkpoint {cp_id} missing")
        elif merkle_root(hs) != root:
            rep.add("checkpoint_mismatch", start, f"Merkle root of checkpoint {cp_id} differs")
        signed = verifier is not None and sign_from is not None and start >= sign_from
        if signed and not verifier.verify(bytes.fromhex(cph), sig):
            rep.add("bad_signature", start, f"checkpoint {cp_id} signature invalid")
        prev_cp, expect_start = cph, end + 1
    if interval and last_seq:
        for m in range(int(interval), last_seq + 1, int(interval)):
            if m not in ends:
                rep.add("checkpoint_missing", m, f"no checkpoint ends at seq {m} (deleted?)")
                break


def verify_bundle(bundle: dict, verifier=None) -> VerifyReport:
    """Verify an evidence bundle offline (chain segment, bodies, checkpoints, proofs).

    Pass the verifier you trust (e.g. ``load_key("audit_public.pem")``).  A
    public key embedded in the bundle is only used if you pass ``verifier="embedded"``;
    a forger can embed their own key, so pin the expected ``key_id``.
    """
    rep = VerifyReport()
    if bundle.get("format") != BUNDLE_FORMAT:
        rep.add("format", None, f"unknown bundle format {bundle.get('format')!r}")
        return rep
    if verifier == "embedded":
        info = bundle.get("signing") or {}
        verifier = (
            Ed25519Verifier.from_pem(info["public_key_pem"]) if info.get("public_key_pem") else None
        )
    prev = bundle["prev_hash"]
    expect = bundle["range"][0]
    redacted, accounted = set(), set()
    hashes = {}
    signed_from = None
    for e in bundle["entries"]:
        rep.entries += 1
        if e["seq"] != expect:
            rep.add("gap", e["seq"], f"expected seq {expect}")
        if e["prev_hash"] != prev:
            rep.add("prev_mismatch", e["seq"], "prev_hash does not match the previous entry")
        if (
            entry_hash(e["seq"], e["ts"], e["actor"], e["action"], e["body_hash"], e["prev_hash"])
            != e["hash"]
        ):
            rep.add("hash_mismatch", e["seq"], "entry header was modified")
        if e["body"] is None:
            if e["redacted"]:
                redacted.add(e["seq"])
            else:
                rep.add("body_missing", e["seq"], "body removed without redaction flag")
        else:
            if sha256_hex(e["body"]) != e["body_hash"]:
                rep.add("body_mismatch", e["seq"], "body does not match its committed hash")
            if e["action"] in TOMBSTONE_ACTIONS:
                accounted.update(int(s) for s in json.loads(e["body"]).get("redacted_seqs", []))
        if verifier is not None:
            # Entries before the first signed one are covered by that entry's
            # prev-hash commitment; from it on, every entry must verify.
            if signed_from is None and e["sig"]:
                signed_from = e["seq"]
            if signed_from is not None and not verifier.verify(bytes.fromhex(e["hash"]), e["sig"]):
                rep.add("bad_signature", e["seq"], "missing or invalid signature")
        hashes[e["seq"]] = e["hash"]
        prev, expect = e["hash"], e["seq"] + 1
        rep.head_seq, rep.head_hash = e["seq"], e["hash"]
    rep.redacted_entries = len(redacted)
    if verifier is not None:
        rep.signatures_checked = signed_from is not None
        rep.sign_from = signed_from
        if signed_from is None and rep.entries:
            rep.add("wrong_key", None, "no entry in the bundle is signed")
        msg = _bundle_seal_msg(bundle["range"], bundle["prev_hash"], bundle["entries"])
        if not verifier.verify(msg, bundle.get("seal")):
            rep.add("bad_signature", None, "bundle seal (range/last hash) invalid or missing")
        if bundle["entries"] and bundle["entries"][-1]["seq"] != bundle["range"][1]:
            rep.add("gap", bundle["range"][1], "bundle ends before its declared range")
    for s in sorted(redacted - accounted):
        rep.warnings.append(f"entry {s} redacted; its tombstone is outside this bundle's range")
    for cp in bundle["checkpoints"]:
        if not (cp["start_seq"] >= bundle["range"][0] and cp["end_seq"] <= bundle["range"][1]):
            continue
        rep.checkpoints += 1
        if (
            checkpoint_hash(
                cp["id"], cp["start_seq"], cp["end_seq"], cp["root"], cp["prev_cp"], cp["ts"]
            )
            != cp["cp_hash"]
        ):
            rep.add(
                "checkpoint_mismatch", cp["start_seq"], f"checkpoint {cp['id']} record modified"
            )
        hs = [hashes[s] for s in range(cp["start_seq"], cp["end_seq"] + 1)]
        if merkle_root(hs) != cp["root"]:
            rep.add(
                "checkpoint_mismatch",
                cp["start_seq"],
                f"Merkle root of checkpoint {cp['id']} differs",
            )
        cp_signed = (
            verifier is not None and signed_from is not None and cp["start_seq"] >= signed_from
        )
        if cp_signed and not verifier.verify(bytes.fromhex(cp["cp_hash"]), cp["sig"]):
            rep.add("bad_signature", cp["start_seq"], f"checkpoint {cp['id']} signature invalid")
    cps = {cp["id"]: cp for cp in bundle["checkpoints"]}
    for s, pr in bundle.get("proofs", {}).items():
        cp = cps.get(pr["checkpoint_id"])
        e_hash = hashes.get(int(s))
        if cp is None or e_hash is None or not verify_merkle_proof(e_hash, pr["path"], cp["root"]):
            rep.add("proof_invalid", int(s), "Merkle inclusion proof does not verify")
    return rep
