"""Randomized (Monte Carlo) tamper trials for the OMPA audit log.

Each trial builds a fresh log with a random number of entries, random bodies,
a random checkpoint interval and (sometimes) legitimate redactions with
tombstones; applies ONE random tamper operation directly to the SQLite file
(or the head file); then re-opens the log read-only and verifies it.
A trial counts as *detected* if verify() reports ok=False or opening/
verifying raises.  Control trials (no tamper) measure false positives.

Attacker model per mode:
  unsigned  -- may edit the DB and head file but does not hold any key
               (full consistent re-hash of the chain is a known undetectable
               case without anchors/keys and is reported separately)
  hmac      -- log signed with an HMAC key the attacker does not have; verify
               with the key; includes signature-targeted ops and full re-hash

Usage:
    python benchmarks/audit_tamper_mc.py --trials 2000 --seed 20261021 --out mc.json
"""

from __future__ import annotations

import argparse
import contextlib
import json
import random
import shutil
import sqlite3
import string
import tempfile
import time
from collections import Counter
from pathlib import Path

from ompa.audit import (
    GENESIS_PREV,
    AuditLog,
    HMACSigner,
    canonical_json,
    checkpoint_hash,
    entry_hash,
    merkle_root,
    sha256_hex,
)

COLS = ["ts", "actor", "action", "body", "body_hash", "prev_hash", "hash"]
OPS_UNSIGNED = [
    "flip_byte",
    "delete_row",
    "delete_tail",
    "reorder",
    "insert_forged",
    "append_forged",
    "duplicate_row",
    "redact_no_tombstone",
    "unredact_flag",
    "truncate_file",
    "drop_table_rows",
    "checkpoint_flip",
    "checkpoint_delete",
    "head_tamper",
    "head_delete",
]
OPS_SIGNED = OPS_UNSIGNED + ["sig_flip", "sig_strip_all", "rehash_suffix", "rekey_suffix"]


def build_log(path: Path, rng: random.Random, signer=None) -> AuditLog:
    log = AuditLog(path, signer=signer, checkpoint_interval=rng.randint(3, 60))
    n = rng.randint(8, 160)
    for i in range(n):
        body = {
            "path": f"notes/{rng.randint(0, 50)}.md",
            "sha256": f"{rng.getrandbits(256):064x}",
            "text": "".join(rng.choices(string.ascii_letters + " éü✓", k=rng.randint(0, 40))),
            "i": i,
        }
        log.append(rng.choice(["note.write", "note.edit", "fact.add", "fact.merge"]), body)
    if rng.random() < 0.3:  # legitimate redaction + tombstone (must still verify)
        cand = [e.seq for e in log.entries() if e.seq > 1]
        seqs = sorted(rng.sample(cand, k=min(len(cand), rng.randint(1, 3))))
        with log.transaction():
            log.redact(seqs)
            log.append("forget", {"subject_commitment": "x" * 64, "redacted_seqs": seqs})
    return log


def _rows(c):
    return c.execute(
        "SELECT seq, ts, actor, action, body, body_hash, prev_hash, hash, sig, redacted "
        "FROM audit_entries ORDER BY seq"
    ).fetchall()


def _mutate_text(rng: random.Random, s: str) -> str:
    i = rng.randrange(len(s))
    alphabet = (
        "0123456789abcdef"
        if all(ch in "0123456789abcdef" for ch in s)
        else (string.ascii_letters + string.digits)
    )
    new = rng.choice([ch for ch in alphabet if ch != s[i]])
    return s[:i] + new + s[i + 1 :]


