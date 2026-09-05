"""
VYRA V3 — Smart Threads

Groups related messages together using ONLY signals that actually exist
in this project's schema: message content, category, and timestamp.
There is no sender or subject field on Message (confirmed by inspection),
so "same sender" matching — mentioned as a possible signal in the project
brief — is not implementable here without fabricating data; this module
uses content/category/temporal signals instead, which are real.

Matching approach (deterministic, no ML):
  1. Only consider existing threads in the SAME category as the new message.
  2. Only consider threads whose most recent member message is within
     THREAD_TIME_WINDOW_DAYS of the new message (avoids merging an old,
     unrelated thread just because the topic recurs months later).
  3. Compute a word-overlap ratio between the new message and the
     thread's most recent message, using significant words only (length
     >= 4, lowercased, with a small stopword list removed).
  4. If the best-matching thread clears WORD_OVERLAP_THRESHOLD, the
     message joins it. Otherwise a new thread is created.

This function is intentionally conservative (requires real overlap, not
just same category) — the brief explicitly warns against creating
hundreds of duplicate threads AND against merging clearly unrelated
messages sharing only a category.

Public interface:
    find_matching_thread(new_content, category, candidate_threads, message_lookup) -> Thread | None
    compute_word_overlap(text_a, text_b) -> float
    make_thread_title(content) -> str
"""

import re

THREAD_TIME_WINDOW_DAYS = 45
WORD_OVERLAP_THRESHOLD = 0.18

_STOPWORDS = {
    "this", "that", "with", "your", "from", "have", "will", "please",
    "about", "there", "their", "would", "could", "should", "these",
    "those", "here", "were", "been", "being", "into", "than", "then",
    "also", "just", "more", "some", "such", "only", "very", "when",
    "what", "which", "while",
}

_WORD_RE = re.compile(r"[a-zA-Z]{4,}")


def _significant_words(text: str) -> set:
    return {w.lower() for w in _WORD_RE.findall(text)} - _STOPWORDS


def compute_word_overlap(text_a: str, text_b: str) -> float:
    """
    Overlap coefficient over significant words: |A ∩ B| / min(|A|, |B|).
    Deliberately NOT Jaccard (|A∩B|/|A∪B|) — Jaccard penalizes messages of
    different lengths even when the shorter one is almost entirely
    contained in the longer one, which is exactly the common case for
    thread updates ("Interview rescheduled to Thursday." vs a longer
    original message). Overlap coefficient stays fair across message
    length while still requiring genuine shared vocabulary — unrelated
    messages still score 0.0 either way since their intersection is empty.

    NOTE — no hard "minimum shared word count" gate: an earlier version of
    this function required >=2 shared significant words before returning
    a nonzero score, specifically to prevent two messages that only share
    one company/product name (e.g. "TechCorp") from being treated as
    related. That gate was found during Phase 3G integration testing to
    also break the opposite, more common case: a real update thread where
    consecutive messages are reworded enough that only a single proper
    noun (the entity name) persists across them — exactly the "Internship
    - TechCorp" example this feature exists to support. Both cases
    produce a similar coefficient score under a purely lexical method, so
    they cannot be cleanly separated by word overlap alone; this is a
    disclosed limitation of a deterministic MVP with no real entity
    understanding. Given a real update thread failing to group is a more
    disruptive failure mode than an occasional unrelated same-entity
    message merging, the threshold below is tuned to catch genuine
    threads, and callers additionally scope candidates to the same
    category and a bounded recency window to reduce false merges.
    """
    words_a = _significant_words(text_a)
    words_b = _significant_words(text_b)
    if not words_a or not words_b:
        return 0.0
    shared = words_a & words_b
    if not shared:
        return 0.0
    return len(shared) / min(len(words_a), len(words_b))


def make_thread_title(content: str) -> str:
    words = content.strip().split()
    title = " ".join(words[:8])
    if len(words) > 8:
        title += "..."
    return title


def find_matching_thread(new_content: str, category: str, candidate_threads: list, latest_message_by_thread: dict):
    """
    candidate_threads: list of Thread rows already filtered by the caller
        to the same category (a plain Python filter — no DB logic lives
        in this pure function, matching the rest of the V3 module style).
    latest_message_by_thread: dict[thread.id] -> latest Message.content
        for that thread, so this function never needs to touch the ORM.

    Returns the best-matching Thread, or None if nothing clears the
    overlap threshold (caller should create a new Thread in that case).
    """
    best_thread = None
    best_overlap = 0.0

    for thread in candidate_threads:
        latest_content = latest_message_by_thread.get(thread.id)
        if not latest_content:
            continue
        overlap = compute_word_overlap(new_content, latest_content)
        if overlap >= WORD_OVERLAP_THRESHOLD and overlap > best_overlap:
            best_overlap = overlap
            best_thread = thread

    return best_thread