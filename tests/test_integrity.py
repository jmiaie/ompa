"""Regression tests: the graph follows the vault, retention ignores note-supplied
dates, and erasure never merges two notes into one."""

import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from ompa import Ompa
from ompa.knowledge_graph import KnowledgeGraph

YESTERDAY = (date.today() - timedelta(days=1)).isoformat()


def _current(ao, entity, *predicates):
    return sorted(
        (t.predicate, t.object) for t in ao.kg.query_entity(entity) if t.predicate in predicates
    )


# ---------------------------------------------------------------------------
# Knowledge graph: facts a note no longer states stop being current
# ---------------------------------------------------------------------------


def test_removed_link_and_tag_are_no_longer_current(tmp_path):
    ao = Ompa(vault_path=tmp_path, enable_semantic=False)
    ao.write("Owner is [[Alice]].", file_path="work/active/billing.md", tags=["q3"])
    assert _current(ao, "billing", "links_to", "has_tag") == [
        ("has_tag", "q3"),
        ("links_to", "Alice"),
    ]

    ao.write("Owner is [[Bob]].", file_path="work/active/billing.md", tags=["q4"])
    assert _current(ao, "billing", "links_to", "has_tag") == [
        ("has_tag", "q4"),
        ("links_to", "Bob"),
    ]


def test_retraction_keeps_history(tmp_path):
    ao = Ompa(vault_path=tmp_path, enable_semantic=False)
    note = tmp_path / "work" / "active" / "billing.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("Owner is [[Alice]].\n", encoding="utf-8")
    # the link was first recorded in January
    ao.kg.add_triple("billing", "links_to", "Alice", valid_from="2026-01-10", source=str(note))

    note.write_text("Owner is [[Bob]].\n", encoding="utf-8")
    ao.post_tool("edit", {"file_path": str(note)})

    alice = [t for t in ao.kg.query_relation("billing", "links_to") if t.object == "Alice"]
    assert [(t.valid_from, t.valid_to) for t in alice] == [("2026-01-10", YESTERDAY)]
    assert ("links_to", "Alice") in [
        (t.predicate, t.object) for t in ao.kg.query_entity("billing", as_of="2026-06-01")
    ]
    assert _current(ao, "billing", "links_to") == [("links_to", "Bob")]


def test_restating_a_withdrawn_fact_makes_it_current_again(tmp_path):
    ao = Ompa(vault_path=tmp_path, enable_semantic=False)
    ao.write("See [[Alice]].", file_path="work/active/n.md")
    ao.write("Nothing here.", file_path="work/active/n.md")
    assert _current(ao, "n", "links_to") == []
    ao.write("See [[Alice]] again.", file_path="work/active/n.md")
    assert _current(ao, "n", "links_to") == [("links_to", "Alice")]


def test_facts_added_by_an_agent_are_not_retracted(tmp_path):
    ao = Ompa(vault_path=tmp_path, enable_semantic=False)
    ao.write("Owner is [[Alice]].", file_path="work/active/billing.md")
    path = str(tmp_path / "work" / "active" / "billing.md")
    ao.kg_add("billing", "owned_by", "Finance", source=path)  # not a note-derived predicate
    ao.kg_add("billing", "links_to", "Ledger")  # no source file

    ao.write("No links now.", file_path="work/active/billing.md")
    assert _current(ao, "billing", "links_to", "owned_by") == [
        ("links_to", "Ledger"),
        ("owned_by", "Finance"),
    ]


def test_same_named_note_elsewhere_keeps_its_facts(tmp_path):
    ao = Ompa(vault_path=tmp_path, enable_semantic=False)
    ao.write("About [[Orion]].", file_path="work/active/plan.md", tags=["a"])
    ao.write("About [[Atlas]].", file_path="org/teams/plan.md", tags=["b"])
    # both notes are the entity "plan"; rewriting one must not close the other's facts
    ao.write("About nothing.", file_path="work/active/plan.md", tags=["a"])
    assert ("links_to", "Atlas") in _current(ao, "plan", "links_to")
    assert ("links_to", "Orion") not in _current(ao, "plan", "links_to")