def _rehash_from(c, start: int, signer=None) -> None:
    """Attacker re-computes hashes/prev links from ``start`` (and every checkpoint),
    signing with ``signer`` if given (an attacker key), else leaving sigs as-is."""
    rows = _rows(c)
    prev = GENESIS_PREV
    for seq, ts, actor, action, body, bh, _ph, h, sig, _red in rows:
        if seq >= start:
            if body is not None:
                bh = sha256_hex(body)
            h = entry_hash(seq, ts, actor, action, bh, prev)
            if signer is not None:
                sig = signer.sign(bytes.fromhex(h))
            c.execute(
                "UPDATE audit_entries SET body_hash=?, prev_hash=?, hash=?, sig=? WHERE seq=?",
                (bh, prev, h, sig, seq),
            )
        prev = h
    hashes = {r[0]: r[7] for r in _rows(c)}
    prev_cp = GENESIS_PREV
    for cid, st, en, _root, _pc, _cph, ts, sig in c.execute(
        "SELECT * FROM audit_checkpoints ORDER BY id"
    ).fetchall():
        root = merkle_root([hashes[s] for s in range(st, en + 1) if s in hashes])
        cph = checkpoint_hash(cid, st, en, root, prev_cp, ts)
        if signer is not None:
            sig = signer.sign(bytes.fromhex(cph))
        c.execute(
            "UPDATE audit_checkpoints SET root=?, prev_cp=?, cp_hash=?, sig=? WHERE id=?",
            (root, prev_cp, cph, sig, cid),
        )
        prev_cp = cph
    return prev


def _write_head(path: Path, seq: int, h: str, signer=None) -> None:
    rec = {"seq": seq, "hash": h, "ts": "2026-01-01T00:00:00.000000Z"}
    if signer is not None:
        rec["key_id"] = signer.key_id
        rec["sig"] = signer.sign(canonical_json({"seq": seq, "hash": h}).encode())
    path.with_name(path.name + ".head.json").write_text(canonical_json(rec))


