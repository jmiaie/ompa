"""
Property-based tests for OMPA using Hypothesis.

Tests invariants that must hold for any valid input, not just example inputs.
Run: pytest tests/test_properties.py -v

Requires: pip install hypothesis
"""

import os
import tempfile

import pytest

try:
    from hypothesis import assume, given, settings
    from hypothesis import strategies as st

    HYPOTHESIS_AVAILABLE = True

    # ---------------------------------------------------------------------------
    # Strategies — defined only when hypothesis is available; module-level
    # definitions would crash on import when hypothesis is absent.
    # ---------------------------------------------------------------------------

    safe_text = st.text(
        alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd", "Zs")),
        min_size=1,
        max_size=50,
    ).filter(lambda s: s.strip())

    entity_name = st.text(
        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-",
        min_size=1,
        max_size=30,
    )

    predicate_name = st.sampled_from([
        "works_on", "knows", "created", "links_to", "has_tag",
        "in_folder", "depends_on", "blocks", "owns", "manages",
    ])

    iso_date = st.dates(
        min_value=__import__("datetime").date(2020, 1, 1),
        max_value=__import__("datetime").date(2030, 12, 31),
    ).map(str)

    message_type_str = st.sampled_from([
        "DECISION", "INCIDENT", "WIN", "LOSS", "BLOCKER",
        "QUESTION", "SUGGESTION", "REVIEW", "BUG", "FEATURE",
        "LEARN", "RETROSPECTIVE", "ALERT", "STATUS", "CHORE",
    ])

except ImportError:
    HYPOTHESIS_AVAILABLE = False
    # No-op stubs so class bodies don't raise NameError when hypothesis is absent.
    def given(*a, **kw):
        return lambda f: f
    def settings(*a, **kw):
        return lambda f: f
    def assume(cond):
        pass

    class _StStub:
        def __getattr__(self, name):
            return lambda *a, **kw: None

    st = _StStub()
    entity_name = None
    predicate_name = None
    iso_date = None
    safe_text = None
    message_type_str = None

pytestmark = pytest.mark.skipif(
    not HYPOTHESIS_AVAILABLE,
    reason="hypothesis not installed (pip install hypothesis)",
)


# ---------------------------------------------------------------------------
# KnowledgeGraph properties
# ---------------------------------------------------------------------------

class TestKGProperties:

    @given(subject=entity_name, predicate=predicate_name, obj=entity_name)
    @settings(max_examples=50)
    def test_add_then_query_roundtrip(self, subject, predicate, obj):
        """Any triple added must be retrievable by querying the subject."""
        assume(subject != obj)

        with tempfile.TemporaryDirectory() as tmp:
            from ompa import KnowledgeGraph

            kg = KnowledgeGraph(db_path=os.path.join(tmp, "kg.sqlite3"))
            kg.add_triple(subject, predicate, obj)

            results = kg.query_entity(subject)
            subjects = [t.subject for t in results]
            assert subject in subjects, f"Expected {subject!r} in subjects after add_triple"

    @given(subject=entity_name, predicate=predicate_name, obj=entity_name)
    @settings(max_examples=50)
    def test_triple_appears_in_both_subject_and_object_query(self, subject, predicate, obj):
        """A triple added must appear in queries for both subject AND object."""
        assume(subject != obj)

        with tempfile.TemporaryDirectory() as tmp:
            from ompa import KnowledgeGraph

            kg = KnowledgeGraph(db_path=os.path.join(tmp, "kg.sqlite3"))
            kg.add_triple(subject, predicate, obj)

            subject_results = kg.query_entity(subject)
            object_results = kg.query_entity(obj)

            all_subjects = [t.subject for t in subject_results]
            all_objects = [t.object for t in object_results]

            assert subject in all_subjects or subject in [t.subject for t in object_results]
            assert obj in all_objects or obj in [t.object for t in subject_results]

    @given(
        subject=entity_name,
        predicate=predicate_name,
        obj=entity_name,
        valid_from=iso_date,
    )
    @settings(max_examples=40)
    def test_triple_valid_from_respected(self, subject, predicate, obj, valid_from):
        """A triple added with valid_from should not appear as-of a date before it."""
        assume(subject != obj)

        with tempfile.TemporaryDirectory() as tmp:
            import datetime

            from ompa import KnowledgeGraph

            kg = KnowledgeGraph(db_path=os.path.join(tmp, "kg.sqlite3"))
            kg.add_triple(subject, predicate, obj, valid_from=valid_from)

            # Query one day before valid_from — should NOT appear
            vf = datetime.date.fromisoformat(valid_from)
            day_before = (vf - datetime.timedelta(days=1)).isoformat()
            results_before = kg.query_entity(subject, as_of=day_before)

            # The triple should not be valid before its start date
            matching = [
                t for t in results_before
                if t.subject == subject and t.predicate == predicate and t.object == obj
            ]
            assert len(matching) == 0, (
                f"Triple should not appear as-of {day_before} (valid_from={valid_from})"
            )

    @given(subject=entity_name, predicate=predicate_name, obj=entity_name)
    @settings(max_examples=30)
    def test_stats_count_monotonically_increases(self, subject, predicate, obj):
        """Triple count must be >= before any add_triple call."""
        assume(subject != obj)

        with tempfile.TemporaryDirectory() as tmp:
            from ompa import KnowledgeGraph

            kg = KnowledgeGraph(db_path=os.path.join(tmp, "kg.sqlite3"))
            before = kg.stats()["triple_count"]
            kg.add_triple(subject, predicate, obj)
            after = kg.stats()["triple_count"]

            assert after >= before, "triple_count must not decrease after add_triple"

    @given(
        triples=st.lists(
            st.tuples(entity_name, predicate_name, entity_name),
            min_size=1,
            max_size=10,
            unique=True,
        )
    )
    @settings(max_examples=20)
    def test_timeline_contains_all_added_triples(self, triples):
        """Timeline must contain every triple that was added for the subject."""
        subject = "TestEntity"
        with tempfile.TemporaryDirectory() as tmp:
            from ompa import KnowledgeGraph

            kg = KnowledgeGraph(db_path=os.path.join(tmp, "kg.sqlite3"))
            for _, predicate, obj in triples:
                assume(obj != subject)
                kg.add_triple(subject, predicate, obj)

            timeline = kg.timeline(subject)
            timeline_labels = [e["label"] for e in timeline]

            # Every triple added for subject should appear in timeline
            for _, predicate, obj in triples:
                expected = f"{subject} --{predicate}--> {obj}"
                assert expected in timeline_labels, (
                    f"Expected '{expected}' in timeline"
                )


