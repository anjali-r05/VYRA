"""
VYRA V2 — Personalization Engine

Computes a bounded, explainable adjustment to a message's V1 attention
score, based ONLY on real feedback the user has previously given via
POST /api/feedback (the existing 👍 Correct / 👎 Wrong buttons on the
Analyze page).

Design principles (see project brief for the full rationale):

1. NO new database tables. Personalization is derived live from the
   existing `Feedback` + `Message` tables, joined by category. This
   keeps V1's schema completely untouched and avoids any migration risk.

2. NO fabricated data. If a category has zero feedback, the adjustment
   is exactly 0 and the reason explicitly says VYRA hasn't learned
   anything about it yet.

3. Bounded and conservative. The adjustment is capped so it can never
   swing the attention score by more than PERSONALIZATION_MAX_ADJUSTMENT
   points in either direction — personalization nudges the V1 score, it
   never replaces it.

4. Safety-aware by construction: analyzer_v2.py (which calls this
   module) refuses to apply a *negative* adjustment to messages V1 flagged
   as high-urgency + action-required, and refuses to apply ANY adjustment
   to messages V1 flagged suspicious. This module itself stays unaware of
   that policy — it only computes what the raw feedback history suggests.

Public interface:
    compute_personalization_adjustment(category) -> PersonalizationResult
    get_category_profile(category) -> CategoryFeedbackStats   (used by Attention DNA)
    get_all_category_profiles() -> dict[str, CategoryFeedbackStats]
"""

from dataclasses import dataclass, field
from typing import Optional

# Maximum points the personalization layer may add or subtract from the
# V1 attention score. Deliberately small relative to the 0-100 scale and
# to config.DECISION_THRESHOLDS's 40-point gap between tiers, so a
# single tier shift (e.g. digest -> notify) requires a real, repeated
# feedback pattern, not one data point.
PERSONALIZATION_MAX_ADJUSTMENT = 15.0

# Points of adjustment contributed per relevant feedback entry, before
# the cap above is applied.
POINTS_PER_FEEDBACK = 4.0


@dataclass
class CategoryFeedbackStats:
    category: str
    total_feedback: int
    correct_count: int
    wrong_count: int
    wrong_notify_count: int   # user said a NOTIFY decision in this category was wrong (over-notified)
    wrong_low_count: int      # user said a DIGEST/MUTE decision in this category was wrong (under-prioritized)


@dataclass
class PersonalizationResult:
    category: str
    adjustment: float               # points, already bounded to +/- PERSONALIZATION_MAX_ADJUSTMENT
    reasons: list = field(default_factory=list)
    stats: Optional[CategoryFeedbackStats] = None


def get_category_profile(category: str, user_id: int | None = None) -> CategoryFeedbackStats:
    """
    Query real, already-stored feedback for a single category. This is a
    live aggregation over existing rows — no caching table, no new schema.
    """
    from models import Feedback, Message
    from services.database import db

    query = (
        db.session.query(Feedback.feedback, Feedback.original_decision)
        .join(Message, Message.id == Feedback.message_id)
        .filter(Message.category == category)
    )
    if user_id is not None:
        query = query.filter(Message.user_id == user_id)
    rows = query.all()

    total = len(rows)
    correct = sum(1 for f, _ in rows if f == "correct")
    wrong = sum(1 for f, _ in rows if f == "wrong")
    wrong_notify = sum(1 for f, d in rows if f == "wrong" and d == "notify")
    wrong_low = sum(1 for f, d in rows if f == "wrong" and d in ("digest", "mute"))

    return CategoryFeedbackStats(
        category=category,
        total_feedback=total,
        correct_count=correct,
        wrong_count=wrong,
        wrong_notify_count=wrong_notify,
        wrong_low_count=wrong_low,
    )


def get_all_category_profiles(user_id: int | None = None) -> dict:
    """Used by the Attention DNA page to show a learned profile per category."""
    from config import ALL_CATEGORIES
    return {cat: get_category_profile(cat, user_id=user_id) for cat in ALL_CATEGORIES}


def compute_personalization_adjustment(category: str, user_id: int | None = None) -> PersonalizationResult:
    """
    Turn one category's real feedback history into a small, bounded,
    explainable score adjustment.

    Rule (deliberately simple and interpretable, not a black box):
      - If the user has marked more "wrong" feedback on NOTIFY decisions
        than on DIGEST/MUTE decisions in this category, VYRA has been
        over-notifying -> adjustment is negative (lower future scores).
      - If the opposite is true, VYRA has been under-prioritizing this
        category -> adjustment is positive (raise future scores).
      - If feedback is balanced or absent, adjustment is 0.
    """
    stats = get_category_profile(category, user_id=user_id)

    if stats.total_feedback == 0:
        return PersonalizationResult(
            category=category,
            adjustment=0.0,
            reasons=["VYRA hasn't received any feedback for this category yet."],
            stats=stats,
        )

    reasons = []
    if stats.wrong_low_count > stats.wrong_notify_count:
        raw = min(PERSONALIZATION_MAX_ADJUSTMENT, stats.wrong_low_count * POINTS_PER_FEEDBACK)
        reasons.append(
            f"You marked {stats.wrong_low_count} lower-priority decision"
            f"{'s' if stats.wrong_low_count != 1 else ''} in '{category}' as wrong — "
            f"VYRA now weighs this category higher."
        )
        adjustment = raw
    elif stats.wrong_notify_count > stats.wrong_low_count:
        raw = min(PERSONALIZATION_MAX_ADJUSTMENT, stats.wrong_notify_count * POINTS_PER_FEEDBACK)
        reasons.append(
            f"You marked {stats.wrong_notify_count} Notify decision"
            f"{'s' if stats.wrong_notify_count != 1 else ''} in '{category}' as wrong — "
            f"VYRA now weighs this category lower."
        )
        adjustment = -raw
    else:
        adjustment = 0.0
        if stats.correct_count > 0:
            reasons.append(
                f"Your past feedback in '{category}' confirmed VYRA's decisions "
                f"{stats.correct_count} time{'s' if stats.correct_count != 1 else ''} — no adjustment needed."
            )
        else:
            reasons.append(f"Feedback for '{category}' is currently balanced — no adjustment applied.")

    return PersonalizationResult(category=category, adjustment=adjustment, reasons=reasons, stats=stats)