def apply_tamper(op: str, path: Path, rng: random.Random) -> str:
    """Apply ``op`` to the closed log at ``path``; returns a short description."""
    c = sqlite3.connect(str(path), isolation_level=None)
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    rows = _rows(c)
    seqs = [r[0] for r in rows]
    desc = op
    try:
        if op == "flip_byte":
            for _ in range(100):
                r = rng.choice(rows)
                col = rng.choice(COLS)
                val = r[1 + COLS.index(col)]
                if val:
                    break
            new = _mutate_text(rng, val)
            c.execute(f"UPDATE audit_entries SET {col}=? WHERE seq=?", (new, r[0]))  # nosec B608
            desc = f"flip_byte {col}@{r[0]}"
        elif op == "delete_row":
            s = rng.choice(seqs)
            c.execute("DELETE FROM audit_entries WHERE seq=?", (s,))
            desc = f"delete_row {s}"
        elif op == "delete_tail":
            k = rng.randint(1, min(5, len(seqs) - 1))
            c.execute("DELETE FROM audit_entries WHERE seq>?", (seqs[-1] - k,))
            desc = f"delete_tail {k}"
        elif op == "reorder":
            a, b = rng.sample(seqs, 2)
            c.execute("UPDATE audit_entries SET seq=-1 WHERE seq=?", (a,))
            c.execute("UPDATE audit_entries SET seq=? WHERE seq=?", (a, b))
            c.execute("UPDATE audit_entries SET seq=? WHERE seq=-1", (b,))
            desc = f"reorder {a}<->{b}"
        elif op in ("insert_forged", "append_forged"):
            k = rng.choice(seqs[1:]) if op == "insert_forged" else seqs[-1] + 1
            for s in sorted([x for x in seqs if x >= k], reverse=True):
                c.execute("UPDATE audit_entries SET seq=? WHERE seq=?", (s + 1, s))
            prev = next(r[7] for r in rows if r[0] == k - 1)
            body = canonical_json({"path": "forged.md", "_salt": "00" * 16})
            bh = sha256_hex(body)
            ts = "2026-01-01T00:00:00.000000Z"
            h = entry_hash(k, ts, "mallory", "note.write", bh, prev)
            c.execute(
                "INSERT INTO audit_entries VALUES (?,?,?,?,?,?,?,?,NULL,0)",
                (k, ts, "mallory", "note.write", body, bh, prev, h),
            )
            desc = f"{op} at {k}"
        elif op == "duplicate_row":
            a, b = rng.sample(seqs, 2)
            src = next(r for r in rows if r[0] == a)
            c.execute(
                "UPDATE audit_entries SET ts=?, actor=?, action=?, body=?, body_hash=?, "
                "prev_hash=?, hash=?, sig=?, redacted=? WHERE seq=?",
                (*src[1:], b),
            )
            desc = f"duplicate {a}->{b}"
        elif op == "redact_no_tombstone":
            cand = [r[0] for r in rows if r[4] is not None and r[3] not in ("audit.genesis",)]
            s = rng.choice(cand)
            c.execute("UPDATE audit_entries SET body=NULL, redacted=1 WHERE seq=?", (s,))
            desc = f"redact {s}"
        elif op == "unredact_flag":
            s = rng.choice(seqs)
            r = next(x for x in rows if x[0] == s)
            c.execute("UPDATE audit_entries SET redacted=? WHERE seq=?", (1 - r[9], s))
            desc = f"flag {s}"
        elif op == "truncate_file":
            c.close()
            size = path.stat().st_size
            cut = rng.randrange(0, size)
            with open(path, "r+b") as f:
                f.seek(cut)
                removed = f.read()
                f.truncate(cut)
            if not removed.strip(b"\x00"):
                # only zero bytes cut: SQLite reads them back as zeros, the
                # content is unchanged -> not a tamper, reported as no_effect
                return f"noop:truncate_file {cut}/{size}"
            return f"truncate_file {cut}/{size}"
        elif op == "drop_table_rows":
            c.execute("DELETE FROM audit_entries")
            desc = "drop all rows"
        elif op == "checkpoint_flip":
            cps = c.execute(
                "SELECT id, root, prev_cp, cp_hash, ts FROM audit_checkpoints"
            ).fetchall()
            if not cps:
                return apply_tamper("flip_byte", path, rng)
            cp = rng.choice(cps)
            col = rng.choice(["root", "prev_cp", "cp_hash", "ts"])
            val = cp[["id", "root", "prev_cp", "cp_hash", "ts"].index(col)]
            c.execute(
                f"UPDATE audit_checkpoints SET {col}=? WHERE id=?",  # nosec B608
                (_mutate_text(rng, val), cp[0]),
            )
            desc = f"checkpoint_flip {col}@{cp[0]}"
        elif op == "checkpoint_delete":
            ids = [r[0] for r in c.execute("SELECT id FROM audit_checkpoints")]
            if not ids:
                return apply_tamper("delete_row", path, rng)
            i = rng.choice(ids)
            c.execute("DELETE FROM audit_checkpoints WHERE id=?", (i,))
            desc = f"checkpoint_delete {i}"
        elif op == "head_tamper":
            hp = path.with_name(path.name + ".head.json")
            head = json.loads(hp.read_text())
            if rng.random() < 0.5:
                head["seq"] = max(1, head["seq"] - rng.randint(1, 3))
            else:
                head["hash"] = _mutate_text(rng, head["hash"])
            hp.write_text(json.dumps(head))
            desc = "head_tamper"
        elif op == "head_delete":
            path.with_name(path.name + ".head.json").unlink()
            desc = "head_delete"
        elif op == "sig_flip":
            r = rng.choice([x for x in rows if x[8]])
            c.execute("UPDATE audit_entries SET sig=? WHERE seq=?", (_mutate_text(rng, r[8]), r[0]))
            desc = f"sig_flip {r[0]}"
        elif op == "sig_strip_all":
            c.execute("UPDATE audit_entries SET sig=NULL")
            c.execute("UPDATE audit_checkpoints SET sig=NULL")
            desc = "sig_strip_all"
        elif op in ("rehash_suffix", "rekey_suffix"):
            # strongest no-key attack: edit an entry, then make every hash, link,
            # checkpoint and the head consistent again (rekey: re-sign with own key)
            s = rng.choice(seqs[1:])
            r = next(x for x in rows if x[0] == s)
            c.execute("UPDATE audit_entries SET actor=? WHERE seq=?", (r[2] + "x", s))
            fake = HMACSigner(b"attacker-key-" + b"0" * 32) if op == "rekey_suffix" else None
            last = _rehash_from(c, s, fake)
            c.close()
            _write_head(path, seqs[-1], last, fake)
            return f"{op} from {s}"
        else:
            raise ValueError(op)
    finally:
        with contextlib.suppress(sqlite3.Error):
            c.close()
    return desc


