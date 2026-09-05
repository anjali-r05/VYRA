"""
VYRA V3 — Adaptive Attention

V2 already personalizes scores per-category from real feedback history.
Adaptive Attention adds one further, narrow, deterministic layer: it
distinguishes a genuinely important message that happens to sit in a
normally low-priority category (e.g. "University fee waiver — applications
close tonight" filed as "promotion") from truly generic low-priority
content (e.g. "Flash sale on shoes").

This is intentionally NARROW rather than a second general-purpose scoring
system — V2 already owns "does the user generally care about this
category". V3's job here is purely: "does THIS SPECIFIC message have a
real deadline AND a required action, despite its category's normal
treatment?" That combination is a strong, deterministic, explainable
signal that content is a genuine opportunity, not noise.

NOTE on relevance: V1's own relevance module (agent/relevance.py) scores
low-priority categories like "promotion" in a fixed, narrow ~15-21 range
regardless of message content — relevance is deliberately category-driven
in V1, not content-driven. Because of that, relevance is used here only
as supporting context in the explanation, never as a hard gate — a hard
minimum-relevance requirement would make this override mathematically
unreachable for exactly the categories it's meant to help. (This was
caught during testing: an earlier version of this module gated on
relevance >= 50, which no promotion-category message could ever satisfy.)

Public interface: `compute_adaptive_adjustment(v2_result) -> (adjustment, reasons)`
"""

# Bounded independently of V2's own cap (agent/personalization.py's
# PERSONALIZATION_MAX_ADJUSTMENT) — the two layers are additive but each
# individually small, so neither can alone swing a decision across more
# than one tier without a second corroborating layer agreeing.
ADAPTIVE_MAX_ADJUSTMENT = 10.0

LOW_PRIORITY_CATEGORIES = {"promotion", "shopping", "spam", "social"}


def compute_adaptive_adjustment(v2_result: dict) -> tuple:
    """
    v2_result: the dict returned by agent.analyzer_v2.analyze_message_personalized()
    Returns (adjustment: float, reasons: list[str]).
    """
    if v2_result.get("suspicious"):
        # Never let a contextual "this looks like an opportunity" heuristic
        # touch a message V1's safety layer already flagged suspicious —
        # a scam can easily contain a fake deadline and a fake "confirm now"
        # call to action, which is exactly the pattern this module looks for.
        return 0.0, ["Adaptive attention is not applied to messages flagged suspicious by V1's safety check."]

    category = v2_result.get("category")
    deadline = v2_result.get("deadline")
    action_required = v2_result.get("action_required", False)
    relevance = v2_result.get("relevance", 0)

    if category in LOW_PRIORITY_CATEGORIES and deadline and action_required:
        return ADAPTIVE_MAX_ADJUSTMENT, [
            f"This '{category}' message has a real deadline ({deadline}) and requires action "
            f"(relevance {relevance:.0f}) — likely a genuine opportunity rather than generic {category} content."
        ]

    return 0.0, ["No contextual override needed beyond V1's category-based and V2's feedback-based scoring."]