def test_moved_note_drops_its_old_folder(tmp_path):
    ao = Ompa(vault_path=tmp_path, enable_semantic=False)
    ao.write("Draft.", file_path="work/active/spec.md")
    assert ("in_subfolder", "work/active") in _current(ao, "spec", "in_subfolder")

    src = tmp_path / "work" / "active" / "spec.md"
    dst = tmp_path / "work" / "archive" / "spec.md"
    src.rename(dst)
    ao.post_tool("write", {"file_path": str(dst)})
    assert _current(ao, "spec", "in_subfolder") == [("in_subfolder", "work/archive")]


def test_repopulating_an_unchanged_note_changes_nothing(tmp_path):
    kg = KnowledgeGraph(db_path=str(tmp_path / "kg.sqlite3"))
    note = tmp_path / "work" / "orion.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "---\ntags: [decision]\ndate: 2026-02-03\n---\nSee [[Atlas]].\n", encoding="utf-8"
    )
    kg.populate_from_note(note, tmp_path)
    before = sorted((t.predicate, t.object, t.valid_to) for t in kg.query_entity("orion"))
    kg.populate_from_note(note, tmp_path)
    assert sorted((t.predicate, t.object, t.valid_to) for t in kg.query_entity("orion")) == before
    assert all(valid_to is None for _, _, valid_to in before)


def test_retraction_is_audited_and_replayable(tmp_path):
    ao = Ompa(vault_path=tmp_path, enable_semantic=False, audit=True)
    ao.write("Owner is [[Alice]].", file_path="work/active/billing.md")
    ao.write("Owner is [[Bob]].", file_path="work/active/billing.md")

    withdrawn = [
        e.body
        for e in ao.gov.audit.entries()
        if e.action == "fact.invalidate" and e.body and e.body.get("o") == "Alice"
    ]
    assert len(withdrawn) == 1 and withdrawn[0]["ended"] == YESTERDAY
    assert ao.audit_verify()["ok"]
    with ao.as_of(datetime.now(timezone.utc)) as view:
        assert sorted(f.object for f in view.facts("billing") if f.predicate == "links_to") == [
            "Bob"
        ]


def test_read_only_principal_cannot_retract(tmp_path):
    owner = Ompa(vault_path=tmp_path, enable_semantic=False, principal="alice")
    owner.write("Owner is [[Alice]].", file_path="work/active/billing.md")
    owner.set_acl(
        {
            "version": 1,
            "owner": "alice",
            "teams": {},
            "read": ["bob"],
            "write": [],
            "admin": ["alice"],
        }
    )
    note = tmp_path / "work" / "active" / "billing.md"
    note.write_text("No links.\n", encoding="utf-8")

    reader = Ompa(vault_path=tmp_path, enable_semantic=False, principal="bob")
    with pytest.raises(PermissionError):
        reader.kg.populate_from_note(note, tmp_path)
    assert ("links_to", "Alice") in _current(owner, "billing", "links_to")


# ---------------------------------------------------------------------------
# Retention: a note's own date does not decide when it expires
# ---------------------------------------------------------------------------


def test_future_dated_note_still_expires(tmp_path):
    ao = Ompa(vault_path=tmp_path, enable_semantic=False, audit=True)
    ao.add_retention_rule(30, all=True)
    ao.write("kept for ever?", file_path="work/active/evade.md", date="2099-01-01")
    later = datetime.now(timezone.utc) + timedelta(days=45)
    assert ao.apply_retention(dry_run=True, now=later)[0]["notes"] == ["work/active/evade.md"]


