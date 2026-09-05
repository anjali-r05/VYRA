"""
VYRA V3 — Task Extraction

Deterministic, rule-based extraction of actionable tasks from a message's
text. Reuses V1's existing deadline patterns (agent/context.py) and action
phrase list (agent/analyzer.py) rather than duplicating that logic — no
new regex/keyword source of truth is introduced here.

Public interface: `extract_tasks(text) -> list[dict]`

Each returned dict has: title, description, deadline, priority
(no DB writes here — app.py persists these into the Task model after the
parent Message row has an id, same pattern used for Decision/Feedback).
"""

import re

from agent.context import STRONG_DEADLINE_PATTERNS, WEAK_DEADLINE_PATTERNS
from agent.analyzer import ACTION_PHRASES

# A few additional action verbs beyond ACTION_PHRASES (which is tuned for
# V1's binary action_required flag) that are specifically useful for
# identifying a *task title* — e.g. "submit", "review" imply a concrete
# task even without the exact V1 phrasing like "please submit".
TASK_VERB_HINTS = [
    "submit", "review", "send", "complete", "sign", "pay", "reply",
    "confirm", "register", "upload", "attend", "prepare", "finish",
    "return", "renew", "update", "schedule", "book",
]

# Supplementary deadline phrasing specific to task extraction. This does
# NOT modify agent/context.py's own pattern lists — it only adds patterns
# task_extractor.py itself checks, since a task like "review this before
# Monday" is a real deadline that V1's own (deliberately narrower) "by
# <weekday>" pattern doesn't cover.
TASK_SUPPLEMENTARY_STRONG_PATTERNS = [
    r"\bbefore\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    r"\bbefore\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",
]

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _sentence_has_task_signal(sentence_lower: str) -> bool:
    if any(phrase in sentence_lower for phrase in ACTION_PHRASES):
        return True
    return any(verb in sentence_lower for verb in TASK_VERB_HINTS)


def _detect_sentence_deadline(sentence_lower: str) -> tuple:
    for pattern in STRONG_DEADLINE_PATTERNS + TASK_SUPPLEMENTARY_STRONG_PATTERNS:
        m = re.search(pattern, sentence_lower)
        if m:
            return m.group(0).strip(), "strong"
    for pattern in WEAK_DEADLINE_PATTERNS:
        m = re.search(pattern, sentence_lower)
        if m:
            return m.group(0).strip(), "weak"
    return None, None


def _priority_for(deadline_strength):
    if deadline_strength == "strong":
        return "high"
    if deadline_strength == "weak":
        return "medium"
    return "low"


def _make_title(sentence: str) -> str:
    words = sentence.strip().split()
    title = " ".join(words[:10])
    if len(words) > 10:
        title += "..."
    return title


def extract_tasks(text: str) -> list:
    if not text or not text.strip():
        return []

    sentences = [s.strip() for s in SENTENCE_SPLIT_RE.split(text) if s.strip()]
    if not sentences:
        sentences = [text.strip()]

    tasks = []
    seen_titles = set()

    for sentence in sentences:
        sentence_lower = sentence.lower()
        if not _sentence_has_task_signal(sentence_lower):
            continue

        deadline, strength = _detect_sentence_deadline(sentence_lower)
        title = _make_title(sentence)

        if title in seen_titles:
            continue
        seen_titles.add(title)

        tasks.append({
            "title": title,
            "description": sentence,
            "deadline": deadline,
            "priority": _priority_for(strength),
        })

    return tasks