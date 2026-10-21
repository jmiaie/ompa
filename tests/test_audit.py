"""Tests for audit memory: hash chain, tamper detection, signing, bundles,
provenance, as_of, erasure, retention, legal hold, ACL, MCP/CLI and concurrency."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import multiprocessing as mp
import random
import sqlite3
import sys
from pathlib import Path

import pytest

from ompa import AccessDeniedError, LegalHoldError, Ompa
from ompa.audit import (
    AuditError,
    AuditLog,
    HMACSigner,
    generate_key,
    load_key,
    merkle_proof,
    merkle_root,
    utc_now,
    verify_bundle,
    verify_merkle_proof,
)
from ompa.governance import AccessPolicy, subject_regex

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("mc", ROOT / "benchmarks" / "audit_tamper_mc.py")
mc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mc)

KEY = HMACSigner(bytes(range(32)))


class HashEmb:
    """Deterministic bag-of-words embedding (no model download)."""

    def encode(self, text):
        out = [0.0] * 64
        for w in text.lower().split():
            out[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1.0  # nosec B324
        return out


def make_log(tmp_path, n=30, signer=None, interval=8) -> AuditLog:
    log = AuditLog(tmp_path / "audit.sqlite3", signer=signer, checkpoint_interval=interval)
    for i in range(n):
        log.append("note.write", {"path": f"n{i}.md", "sha256": "ab" * 32})
    return log


def kinds(rep) -> set[str]:
    return {i.kind for i in rep.issues}


def tamper_sql(path: Path, *sql) -> None:
    c = sqlite3.connect(str(path))
    for q in sql:
        c.execute(q)
    c.commit()
    c.close()


def grep_vault(root: Path, needle: str) -> list[str]:
    """Case-insensitive byte grep over EVERY file under root (DBs, WAL, indexes)."""
    pat = needle.lower().encode()
    hits = []
    for p in root.rglob("*"):
        if p.is_file() and pat in p.read_bytes().lower():
            hits.append(str(p.relative_to(root)))
    return hits


# ---------------------------------------------------------------------------
# Chain + tamper detection
# ---------------------------------------------------------------------------


def test_clean_log_verifies(tmp_path):
    log = make_log(tmp_path)
    rep = log.verify()
    assert rep.ok, rep.issues
    assert rep.entries == 31 and rep.checkpoints == 3
    assert log.head()["seq"] == 31


@pytest.mark.parametrize("col", ["ts", "actor", "action", "body", "body_hash", "prev_hash", "hash"])
def test_flip_byte_in_any_column_detected(tmp_path, col):
    log = make_log(tmp_path)
    log.close()
    c = sqlite3.connect(str(tmp_path / "audit.sqlite3"))
    val = c.execute(f"SELECT {col} FROM audit_entries WHERE seq=12").fetchone()[0]  # nosec B608
    new = val[:-1] + ("0" if val[-1] != "0" else "1")
    c.execute(f"UPDATE audit_entries SET {col}=? WHERE seq=12", (new,))  # nosec B608
    c.commit()
    c.close()
    rep = AuditLog(tmp_path / "audit.sqlite3", readonly=True).verify()
    assert not rep.ok


def test_delete_row_detected(tmp_path):
    make_log(tmp_path).close()
    tamper_sql(tmp_path / "audit.sqlite3", "DELETE FROM audit_entries WHERE seq=7")
    rep = AuditLog(tmp_path / "audit.sqlite3", readonly=True).verify()
    assert {"gap", "prev_mismatch"} <= kinds(rep)


def test_reorder_detected(tmp_path):
    make_log(tmp_path).close()
    tamper_sql(
        tmp_path / "audit.sqlite3",
        "UPDATE audit_entries SET seq=-1 WHERE seq=5",
        "UPDATE audit_entries SET seq=5 WHERE seq=6",
        "UPDATE audit_entries SET seq=6 WHERE seq=-1",
    )
    assert not AuditLog(tmp_path / "audit.sqlite3", readonly=True).verify().ok


def test_insert_forged_entry_detected(tmp_path):
    make_log(tmp_path).close()
    rng = random.Random(3)
    mc.apply_tamper("insert_forged", tmp_path / "audit.sqlite3", rng)
    assert not AuditLog(tmp_path / "audit.sqlite3", readonly=True).verify().ok


def test_append_forged_entry_detected_by_head(tmp_path):
    make_log(tmp_path).close()
    mc.apply_tamper("append_forged", tmp_path / "audit.sqlite3", random.Random(1))
    rep = AuditLog(tmp_path / "audit.sqlite3", readonly=True).verify()
    assert "head_stale" in kinds(rep)


def test_truncate_tail_detected(tmp_path):
    make_log(tmp_path).close()
    tamper_sql(tmp_path / "audit.sqlite3", "DELETE FROM audit_entries WHERE seq > 27")
    rep = AuditLog(tmp_path / "audit.sqlite3", readonly=True).verify()
    assert "head_mismatch" in kinds(rep)


def test_truncate_db_file_detected(tmp_path):
    make_log(tmp_path, n=200).close()
    p = tmp_path / "audit.sqlite3"
    c = sqlite3.connect(str(p))
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.close()
    with open(p, "r+b") as f:
        f.truncate(p.stat().st_size // 2)
    det, _why = mc.verify_detects(p)
    assert det


@pytest.mark.parametrize("cut", [1, 26, 511, 4095])
def test_partial_page_truncation_detected(tmp_path, cut):
    # Found by the Monte Carlo (seed 1337): a 26-byte cut of a trailing page that
    # held no live rows used to verify clean.  SQLite never writes partial pages.
    make_log(tmp_path).close()
    p = tmp_path / "audit.sqlite3"
    with open(p, "r+b") as f:
        f.truncate(p.stat().st_size - cut)
    rep = AuditLog(p, readonly=True).verify()
    assert "db_size" in kinds(rep)


def test_padded_db_file_detected(tmp_path):
    make_log(tmp_path).close()
    p = tmp_path / "audit.sqlite3"
    with open(p, "ab") as f:
        f.write(b"\0" * 4096)
    assert "db_size" in kinds(AuditLog(p, readonly=True).verify())


def test_deleted_log_is_not_silently_recreated(tmp_path):
    make_log(tmp_path).close()
    for suffix in ("", "-wal", "-shm"):
        (tmp_path / f"audit.sqlite3{suffix}").unlink(missing_ok=True)
    with pytest.raises(AuditError, match="deleted or truncated"):
        AuditLog(tmp_path / "audit.sqlite3")


def test_redaction_requires_tombstone(tmp_path):
    log = make_log(tmp_path)
    log.redact([4])
    assert "unaccounted_redaction" in kinds(log.verify())
    log.append("forget", {"subject_commitment": "0" * 64, "redacted_seqs": [4]})
    rep = log.verify()
    assert rep.ok and rep.redacted_entries == 1


def test_checkpoint_tamper_and_deletion_detected(tmp_path):
    make_log(tmp_path).close()
    tamper_sql(tmp_path / "audit.sqlite3", "UPDATE audit_checkpoints SET root=hex(randomblob(32))")
    assert "checkpoint_mismatch" in kinds(AuditLog(tmp_path / "audit.sqlite3").verify())
    (tmp_path / "b").mkdir()
    make_log(tmp_path / "b").close()
    tamper_sql(tmp_path / "b" / "audit.sqlite3", "DELETE FROM audit_checkpoints WHERE id=3")
    assert "checkpoint_missing" in kinds(AuditLog(tmp_path / "b" / "audit.sqlite3").verify())


def test_head_file_tamper_and_deletion(tmp_path):
    log = make_log(tmp_path)
    log.head_path.unlink()
    assert "head_missing" in kinds(log.verify())


def test_full_rehash_needs_anchor_or_key(tmp_path):
    """Documented limit: an attacker who rewrites the DB *and* head without any
    key is undetectable from the files alone; an external anchor catches it."""
    log = make_log(tmp_path)
    anchor = log.head()
    log.close()
    mc.apply_tamper("rehash_suffix", tmp_path / "audit.sqlite3", random.Random(7))
    log = AuditLog(tmp_path / "audit.sqlite3", readonly=True)
    assert log.verify().ok  # the honest limitation
    assert "anchor_mismatch" in kinds(log.verify(anchors=[anchor]))


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


def test_hmac_signing_detects_rehash_and_stripping(tmp_path):
    log = make_log(tmp_path, signer=KEY)
    rep = log.verify()
    assert rep.ok and rep.signatures_checked
    log.close()
    mc.apply_tamper("rehash_suffix", tmp_path / "audit.sqlite3", random.Random(2))
    assert "bad_signature" in kinds(AuditLog(tmp_path / "audit.sqlite3").verify(verifier=KEY))
    (tmp_path / "b").mkdir()
    make_log(tmp_path / "b", signer=KEY).close()
    mc.apply_tamper("sig_strip_all", tmp_path / "b" / "audit.sqlite3", random.Random(2))
    assert not AuditLog(tmp_path / "b" / "audit.sqlite3", readonly=True).verify(KEY).ok


def test_wrong_key_and_rekey_detected(tmp_path):
    make_log(tmp_path, signer=KEY).close()
    other = HMACSigner(b"x" * 32)
    assert "wrong_key" in kinds(AuditLog(tmp_path / "audit.sqlite3", readonly=True).verify(other))
    mc.apply_tamper("rekey_suffix", tmp_path / "audit.sqlite3", random.Random(4))
    assert not AuditLog(tmp_path / "audit.sqlite3", readonly=True).verify(KEY).ok


def test_signing_enabled_later_registers_key(tmp_path):
    make_log(tmp_path, n=5).close()
    log = AuditLog(tmp_path / "audit.sqlite3", signer=KEY)
    log.append("note.write", {"path": "x.md"})
    rep = log.verify(KEY)
    assert rep.ok and rep.sign_from == 7 and rep.warnings  # unsigned prefix warned


def test_ed25519_signing(tmp_path):
    pytest.importorskip("cryptography")
    key = generate_key(tmp_path / "k.pem")
    signer = load_key(key)
    pub = load_key(str(key) + ".pub")
    assert oct(key.stat().st_mode & 0o777) == "0o600"
    log = make_log(tmp_path, signer=signer)
    assert log.verify(verifier=pub).ok
    b = log.export_bundle(proofs_for=[3, 17])
    assert verify_bundle(b, pub).ok
    b["entries"][5]["actor"] = "mallory"
    assert not verify_bundle(b, pub).ok


def test_generate_key_never_overwrites(tmp_path):
    k = generate_key(tmp_path / "h.key", alg="hmac")
    with pytest.raises(FileExistsError):
        generate_key(k, alg="hmac")


# ---------------------------------------------------------------------------
# Merkle + bundles
# ---------------------------------------------------------------------------


def test_merkle_proofs_all_sizes():
    rng = random.Random(11)
    for n in range(1, 41):
        hs = [f"{rng.getrandbits(256):064x}" for _ in range(n)]
        root = merkle_root(hs)
        for i in range(n):
            pr = merkle_proof(hs, i)
            assert verify_merkle_proof(hs[i], pr, root)
            bad = f"{rng.getrandbits(256):064x}"
            assert not verify_merkle_proof(bad, pr, root)


def test_bundle_export_verify_and_tamper(tmp_path):
    log = make_log(tmp_path, signer=KEY)
    b = json.loads(json.dumps(log.export_bundle(start=5, end=20, proofs_for=[9])))
    rep = verify_bundle(b, KEY)
    assert rep.ok, rep.issues
    trimmed = json.loads(json.dumps(b))
    trimmed["entries"] = trimmed["entries"][:-2]
    trimmed["range"][1] -= 2  # attacker hides the last two entries
    assert not verify_bundle(trimmed, KEY).ok
    b["entries"][3]["body"] = b["entries"][3]["body"].replace("n", "m", 1)
    assert "body_mismatch" in kinds(verify_bundle(b, KEY))


# ---------------------------------------------------------------------------
# Monte Carlo tamper trials (small in CI; see benchmarks for large N)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode,seed", [("unsigned", 20261021), ("hmac", 20261021)])
def test_monte_carlo_tamper_trials(mode, seed):
    res = mc.run(trials=120, seed=seed, mode=mode)
    assert res["detection_rate"] == 1.0, res["misses"]
    assert res["controls"].get("false_positive", 0) == 0


# ---------------------------------------------------------------------------
# Ompa integration: hooks, migration, provenance, as_of
# ---------------------------------------------------------------------------


def test_audit_off_by_default(tmp_path):
    ao = Ompa(vault_path=str(tmp_path), enable_semantic=False)
    ao.write("hello world note", file_path="brain/a.md")
    assert ao.gov.audit is None
    assert not (tmp_path / ".palace" / "audit.sqlite3").exists()


def test_enable_on_existing_vault_baseline_and_drift(tmp_path):
    ao = Ompa(vault_path=str(tmp_path), enable_semantic=False)
    ao.write("existing note links [[Other]]", file_path="brain/a.md")
    ao.kg_add("Kai", "works_on", "Orion")
    res = ao.enable_audit()
    assert res["created"] and res["baseline"]["notes"] >= 1 and res["baseline"]["facts"] >= 2
    ao2 = Ompa(vault_path=str(tmp_path), enable_semantic=False)  # auto-detects the log
    assert ao2.gov.audit is not None
    rep = ao2.audit_verify()
    assert rep["ok"] and not any(rep["shared"]["drift"].values())
    (tmp_path / "brain" / "a.md").write_text("edited behind OMPA's back")
    assert rep is not None
    assert ao2.audit_verify()["shared"]["drift"]["changed_outside_audit"] == ["brain/a.md"]
    with ao2.as_of(utc_now()) as view:
        assert any(f.object == "Orion" for f in view.facts("Kai"))


def test_mutations_are_logged(tmp_path):
    ao = Ompa(vault_path=str(tmp_path), enable_semantic=False, audit=True, principal="alice")
    ao.write("Kai leads [[Orion]]", file_path="work/orion.md")
    ao.write("Kai leads [[Orion]] and [[Atlas]]", file_path="work/orion.md")
    ao.kg_add("Kai", "works_on", "Orion", valid_from="2026-01-01")
    ao.kg_add("Kai", "works_on", "Orion", valid_from="2026-02-01")  # merge
    ao.kg.invalidate("Kai", "works_on", "Orion", ended="2026-03-01")
    ao.delete_note("work/orion.md")
    actions = [e.action for e in ao.gov.audit.entries()]
    for a in ("note.write", "note.edit", "fact.add", "fact.merge", "fact.invalidate"):
        assert a in actions
    assert actions[-1] == "note.delete" and "fact.delete" in actions
    assert {e.actor for e in ao.gov.audit.entries() if e.seq > 1} == {"alice"}
    assert ao.audit_verify()["ok"]


def test_search_citations(tmp_path):
    ao = Ompa(
        vault_path=str(tmp_path),
        enable_semantic=True,
        embedding_backend=HashEmb(),
        audit=True,
        principal="bot-7",
    )
    body = "intro line\n" + "filler words " * 300 + "\nthe zeppelin manifest is here\nend"
    ao.write(body, file_path="work/ship.md")
    for hybrid in (True, False):
        r = ao.search("zeppelin manifest", 3, hybrid=hybrid)[0]
        c = r.citation
        raw = Path(r.path).read_bytes()
        assert c["content_sha256"] == hashlib.sha256(raw).hexdigest()
        assert c["matches_audit"] and c["recorded_source"] == "audit"
        assert c["recorded_by"] == "bot-7" and c["valid_from"]
        span = raw[c["byte_start"] : c["byte_end"]].decode()
        lines = raw.decode().splitlines()[c["line_start"] - 1 : c["line_end"]]
        assert span.strip() and "\n".join(lines).strip()
    kw = Ompa(vault_path=str(tmp_path), enable_semantic=False).search("zeppelin", 1)[0]
    line = Path(kw.path).read_text().splitlines()[kw.citation["line_start"] - 1]
    assert "zeppelin" in line


def test_fact_provenance(tmp_path):
    ao = Ompa(vault_path=str(tmp_path), enable_semantic=False, audit=True, principal="alice")
    ao.write("first line\nsee [[Orion]] here", file_path="work/x.md", tags=["proj"])
    t = next(t for t in ao.kg_query("x") if t.object == "Orion")
    c = t.citation
    assert c["path"].endswith("work/x.md") and c["author"] == "alice"
    raw = Path(c["path"]).read_bytes()
    assert c["content_sha256"] == hashlib.sha256(raw).hexdigest()
    assert "[[Orion]]" in raw.decode().splitlines()[c["line"] - 1]
    assert c["recorded_at"]


def test_as_of_point_in_time(tmp_path):
    ao = Ompa(vault_path=str(tmp_path), enable_semantic=False, audit=True)
    ao.kg_add("Kai", "works_on", "Orion", valid_from="2026-01-01")
    t1 = utc_now()
    ao.kg.invalidate("Kai", "works_on", "Orion", ended="2026-01-31")
    ao.kg_add("Kai", "works_on", "Atlas", valid_from="2026-02-01")
    t2 = utc_now()
    with ao.as_of(t1) as v:
        assert [f.object for f in v.facts("Kai", valid_at="2026-06-01")] == ["Orion"]
    with ao.as_of(t2) as v:
        assert [f.object for f in v.facts("Kai", valid_at="2026-06-01")] == ["Atlas"]
        assert [f.object for f in v.facts("Kai", valid_at="2026-01-15")] == ["Orion"]
    with ao.as_of("2000-01-01") as v:
        assert v.facts("Kai", valid_at="2026-06-01") == []


# ---------------------------------------------------------------------------
# Right to erasure, retention, legal hold
# ---------------------------------------------------------------------------


def _seed_person(ao, name="Zorblatt Quince"):
    ao.write(f"Met {name} about Orion. [[{name}]] owns budget.", file_path="work/orion.md")
    ao.write(f"{name} is the CFO. Phone 555-0100.", file_path=f"people/{name}.md")
    ao.write("Notes on zorblatt-quince-project", file_path="work/zorblatt quince/plan.md")
    ao.write("Unrelated note about pasta.", file_path="brain/pasta.md")
    ao.pre_compact(f"User said {name} called about the budget.")  # session log JSON
    ao.kg_add(name, "works_at", "Acme")
    ao.search("Orion budget", 3)


@pytest.mark.parametrize("semantic", [False, True])
def test_forget_leaves_no_recoverable_content(tmp_path, semantic):
    v = tmp_path / "v"
    ao = Ompa(
        vault_path=str(v),
        enable_semantic=semantic,
        embedding_backend=HashEmb() if semantic else None,
        audit=True,
        principal="dpo",
    )
    _seed_person(ao)
    assert grep_vault(v, "zorblatt")  # present before
    [rc] = ao.forget("Zorblatt Quince")
    for needle in ("zorblatt", "quince"):
        assert grep_vault(v, needle) == [], needle  # every file: notes, DBs, WAL, indexes
    assert rc["delete"] == 1 and rc["redact"] >= 1 and rc["rename"] == 1 and rc["scrub"] >= 1
    rep = ao.audit_verify()["shared"]
    assert rep["ok"], rep["issues"]
    tomb = [e for e in ao.gov.audit.entries() if e.action == "forget"][-1]
    assert tomb.body["redacted_seqs"] and tomb.seq == rc["tombstone_seq"]
    digest = hashlib.sha256((rc["nonce"] + "\0" + "zorblatt quince").encode()).hexdigest()
    assert digest == tomb.body["subject_commitment"]  # proof of deletion without the data
    assert "pasta" in Path(v / "brain" / "pasta.md").read_text()  # unrelated data intact
    with ao.as_of(utc_now()) as view:
        assert view.facts("Zorblatt Quince") == []


def test_subject_regex_variants():
    rx = subject_regex(["Ada Lovelace", "Countess of Lovelace"])
    assert (
        rx.search("ada-lovelace")
        and rx.search("ADA_LOVELACE")
        and rx.search("the countess of lovelace")
    )
    assert not rx.search("adalovelace") and not rx.search("Ada Lovelacey")
    with pytest.raises(ValueError):
        subject_regex("a")


def test_legal_hold_blocks_delete_overwrite_forget(tmp_path):
    ao = Ompa(vault_path=str(tmp_path), enable_semantic=False, audit=True)
    ao.write("Acme litigation memo", file_path="work/memo.md")
    hold = ao.add_legal_hold("Acme", kind="subject", reason="case 42")
    with pytest.raises(LegalHoldError):
        ao.delete_note("work/memo.md")
    with pytest.raises(LegalHoldError):
        ao.write("overwrite", file_path="work/memo.md")
    with pytest.raises(LegalHoldError):
        ao.forget("Acme")
    assert (tmp_path / "work" / "memo.md").exists()
    acts = [e.action for e in ao.gov.audit.entries()]
    assert acts.count("hold.blocked") == 2 and "forget.blocked" in acts
    ao.remove_policy(hold["id"])
    assert ao.delete_note("work/memo.md")["success"]
    assert ao.audit_verify()["ok"]


def test_retention_rules(tmp_path):
    ao = Ompa(vault_path=str(tmp_path), enable_semantic=False, audit=True)
    ao.write("scratch thought about Zyx", file_path="brain/s1.md", tags=["scratch"])
    ao.write("keep me", file_path="brain/k.md", tags=["keep"])
    ao.write("held scratch", file_path="brain/s2.md", tags=["scratch", "legal"])
    for p in ("brain/s1.md", "brain/s2.md"):  # backdate frontmatter date
        f = tmp_path / p
        f.write_text(f.read_text().replace(f.read_text().split("date: ")[1][:10], "2020-01-01"))
    ao.add_retention_rule(30, tag="scratch")
    ao.add_legal_hold("legal", kind="tag")
    dry = ao.apply_retention(dry_run=True)[0]
    assert dry["notes"] == ["brain/s1.md"] and dry["held_skipped"] == ["brain/s2.md"]
    rc = ao.apply_retention()[0]
    assert rc["notes_deleted"] == 1 and not (tmp_path / "brain/s1.md").exists()
    assert (tmp_path / "brain/s2.md").exists() and (tmp_path / "brain/k.md").exists()
    assert ao.audit_verify()["ok"]
    assert grep_vault(tmp_path, "brain/s1.md") == []  # expired path gone from audit bodies too


def test_retention_by_predicate(tmp_path):
    ao = Ompa(vault_path=str(tmp_path), enable_semantic=False, audit=True)
    ao.kg_add("Kai", "mentioned", "Orion")
    ao.kg_add("Kai", "works_on", "Orion")
    ao.add_retention_rule(1, predicate="mentioned")
    from datetime import datetime, timedelta, timezone

    rc = ao.apply_retention(now=datetime.now(timezone.utc) + timedelta(days=3))[0]
    assert rc["facts_deleted"] == 1
    assert [t.predicate for t in ao.kg_query("Kai")] == ["works_on"]
    assert ao.audit_verify()["ok"]


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


def _acl_vault(tmp_path):
    owner = Ompa(vault_path=str(tmp_path), enable_semantic=False, principal="alice")
    owner.write("team roadmap for Orion", file_path="work/roadmap.md")
    owner.set_acl(
        AccessPolicy.template("alice", readers=["team:eng"], teams={"eng": ["bob", "carol"]})
    )
    return owner


def test_acl_denials_enforced_and_logged(tmp_path):
    owner = _acl_vault(tmp_path)
    bob = Ompa(vault_path=str(tmp_path), enable_semantic=False, principal="bob")
    assert bob.search("roadmap", 3)  # reader may read
    denied = [
        lambda: bob.write("x", file_path="work/x.md"),
        lambda: bob.kg_add("a", "b", "c"),
        lambda: bob.delete_note("work/roadmap.md"),
        lambda: bob.forget("Orion"),
        lambda: bob.add_legal_hold("x"),
        lambda: bob.set_acl({"admin": ["bob"]}),
        lambda: bob.update_brain("Key Decisions", "x"),
    ]
    for fn in denied:
        with pytest.raises(AccessDeniedError):
            fn()
    eve = Ompa(vault_path=str(tmp_path), enable_semantic=False, principal="eve")
    for fn in (lambda: eve.search("roadmap"), lambda: eve.kg_query("roadmap"), eve.audit_verify):
        with pytest.raises(AccessDeniedError):
            fn()
    log = [e for e in owner.gov.audit.entries() if e.action == "access.denied"]
    assert len(log) == len(denied) + 3
    assert {e.actor for e in log} == {"bob", "eve"}
    assert (tmp_path / "work" / "roadmap.md").exists() and not (tmp_path / "work/x.md").exists()
    assert owner.audit_verify()["ok"]


def test_denial_inside_transaction_is_not_rolled_back(tmp_path):
    _acl_vault(tmp_path)
    bob = Ompa(vault_path=str(tmp_path), enable_semantic=False, principal="bob")
    with pytest.raises(AccessDeniedError), bob.gov.txn():
        bob.kg_add("a", "b", "c")  # KG guard raises inside an open audit transaction
    assert any(e.action == "access.denied" for e in bob.gov.audit.entries())


def test_malformed_acl_fails_closed(tmp_path):
    (tmp_path / ".palace").mkdir(parents=True)
    (tmp_path / ".palace" / "acl.json").write_text('{"read": "everyone"}')
    with pytest.raises(ValueError):
        Ompa(vault_path=str(tmp_path), enable_semantic=False)


def test_private_and_shared_vaults(tmp_path):
    shared, personal = tmp_path / "shared", tmp_path / "personal"
    ao = Ompa(
        shared_vault_path=str(shared),
        personal_vault_path=str(personal),
        enable_semantic=False,
        principal="alice",
    )
    ao.write("private diary entry", file_path="brain/diary.md", vault="personal")
    ao.set_acl(AccessPolicy.template("alice"), vault="personal")
    ao.set_acl(AccessPolicy.template("alice", readers=["*"], writers=["*"]), vault="shared")
    bob = Ompa(
        shared_vault_path=str(shared),
        personal_vault_path=str(personal),
        enable_semantic=False,
        principal="bob",
    )
    bob.write("shared note", file_path="work/s.md", vault="shared")
    with pytest.raises(AccessDeniedError):
        bob.search("diary", vaults=["personal"])
    with pytest.raises(AccessDeniedError):
        bob.write("sneaky", file_path="brain/x.md", vault="personal")
    assert any(e.action == "access.denied" for e in ao.personal_gov.audit.entries())


def test_mcp_principal_acl_and_tools(tmp_path, monkeypatch):
    from ompa import mcp_server

    _acl_vault(tmp_path)
    monkeypatch.delenv("OMPA_PRINCIPAL", raising=False)
    vp = str(tmp_path)
    r = mcp_server.handle_call_tool(
        "ao_kg_add",
        {"vault_path": vp, "principal": "bob", "subject": "a", "predicate": "b", "object": "c"},
    )
    assert r["error"] == "AccessDeniedError"
    r = mcp_server.handle_call_tool(
        "ao_search", {"vault_path": vp, "principal": "eve", "query": "x"}
    )
    assert r["error"] == "AccessDeniedError"
    r = mcp_server.handle_call_tool(
        "ao_search", {"vault_path": vp, "principal": "bob", "query": "roadmap"}
    )
    assert r["results"] and r["results"][0]["citation"]["content_sha256"]
    r = mcp_server.handle_call_tool(
        "ao_forget", {"vault_path": vp, "principal": "alice", "subject": "roadmap"}
    )
    assert r["receipts"][0]["dry_run"]  # dry run by default
    r = mcp_server.handle_call_tool("ao_audit_verify", {"vault_path": vp, "principal": "alice"})
    assert r["ok"]
    monkeypatch.setenv("OMPA_PRINCIPAL", "bob")
    r = mcp_server.handle_call_tool(
        "ao_search", {"vault_path": vp, "principal": "alice", "query": "x"}
    )
    assert r["error"] == "AccessDeniedError"  # cannot override a pinned principal
    tools = {t["name"]: t for t in mcp_server.handle_list_tools()["tools"]}
    assert {"ao_audit_verify", "ao_forget", "ao_as_of", "ao_audit_head", "ao_audit_export"} <= set(
        tools
    )
    assert "principal" in tools["ao_search"]["inputSchema"]["properties"]


def test_cli_audit_verify_exit_codes(tmp_path):
    from typer.testing import CliRunner

    from ompa.cli import app

    ao = Ompa(vault_path=str(tmp_path), enable_semantic=False, audit=True)
    ao.write("hello", file_path="brain/h.md")
    runner = CliRunner()
    res = runner.invoke(app, ["audit", "verify", "--vault-path", str(tmp_path)])
    assert res.exit_code == 0, res.output
    tamper_sql(
        tmp_path / ".palace" / "audit.sqlite3", "UPDATE audit_entries SET actor='m' WHERE seq=2"
    )
    res = runner.invoke(app, ["audit", "verify", "--vault-path", str(tmp_path)])
    assert res.exit_code == 1 and "hash_mismatch" in res.output
    out = tmp_path / "bundle.json"
    assert (
        runner.invoke(app, ["audit", "export", str(out), "--vault-path", str(tmp_path)]).exit_code
        == 0
    )
    assert runner.invoke(app, ["audit", "verify-bundle", str(out)]).exit_code == 1  # tampered seq 2


# ---------------------------------------------------------------------------
# Concurrency: no lost entries, chain stays valid
# ---------------------------------------------------------------------------


def _append_worker(db, n, tag):
    log = AuditLog(db)
    for i in range(n):
        log.append("note.write", {"w": tag, "i": i})
    log.close()


def _ompa_worker(vault, n, tag):
    ao = Ompa(vault_path=vault, enable_semantic=False, principal=f"w{tag}")
    for i in range(n):
        ao.write(f"note {tag}-{i} [[Hub]]", file_path=f"work/w{tag}-{i}.md")


def test_concurrent_processes_lose_nothing(tmp_path):
    db = tmp_path / "audit.sqlite3"
    AuditLog(db).close()
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_append_worker, args=(db, 60, t)) for t in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
        assert p.exitcode == 0
    log = AuditLog(db, readonly=True)
    rep = log.verify()
    assert rep.ok, rep.issues
    assert rep.entries == 1 + 4 * 60


def test_concurrent_ompa_writers_audited(tmp_path):
    Ompa(vault_path=str(tmp_path), enable_semantic=False, audit=True)
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_ompa_worker, args=(str(tmp_path), 15, t)) for t in range(3)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(180)
        assert p.exitcode == 0
    ao = Ompa(vault_path=str(tmp_path), enable_semantic=False)
    writes = [e for e in ao.gov.audit.entries() if e.action == "note.write"]
    assert len(writes) == 45 and len(list((tmp_path / "work").glob("w*.md"))) == 45
    rep = ao.audit_verify()
    assert rep["ok"] and not any(rep["shared"]["drift"].values())


def test_threads_share_one_log(tmp_path):
    import threading

    log = AuditLog(tmp_path / "audit.sqlite3")

    def worker(t):
        for i in range(50):
            log.append("fact.add", {"t": t, "i": i})

    ts = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    rep = log.verify()
    assert rep.ok and rep.entries == 201


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
