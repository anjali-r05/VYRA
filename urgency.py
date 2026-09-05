"""
Urgency Module

Determines how urgent a message is: "low" | "medium" | "high" plus a
normalized 0-100 numeric score used by the scoring engine.

Public interface: `analyze_urgency(text, time_sensitive, deadline) -> UrgencyResult`
"""

from dataclasses import dataclass

HIGH_URGENCY_PHRASES = [
    "urgent", "asap", "immediately", "right now", "emergency",
    "as soon as possible", "critical", "deadline today", "today only",
    "act fast", "final call", "last chance",
]

MEDIUM_URGENCY_PHRASES = [
    "soon", "this week", "reminder", "don't forget", "upcoming",
    "please respond", "by tomorrow", "shortly",
]


@dataclass
class UrgencyResult:
    level: str      # low | medium | high
    score: float     # 0-100


def analyze_urgency(
    text: str,
    time_sensitive: bool,
    deadline: str | None,
    deadline_strength: str | None = None,
) -> UrgencyResult:
    text_lower = text.lower()

    high_hits = sum(1 for phrase in HIGH_URGENCY_PHRASES if phrase in text_lower)
    medium_hits = sum(1 for phrase in MEDIUM_URGENCY_PHRASES if phrase in text_lower)

    score = 20.0  # baseline for a message with no urgency signals

    if high_hits > 0:
        score = 90.0
    elif medium_hits > 0:
        score = 55.0

    # A STRONG deadline (explicit time/day) meaningfully raises urgency.
    # A WEAK deadline (a bare "today"/"tonight" with no specific time —
    # common in promotional copy) should only nudge urgency slightly,
    # since it isn't a real scheduling commitment.
    if deadline_strength == "strong":
        deadline_lower = (deadline or "").lower()
        if "today" in deadline_lower or "tonight" in deadline_lower:
            score = max(score, 90.0)
        elif "tomorrow" in deadline_lower:
            score = max(score, 80.0)
        else:
            score = max(score, 65.0)
    elif deadline_strength == "weak":
        score = max(score, 35.0)

    score = max(0.0, min(100.0, score))

    if score >= 75:
        level = "high"
    elif score >= 45:
        level = "medium"
    else:
        level = "low"

    return UrgencyResult(level=level, score=score)