def verify_detects(path: Path, verifier=None, anchors=None) -> tuple[bool, str]:
    try:
        log = AuditLog(path, readonly=True)
        rep = log.verify(verifier=verifier, anchors=anchors)
        log.close()
    except Exception as e:  # unreadable/corrupt counts as detected
        return True, f"exception:{type(e).__name__}"
    return (not rep.ok), ",".join(sorted({i.kind for i in rep.issues}))


def run(trials: int, seed: int, mode: str = "unsigned", control_every: int = 5) -> dict:
    rng = random.Random(seed)
    key = HMACSigner(bytes(range(32))) if mode == "hmac" else None
    ops = OPS_SIGNED if mode == "hmac" else OPS_UNSIGNED
    per_op: dict[str, Counter] = {}
    misses = []
    controls = Counter()
    t0 = time.perf_counter()
    root = Path(tempfile.mkdtemp(prefix="ompa-mc-"))
    try:
        for t in range(trials):
            d = root / f"t{t}"
            d.mkdir()
            path = d / "audit.sqlite3"
            build_log(path, rng, key).close()
            if control_every and t % control_every == 0:
                det, why = verify_detects(path, key)
                controls["false_positive" if det else "clean_ok"] += 1
                if det:
                    misses.append({"trial": t, "op": "control", "why": why})
            op = rng.choice(ops)
            anchor = None
            if mode == "unsigned_rehash":
                hp = path.with_name(path.name + ".head.json")
                anchor = json.loads(hp.read_text())  # published before the attack
                op = "rehash_suffix_with_head"
                desc = apply_tamper("rehash_suffix", path, rng)
            else:
                desc = apply_tamper(op, path, rng)
            if desc.startswith("noop:"):
                per_op.setdefault(op, Counter())["no_effect"] += 1
                shutil.rmtree(d, ignore_errors=True)
                continue
            det, why = verify_detects(path, key)
            per_op.setdefault(op, Counter())["detected" if det else "missed"] += 1
            if anchor is not None:
                det_a, _ = verify_detects(path, key, anchors=[anchor])
                per_op.setdefault("with_external_anchor", Counter())[
                    "detected" if det_a else "missed"
                ] += 1
            if not det:
                misses.append({"trial": t, "op": desc})
            shutil.rmtree(d, ignore_errors=True)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    detected = sum(c["detected"] for k, c in per_op.items() if k != "with_external_anchor")
    effective = sum(
        c["detected"] + c["missed"] for k, c in per_op.items() if k != "with_external_anchor"
    )
    no_effect = sum(c["no_effect"] for c in per_op.values())
    return {
        "mode": mode,
        "seed": seed,
        "trials": trials,
        "effective_tampers": effective,
        "no_effect_tampers": no_effect,
        "detected": detected,
        "detection_rate": detected / effective if effective else None,
        "per_op": {k: dict(v) for k, v in sorted(per_op.items())},
        "controls": dict(controls),
        "misses": misses[:50],
        "seconds": round(time.perf_counter() - t0, 2),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--trials", type=int, default=1000)
    ap.add_argument("--seed", type=int, action="append")
    ap.add_argument(
        "--mode", choices=["unsigned", "hmac", "unsigned_rehash", "both"], default="both"
    )
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    seeds = a.seed or [20261021]
    modes = ["unsigned", "hmac"] if a.mode == "both" else [a.mode]
    res = [run(a.trials, s, m) for s in seeds for m in modes]
    for r in res:
        print(
            f"{r['mode']:8s} seed={r['seed']} N={r['trials']} effective={r['effective_tampers']} "
            f"no_effect={r['no_effect_tampers']} detected={r['detected']} "
            f"rate={r['detection_rate']:.4f} controls={r['controls']} ({r['seconds']}s)"
        )
        if "with_external_anchor" in r["per_op"]:
            print(f"   with external anchor: {r['per_op']['with_external_anchor']}")
    if a.out:
        a.out.write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
