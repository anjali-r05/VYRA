"""
Context Module

Responsible for understanding WHAT a message is about:
- category classification
- intent detection
- deadline extraction

This is deliberately rule/keyword based for V1. A future version could
swap this for an ML classifier without changing its public interface:
`analyze_context(text) -> ContextResult`
"""

import re
from dataclasses import dataclass, field
from typing import Optional


CATEGORY_KEYWORDS = {
    "career": [
        "interview", "job offer", "resume", "cv", "recruiter", "hiring",
        "position", "shortlisted", "internship", "offer letter", "job application",
    ],
    "academic": [
        "exam", "assignment", "homework", "syllabus", "college", "school",
        "class", "lecture", "semester", "board exam", "result", "marks",
        "admission", "professor", "teacher", "submission", "project deadline",
    ],
    "projects": [
        "pull request", "merge", "deploy", "bug", "repository", "sprint",
        "standup", "codebase", "pipeline", "build failed", "release",
    ],
    "family": [
        "mom", "dad", "brother", "sister", "grandma", "grandpa", "family",
        "dinner tonight", "come home", "uncle", "aunt",
    ],
    "meeting": [
        "meeting", "call scheduled", "zoom", "google meet", "conference",
        "sync up", "catch up call", "agenda",
    ],
    "reminder": [
        "reminder", "don't forget", "just a heads up", "friendly reminder",
    ],
    "promotion": [
        "sale", "discount", "% off", "offer ends", "buy now", "flash sale",
        "coupon", "limited time", "shop now", "deal of the day",
    ],
    "shopping": [
        "order shipped", "delivery", "your order", "tracking number", "cart",
        "out for delivery", "order confirmed",
    ],
    "social": [
        "liked your post", "commented on", "tagged you", "followed you",
        "friend request", "new follower",
    ],
    "spam": [
        "unsubscribe", "you have been selected", "congratulations you won",
        "free gift", "click below",
    ],
}

INTENT_KEYWORDS = {
    "interview": ["interview"],
    "required_action": [
        "please confirm", "please submit", "action required", "please respond",
        "kindly reply", "rsvp", "please complete", "please sign", "please pay",
    ],
    "informational": ["fyi", "just so you know", "heads up", "for your information"],
    "invitation": ["invite you", "join us", "you're invited", "invitation"],
    "warning": ["warning", "alert", "caution", "urgent notice"],
}

# STRONG patterns carry a specific, actionable time reference (explicit
# clock time, a named weekday, or "by/due/within" phrasing) and are
# treated as real deadlines. WEAK patterns are bare temporal words
# ("today", "tonight") that appear constantly in low-value content
# (e.g. "Buy shoes today!") and should not by themselves be read as a
# scheduling deadline.
STRONG_DEADLINE_PATTERNS = [
    r"\btomorrow\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b",
    r"\btoday\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b",
    r"\bby\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b",
    r"\bwithin\s+\d+\s+(?:hours?|days?|minutes?)\b",
    r"\bdue\s+(?:on\s+)?\w+\b",
    r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",
]

WEAK_DEADLINE_PATTERNS = [
    r"\btomorrow\b",
    r"\btoday\b",
    r"\btonight\b",
    r"\bnext\s+week\b",
]


@dataclass
class ContextResult:
    category: str
    intent: str
    deadline: Optional[str] = None
    deadline_strength: Optional[str] = None  # "strong" | "weak" | None
    time_sensitive: bool = False
    matched_keywords: list = field(default_factory=list)


def _detect_category(text_lower: str) -> tuple[str, list]:
    best_category = "general"
    best_matches = []
    best_count = 0
    for category, keywords in CATEGORY_KEYWORDS.items():
        matches = [kw for kw in keywords if kw in text_lower]
        if len(matches) > best_count:
            best_count = len(matches)
            best_category = category
            best_matches = matches
    return best_category, best_matches


def _detect_intent(text_lower: str) -> str:
    for intent, keywords in INTENT_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return intent
    return "general"


def _detect_deadline(text_lower: str) -> tuple[Optional[str], Optional[str]]:
    for pattern in STRONG_DEADLINE_PATTERNS:
        match = re.search(pattern, text_lower)
        if match:
            return match.group(0).strip(), "strong"
    for pattern in WEAK_DEADLINE_PATTERNS:
        match = re.search(pattern, text_lower)
        if match:
            return match.group(0).strip(), "weak"
    return None, None


def analyze_context(text: str) -> ContextResult:
    text_lower = text.lower()

    category, matched_keywords = _detect_category(text_lower)
    intent = _detect_intent(text_lower)
    deadline, deadline_strength = _detect_deadline(text_lower)
    time_sensitive = deadline_strength == "strong"

    return ContextResult(
        category=category,
        intent=intent,
        deadline=deadline,
        deadline_strength=deadline_strength,
        time_sensitive=time_sensitive,
        matched_keywords=matched_keywords,
    )