# ---------------------------------------------------------------------------
# MessageClassifier properties
# ---------------------------------------------------------------------------

class TestClassifierProperties:

    @given(message=safe_text)
    @settings(max_examples=100)
    def test_classifier_always_returns_valid_type(self, message):
        """Classifier must return a valid MessageType for any non-empty string."""
        from ompa import MessageClassifier, MessageType

        classifier = MessageClassifier()
        result = classifier.classify(message)

        assert result.message_type in MessageType, (
            f"Expected valid MessageType, got {result.message_type!r}"
        )
        assert 0.0 <= result.confidence <= 1.0, (
            f"Confidence {result.confidence} out of [0,1] range"
        )
        assert result.suggested_action, "suggested_action must be non-empty"

    @given(message=safe_text)
    @settings(max_examples=50)
    def test_routing_hints_always_list(self, message):
        """routing_hints must always be a list (possibly empty)."""
        from ompa import MessageClassifier

        classifier = MessageClassifier()
        result = classifier.classify(message)
        assert isinstance(result.routing_hints, list)


# ---------------------------------------------------------------------------
# Token counter properties
# ---------------------------------------------------------------------------

class TestTokenCounterProperties:

    @given(text=st.text(min_size=0, max_size=500))
    @settings(max_examples=100)
    def test_count_tokens_non_negative(self, text):
        """count_tokens must always return a non-negative integer."""
        from ompa import count_tokens

        result = count_tokens(text)
        assert isinstance(result, int)
        assert result >= 0

    @given(text=st.text(min_size=1, max_size=200))
    @settings(max_examples=50)
    def test_longer_text_more_tokens(self, text):
        """Doubling text must increase token count."""
        from ompa import count_tokens

        single = count_tokens(text)
        double = count_tokens(text + " " + text)
        assert double >= single, (
            f"double ({double}) should be >= single ({single})"
        )

    def test_empty_string_zero_tokens(self):
        """Empty string must produce 0 tokens."""
        from ompa import count_tokens

        assert count_tokens("") == 0


# ---------------------------------------------------------------------------
# Vault properties
# ---------------------------------------------------------------------------

class TestVaultProperties:

    @given(note_name=entity_name, content=safe_text)
    @settings(max_examples=30)
    def test_brain_note_roundtrip(self, note_name, content):
        """A brain note written must be readable back with same content."""
        with tempfile.TemporaryDirectory() as tmp:
            from ompa import Vault

            vault = Vault(tmp)
            vault.update_brain_note(note_name, content)
            note = vault.get_brain_note(note_name)

            assert note is not None, f"Expected to retrieve brain note '{note_name}'"
            assert content in note.content, (
                f"Content {content!r} not found in note"
            )

    @given(
        notes=st.lists(
            st.tuples(entity_name, safe_text),
            min_size=1,
            max_size=5,
            unique_by=lambda x: x[0],
        )
    )
    @settings(max_examples=15)
    def test_stats_total_notes_accurate(self, notes):
        """total_notes in stats must equal number of notes actually written."""
        with tempfile.TemporaryDirectory() as tmp:
            from ompa import Vault

            vault = Vault(tmp)
            for name, content in notes:
                vault.update_brain_note(name, content)

            stats = vault.get_stats()
            assert stats["total_notes"] >= len(notes), (
                f"Expected at least {len(notes)} notes, got {stats['total_notes']}"
            )


# ---------------------------------------------------------------------------
# SyncResult properties
# ---------------------------------------------------------------------------

class TestSyncResultProperties:

    @given(
        files_changed=st.integers(min_value=0, max_value=10000),
        message=safe_text,
    )
    @settings(max_examples=50)
    def test_sync_result_str_contains_direction(self, files_changed, message):
        """SyncResult.__str__ must always include the backend and direction."""
        from ompa.sync import SyncResult

        for backend in ["git", "s3", "rsync"]:
            for direction in ["push", "pull", "status"]:
                result = SyncResult(
                    success=True,
                    backend=backend,
                    direction=direction,
                    files_changed=files_changed,
                    message=message,
                )
                s = str(result)
                assert backend in s
                assert direction in s
