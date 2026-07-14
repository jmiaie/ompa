"""
Message classification for routing and context injection.
Classifies user messages into categories and injects routing hints.
Also classifies content for dual-vault routing (shared vs personal).
"""

import re
from enum import Enum
from dataclasses import dataclass, field


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


@dataclass(frozen=True)
class _TypeProfile:
    """
    Per-MessageType classification bundle: regex patterns, routing hints,
    suggested folder, and primary action.

    These four facets used to live in four separate dicts (PATTERNS,
    ROUTING_HINTS, FOLDER_MAP, and an inline `actions` dict) all keyed by
    MessageType. Keeping them as one dict of profiles means adding a new
    MessageType can't silently omit one of the facets — a missing field is a
    constructor error, not a silent `.get(..., default)` fallback elsewhere.
    """

    patterns: tuple[str, ...]
    routing_hints: tuple[str, ...] = field(default_factory=tuple)
    folder: str = "thinking/"
    action: str = "Continue conversation"


class MessageClassifier:
    """
    Classifies user messages and provides routing guidance.
    Inspired by obsidian-minds classify-message.py but framework-agnostic.
    """

    # One profile per MessageType — patterns, routing hints, folder, and action.
    _PROFILES: dict[MessageType, _TypeProfile] = {
        MessageType.DECISION: _TypeProfile(
            patterns=(
                r"\b(decided|decision|we chose|going with|settled on|agreed to)\b",
                r"\b(defer|postpone|push to|revisit)\b.*\b(Q\d|quarter|sprint)\b",
                r"(?:ADR|decision record)",
            ),
            routing_hints=(
                "This is a decision. Record it in brain/Key Decisions.md",
                "Update relevant project notes with the decision",
                "Add to Decision Log if formal ADR needed",
            ),
            folder="work/active/",
            action="Record decision and update relevant project notes",
        ),
        MessageType.INCIDENT: _TypeProfile(
            patterns=(
                r"\b(incident|outage|bug|crash|failure|error|broken)\b",
                r"\b(debug|root cause|RCA|mitigation|hotfix)\b",
                r"(?:on-call|pagerduty|statuspage)",
            ),
            routing_hints=(
                "Create incident note in work/incidents/",
                "Run /om-incident-capture for structured capture",
                "Update brain/Gotchas.md with lessons learned",
            ),
            folder="work/incidents/",
            action="Create incident note and capture details",
        ),
        MessageType.WIN: _TypeProfile(
            patterns=(
                r"\b(won|praised|success|achieved|shipped|launched|deployed)\b",
                r"\b(great work|nice job|excellent|well done)\b",
                r"\b(milestone|feature complete|released)\b",
            ),
            routing_hints=(
                "Add to perf/Brag Doc.md immediately",
                "This is a win worth capturing for performance review",
                "Update relevant competency notes with evidence",
            ),
            folder="perf/brag/",
            action="Add to Brag Doc and update competency evidence",
        ),
        MessageType.ONE_ON_ONE: _TypeProfile(
            patterns=(
                r"\b(1:1|one-on-one|1on1|check-in|sync)\b.*\b(manager|lead|peer|coworker)\b",
                r"\b(feedback|concerns|growth|progress|career)\b",
                r"\b(1-on-1|weekly sync|bias for action)\b",
            ),
            routing_hints=(
                "Create or update 1:1 note in work/1-1/",
                "Run /om-prep-1on1 if preparing for upcoming 1:1",
            ),
            folder="work/1-1/",
            action="Create or update 1:1 meeting note",
        ),
        MessageType.MEETING: _TypeProfile(
            patterns=(
                r"\b(meeting|briefing|session|call)\b",
                r"\b(agenda|notes|takeaways|action items)\b",
                r"\b(prep for|preparing for|standup|retrospective)\b",
            ),
            routing_hints=(
                "Run /om-meeting for structured meeting prep",
                "Add to work/meetings/ inbox for later processing",
            ),
            folder="work/meetings/",
            action="Create meeting prep or capture notes",
        ),
        MessageType.PROJECT_UPDATE: _TypeProfile(
            patterns=(
                r"\b(project|initiative|epic|feature|story|ticket)\b.*\b(update|progress|status|blocked)\b",
                r"\b(working on|started|finished|continuing)\b",
                r"\b(blocked on|waiting for|depends on)\b",
            ),
            routing_hints=(
                "Update work/active/ project note",
                "Check if project status has changed",
            ),
            folder="work/active/",
            action="Update project status in work/active/",
        ),
        MessageType.PERSON_INFO: _TypeProfile(
            patterns=(
                r"\b(teammate|coworker|peer|manager|lead|engineer)\b.*\b(joined|moved|left|new|role)\b",
                r"\b(people|team|person)\b.*\b(update|change|info)\b",
                r"\b(Sarah|John|Mike|Tom|Jane)\b",  # Names as hints
            ),
            routing_hints=(
                "Update org/people/ note for this person",
                "Run /om-people-profiler for context",
            ),
            folder="org/people/",
            action="Update person note in org/people/",
        ),
        MessageType.QUESTION: _TypeProfile(
            patterns=(
                r"\b(how do|how can|what is|what are|why does|can we|should we)\b",
                r"\?",  # Question marks
                r"\b(clarify|explain|help me understand)\b",
            ),
            routing_hints=(
                "Answer directly with available context",
                "Consider searching vault for relevant knowledge first",
            ),
            folder="thinking/",
            action="Search vault and answer from context",
        ),
        MessageType.TASK: _TypeProfile(
            patterns=(
                r"\b(task|todo|action item|follow-up)\b",
                r"\b(do this|handle|take care of|responsible for)\b",
                r"\-\s*\[[x ]\]",  # Checkbox syntax
            ),
            routing_hints=(
                "Add to task list / obsidian tasks",
                "Track completion in relevant project note",
            ),
            folder="work/active/",
            action="Add to task list",
        ),
        MessageType.ARCHITECTURE: _TypeProfile(
            patterns=(
                r"\b(architecture|design|system design| ADR |technical design)\b",
                r"\b(api|service|microservice|backend|frontend|infrastructure)\b.*\b(design|decide|approach)\b",
                r"\b(migration|refactor|deprecate|legacy)\b",
            ),
            routing_hints=(
                "Consider creating ADR in work/active/",
                "Update reference/architecture docs",
            ),
            folder="work/active/",
            action="Consider creating ADR",
        ),
        MessageType.CODE: _TypeProfile(
            patterns=(
                r"\b(code|function|class|module|import|export)\b",
                r"\b(python|rust|javascript|typescript|java|go)\b",
                r"\b(bug fix|feature|PR|pull request|commit)\b",
            ),
            routing_hints=(
                "Focus on implementation details",
                "Update relevant code documentation",
            ),
            folder="reference/",
            action="Focus on implementation",
        ),
        MessageType.BRAIN_DUMP: _TypeProfile(
            patterns=(
                r"\b(dump|stream of consciousness|random thoughts|everything on my mind)\b",
                r"\b(btw|also|oh and|forgot to mention)\b",
            ),
            routing_hints=(
                "Run /om-dump for freeform capture",
                "Route to appropriate notes automatically",
            ),
            folder="thinking/",
            action="Run /om-dump to route content",
        ),
        MessageType.WRAP_UP: _TypeProfile(
            patterns=(
                r"\b(wrap up|wrapping up|finish up|end session|done for today)\b",
            ),
            routing_hints=(
                "Run /om-wrap-up for session review",
                "Verify all notes have links",
                "Update indexes before closing",
            ),
            folder="brain/",
            action="Run /om-wrap-up session checklist",
        ),
        MessageType.STANDUP: _TypeProfile(
            patterns=(
                r"\b(standup|daily|start of day|morning kickoff)\b",
                r"\b(start session|start work|starting)\b",
            ),
            routing_hints=(
                "Run /om-standup for structured morning context",
                "Read brain/North Star.md first",
            ),
            folder="brain/",
            action="Run /om-standup for morning context",
        ),
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
        scores: dict[MessageType, int] = {}

        for msg_type, profile in self._PROFILES.items():
            score = 0
            for pattern in profile.patterns:
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
        best_type = max(scores, key=scores.__getitem__)
        confidence = min(scores[best_type] / 3.0, 1.0)  # Normalize to 0-1

        # For short messages, reduce confidence
        if len(message.split()) < 5:
            confidence *= 0.7

        profile = self._PROFILES[best_type]
        return Classification(
            message_type=best_type,
            confidence=confidence,
            routing_hints=list(profile.routing_hints),
            suggested_folder=profile.folder,
            suggested_action=profile.action,
        )

    def get_routing_hint(self, message: str) -> str:
        """
        Get a single-line routing hint for a message.
        Suitable for injecting into context.
        """
        classification = self.classify(message)
        return f"[{classification.message_type.value.upper()}] {classification.suggested_action}"

    # Shared-vault message types (team-visible content)
    SHARED_TYPES = {
        MessageType.DECISION,
        MessageType.MEETING,
        MessageType.PROJECT_UPDATE,
        MessageType.PERSON_INFO,
        MessageType.ONE_ON_ONE,
        MessageType.WIN,
        MessageType.ARCHITECTURE,
        MessageType.STANDUP,
        MessageType.WRAP_UP,
    }

    # Personal-vault message types (agent-private content)
    PERSONAL_TYPES = {
        MessageType.BRAIN_DUMP,
    }

    def classify_vault_target(self, message: str) -> str:
        """
        Classify whether a message belongs in shared or personal vault.

        Returns: "shared", "personal", or "ambiguous"
        """
        classification = self.classify(message)

        if classification.message_type in self.SHARED_TYPES:
            return "shared"
        if classification.message_type in self.PERSONAL_TYPES:
            return "personal"
        return "ambiguous"