def test_back_dated_note_is_not_deleted_early(tmp_path):
    ao = Ompa(vault_path=tmp_path, enable_semantic=False, audit=True)
    ao.add_retention_rule(30, all=True)
    # date= records when the conversation happened, not when the note was stored
    ao.write("what we agreed in 2021", file_path="work/active/early.md", date="2021-05-01")
    assert ao.apply_retention(dry_run=True)[0]["notes"] == []
    assert ao.apply_retention()[0]["notes_deleted"] == 0
    assert (tmp_path / "work" / "active" / "early.md").exists()


def test_retention_without_audit_uses_file_age(tmp_path):
    ao = Ompa(vault_path=tmp_path, enable_semantic=False, audit=False)
    ao.add_retention_rule(30, all=True)
    ao.write("old", file_path="work/active/old.md", date="2099-01-01")
    ao.write("new", file_path="work/active/new.md", date="2001-01-01")
    old = tmp_path / "work" / "active" / "old.md"
    stamp = time.time() - 90 * 86400
    os.utime(old, (stamp, stamp))
    assert ao.apply_retention(dry_run=True)[0]["notes"] == ["work/active/old.md"]


def test_touching_an_audited_note_does_not_restart_its_clock(tmp_path):
    ao = Ompa(vault_path=tmp_path, enable_semantic=False, audit=True)
    ao.add_retention_rule(30, all=True)
    ao.write("stale", file_path="work/active/stale.md")
    later = datetime.now(timezone.utc) + timedelta(days=45)
    future = time.time() + 44 * 86400  # someone re-touches the file just before the run
    os.utime(tmp_path / "work" / "active" / "stale.md", (future, future))
    assert ao.apply_retention(dry_run=True, now=later)[0]["notes"] == ["work/active/stale.md"]


def test_notes_older_than_the_audit_log_keep_their_age(tmp_path):
    # a vault that enables auditing later: the baseline entry is new, the file is old
    plain = Ompa(vault_path=tmp_path, enable_semantic=False, audit=False)
    plain.write("from last year", file_path="work/active/legacy.md")
    legacy = tmp_path / "work" / "active" / "legacy.md"
    stamp = time.time() - 400 * 86400
    os.utime(legacy, (stamp, stamp))

    ao = Ompa(vault_path=tmp_path, enable_semantic=False, audit=True)
    ao.add_retention_rule(365, all=True)
    assert ao.apply_retention(dry_run=True)[0]["notes"] == ["work/active/legacy.md"]


# ---------------------------------------------------------------------------
# forget(): notes that redact to the same name are both kept
# ---------------------------------------------------------------------------


def test_forget_does_not_merge_notes_that_redact_to_one_name(tmp_path):
    ao = Ompa(vault_path=tmp_path, enable_semantic=False, audit=True)
    ao.write("Budget owner sign-off, 2026 plan.", file_path="work/active/ada-lovelace review.md")
    ao.write(
        "Performance concerns raised in March.", file_path="work/active/Ada Lovelace review.md"
    )

    receipt = ao.forget("Ada Lovelace")[0]
    assert receipt["rename"] == 2

    survivors = sorted((tmp_path / "work" / "active").glob("*.md"))
    assert len(survivors) == 2
    text = "\n".join(p.read_text(encoding="utf-8") for p in survivors)
    assert "Budget owner sign-off" in text and "Performance concerns" in text
    assert all("lovelace" not in p.name.lower() for p in survivors)
    assert ao.audit_verify()["ok"]


def test_forget_rename_does_not_replace_an_existing_note(tmp_path):
    ao = Ompa(vault_path=tmp_path, enable_semantic=False)
    already = tmp_path / "work" / "active" / "[REDACTED] notes.md"
    ao.write(
        "Unrelated note that happens to have this name.",
        file_path=str(already.relative_to(tmp_path)),
    )
    ao.write("Meeting about the roadmap.", file_path="work/active/Ada Lovelace notes.md")
    target = Path(ao.forget("Ada Lovelace", dry_run=True)[0] and already)

    ao.forget("Ada Lovelace")
    assert "Unrelated note" in target.read_text(encoding="utf-8")
    assert len(list((tmp_path / "work" / "active").glob("*.md"))) == 2
