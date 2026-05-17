"""Tests for ompa.classifier — MessageClassifier."""


class TestClassifier:
    """Test message classifier."""

    def test_decision(self):
        from ompa import MessageClassifier

        c = MessageClassifier()
        result = c.classify("We decided to go with Postgres")
        assert result.message_type.value == "decision"
        assert result.confidence >= 0.3

    def test_win(self):
        from ompa import MessageClassifier

        c = MessageClassifier()
        result = c.classify("We won the enterprise deal!")
        assert result.message_type.value == "win"

    def test_incident(self):
        from ompa import MessageClassifier

        c = MessageClassifier()
        result = c.classify("The auth bug is blocking deployment")
        assert result.message_type.value == "incident"

    def test_question(self):
        from ompa import MessageClassifier

        c = MessageClassifier()
        result = c.classify("Should we use Clerk or Auth0 for auth?")
        assert result.message_type.value == "question"

    def test_suggestion(self):
        from ompa import MessageClassifier

        c = MessageClassifier()
        result = c.classify("We should add tests before merging")
        assert result.message_type.value in ("task", "unknown")

    def test_blocker(self):
        from ompa import MessageClassifier

        c = MessageClassifier()
        result = c.classify("I'm blocked on the API design")
        assert result.message_type.value == "architecture"

    def test_learning(self):
        from ompa import MessageClassifier

        c = MessageClassifier()
        result = c.classify("TIL that Postgres has built-in full-text search")
        assert result.message_type.value in ("brain-dump", "code", "unknown")

    def test_retrospective(self):
        from ompa import MessageClassifier

        c = MessageClassifier()
        result = c.classify("In our retrospective we found three issues")
        assert result.message_type.value == "meeting"

    def test_classifier_vault_target(self):
        """Classifier should suggest vault targets."""
        from ompa import MessageClassifier

        c = MessageClassifier()
        assert c.classify_vault_target("We decided to use Postgres") == "shared"
        assert c.classify_vault_target("random stuff") == "ambiguous"
