"""
Message classification for routing and context injection.
Classifies user messages into categories and injects routing hints.
"""

import re
from enum import Enum
from dataclasses import dataclass


class MessageType(Enum):
    DECISION = "decision"
    INCIDENT = "incident"
    WIN = "win"
    ONE_ON_ONE = "one-on-one"
    MEETING = "meeting"
    PROJECT_UPDATE = "project-update"
    PERSON_INFO = "person-info"
    QUESTION = "question"
    TASK = "task"
    ARCHITECTURE = "architecture"
    CODE = "code"
    BRAIN_DUMP = "brain-dump"
    WRAP_UP = "wrap-up"
    STANDUP = "standup"
    UNKNOWN = "unknown"


@dataclass
class Classification:
    message_type: MessageType
    confidence: float
    routing_hints: list[str]
    suggested_folder: str
    suggested_action: str


class MessageClassifier:
    """
    Classifies user messages and provides routing guidance.
    Inspired by obsidian-minds classify-message.py but framework-agnostic.
    """

    # Regex patterns for classification
    PATTERNS = {
        MessageType.DECISION: [
            r"\b(decided|decision|we chose|going with|settled on|agreed to)\b",
            r"\b(defer|postpone|push to|revisit)\b.*\b(Q\d|quarter|sprint)\b",
            r"(?:ADR|decision record)",
        ],
        MessageType.INCIDENT: [
            r"\b(incident|outage|bug|crash|failure|error|broken)\b",
            r"\b(debug|root cause|RCA|mitigation|hotfix)\b",
            r"(?:on-call|pagerduty|statuspage)",
        ],
        MessageType.WIN: [
            r"\b(won|praised|success|achieved|shipped|launched|deployed)\b",
            r"\b(great work|nice job|excellent|well done)\b",
            r"\b(milestone|feature complete|released)\b",
        ],
        MessageType.ONE_ON_ONE: [
            r"\b(1:1|one-on-one|1on1|check-in|sync)\b.*\b(manager|lead|peer|coworker)\b",
            r"\b(feedback|concerns|growth|progress|career)\b",
            r"\b(1-on-1|weekly sync|bias for action)\b",
        ],
        MessageType.MEETING: [
            r"\b(meeting|briefing|session|call)\b",
            r"\b(agenda|notes|takeaways|action items)\b",
            r"\b(prep for|preparing for|standup|retrospective)\b",
        ],
        MessageType.PROJECT_UPDATE: [
            r"\b(project|initiative|epic|feature|story|ticket)\b.*\b(update|progress|status|blocked)\b",
            r"\b(working on|started|finished|continuing)\b",
            r"\b(blocked on|waiting for|depends on)\b",
        ],
        MessageType.PERSON_INFO: [
            r"\b(teammate|coworker|peer|manager|lead|engineer)\b.*\b(joined|moved|left|new|role)\b",
            r"\b(people|team|person)\b.*\b(update|change|info)\b",
            r"\b(Sarah|John|Mike|Tom|Jane)\b",  # Names as hints
        ],
        MessageType.QUESTION: [
            r"\b(how do|how can|what is|what are|why does|can we|should we)\b",
            r"\?",  # Question marks
            r"\b(clarify|explain|help me understand)\b",
        ],
        MessageType.TASK: [
            r"\b(task|todo|action item|follow-up)\b",
            r"\b(do this|handle|take care of|responsible for)\b",
            r"\-\s*\[[x ]\]",  # Checkbox syntax
        ],
        MessageType.ARCHITECTURE: [
            r"\b(architecture|design|system design| ADR |technical design)\b",
            r"\b(api|service|microservice|backend|frontend|infrastructure)\b.*\b(design|decide|approach)\b",
            r"\b(migration|refactor|deprecate|legacy)\b",
        ],
        MessageType.CODE: [
            r"\b(code|function|class|module|import|export)\b",
            r"\b(python|rust|javascript|typescript|java|go)\b",
            r"\b(bug fix|feature|PR|pull request|commit)\b",
        ],
        MessageType.BRAIN_DUMP: [
            r"\b(dump|stream of consciousness|random thoughts|everything on my mind)\b",
            r"\b(btw|also|oh and|forgot to mention)\b",
        ],
        MessageType.WRAP_UP: [
            r"\b(wrap up|wrapping up|finish up|end session|done for today)\b",
        ],
        MessageType.STANDUP: [
            r"\b(standup|daily|start of day|morning kickoff)\b",
            r"\b(start session|start work|starting)\b",
        ],
    }

    # Routing hints per message type
    ROUTING_HINTS = {
        MessageType.DECISION: [
            "This is a decision. Record it in brain/Key Decisions.md",
            "Update relevant project notes with the decision",
            "Add to Decision Log if formal ADR needed",
        ],
        MessageType.INCIDENT: [
            "Create incident note in work/incidents/",
            "Run /om-incident-capture for structured capture",
            "Update brain/Gotchas.md with lessons learned",
        ],
        MessageType.WIN: [
            "Add to perf/Brag Doc.md immediately",
            "This is a win worth capturing for performance review",
            "Update relevant competency notes with evidence",
        ],
        MessageType.ONE_ON_ONE: [
            "Create or update 1:1 note in work/1-1/",
            "Run /om-prep-1on1 if preparing for upcoming 1:1",
        ],
        MessageType.MEETING: [
            "Run /om-meeting for structured meeting prep",
            "Add to work/meetings/ inbox for later processing",
        ],
        MessageType.PROJECT_UPDATE: [
            "Update work/active/ project note",
            "Check if project status has changed",
        ],
        MessageType.PERSON_INFO: [
            "Update org/people/ note for this person",
            "Run /om-people-profiler for context",
        ],
        MessageType.QUESTION: [
            "Answer directly with available context",
            "Consider searching vault for relevant knowledge first",
        ],
        MessageType.TASK: [
            "Add to task list / obsidian tasks",
            "Track completion in relevant project note",
        ],
        MessageType.ARCHITECTURE: [
            "Consider creating ADR in work/active/",
            "Update reference/architecture docs",
        ],
        MessageType.CODE: [
            "Focus on implementation details",
            "Update relevant code documentation",
        ],
        MessageType.BRAIN_DUMP: [
            "Run /om-dump for freeform capture",
            "Route to appropriate notes automatically",
        ],
        MessageType.WRAP_UP: [
            "Run /om-wrap-up for session review",
            "Verify all notes have links",
            "Update indexes before closing",
        ],
        MessageType.STANDUP: [
            "Run /om-standup for structured morning context",
            "Read brain/North Star.md first",
        ],
    }

    # Suggested folder locations
    FOLDER_MAP = {
        MessageType.DECISION: "work/active/",
        MessageType.INCIDENT: "work/incidents/",
        MessageType.WIN: "perf/brag/",
        MessageType.ONE_ON_ONE: "work/1-1/",
        MessageType.MEETING: "work/meetings/",
        MessageType.PROJECT_UPDATE: "work/active/",
        MessageType.PERSON_INFO: "org/people/",
        MessageType.QUESTION: "thinking/",
        MessageType.TASK: "work/active/",
        MessageType.ARCHITECTURE: "work/active/",
        MessageType.CODE: "reference/",
        MessageType.BRAIN_DUMP: "thinking/",
        MessageType.WRAP_UP: "brain/",
        MessageType.STANDUP: "brain/",
    }

    def classify(self, message: str) -> Classification:
        """
        Classify a user message and return routing guidance.

        Args:
            message: The user message text

        Returns:
            Classification with type, confidence, hints, and suggested actions
        """
        message_lower = message.lower()
        scores = {}

        for msg_type, patterns in self.PATTERNS.items():
            score = 0
            for pattern in patterns:
                if re.search(pattern, message_lower, re.IGNORECASE):
                    score += 1
            if score > 0:
                scores[msg_type] = score

        if not scores:
            return Classification(
                message_type=MessageType.UNKNOWN,
                confidence=0.0,
                routing_hints=["Process as normal conversation"],
                suggested_folder="thinking/",
                suggested_action="Continue conversation normally",
            )

        # Get highest scoring type
        best_type = max(scores, key=scores.get)
        confidence = min(scores[best_type] / 3.0, 1.0)  # Normalize to 0-1

        # For short messages, reduce confidence
        if len(message.split()) < 5:
            confidence *= 0.7

        return Classification(
            message_type=best_type,
            confidence=confidence,
            routing_hints=self.ROUTING_HINTS.get(best_type, []),
            suggested_folder=self.FOLDER_MAP.get(best_type, "thinking/"),
            suggested_action=self._get_action(best_type),
        )

    def _get_action(self, msg_type: MessageType) -> str:
        """Get the primary action for a message type."""
        actions = {
            MessageType.DECISION: "Record decision and update relevant project notes",
            MessageType.INCIDENT: "Create incident note and capture details",
            MessageType.WIN: "Add to Brag Doc and update competency evidence",
            MessageType.ONE_ON_ONE: "Create or update 1:1 meeting note",
            MessageType.MEETING: "Create meeting prep or capture notes",
            MessageType.PROJECT_UPDATE: "Update project status in work/active/",
            MessageType.PERSON_INFO: "Update person note in org/people/",
            MessageType.QUESTION: "Search vault and answer from context",
            MessageType.TASK: "Add to task list",
            MessageType.ARCHITECTURE: "Consider creating ADR",
            MessageType.CODE: "Focus on implementation",
            MessageType.BRAIN_DUMP: "Run /om-dump to route content",
            MessageType.WRAP_UP: "Run /om-wrap-up session checklist",
            MessageType.STANDUP: "Run /om-standup for morning context",
            MessageType.UNKNOWN: "Continue conversation",
        }
        return actions.get(msg_type, "Continue conversation")

    def get_routing_hint(self, message: str) -> str:
        """
        Get a single-line routing hint for a message.
        Suitable for injecting into context.
        """
        classification = self.classify(message)
        return f"[{classification.message_type.value.upper()}] {classification.suggested_action}"
