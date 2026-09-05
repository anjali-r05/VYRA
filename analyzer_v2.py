"""
VYRA V2 — Personalized Analyzer

This module NEVER modifies agent/analyzer.py or any V1 pipeline file.
It calls the existing, unchanged V1 `analyze_message()` first, then
layers a bounded personalization adjustment on top.

Pipeline:

    MESSAGE
       |
       v
    V1 analyze_message()          <- completely unchanged, protected
       |
       v
    personalization adjustment     <- agent/personalization.py, derived
       |                              from real stored Feedback only
       v
    SAFETY GUARDRAILS               <- personalization is refused here
       |                              for suspicious / urgent+actionable
       |                              messages, see _apply_safety_guardrails
       v
    FINAL personalized score/decision

Public interface: `analyze_message_personalized(text, preferences=None) -> dict`

The returned dict is a superset of V1's own return shape (see
agent/analyzer.py) — every V1 key is still present with its original
V1 value, plus new "v2_*" and "final_*" keys. Nothing existing is
renamed or removed, so any code still expecting the V1 shape keeps working.
"""

from agent.analyzer import analyze_message
from agent.decision import make_decision
from agent.personalization import compute_personalization_adjustment


def _apply_safety_guardrails(v1_result: dict, adjustment: float) -> tuple:
    """
    Decide whether the raw personalization adjustment is allowed to apply,
    and if so whether it must be clamped further.

    Returns (allowed_adjustment, guardrail_note_or_None).
    """
    if v1_result["suspicious"]:
        return 0.0, "Personalization is not applied to messages flagged suspicious by V1's safety check."

    if adjustment < 0 and v1_result["urgency"] == "high" and v1_result["action_required"]:
        return 0.0, "Personalization cannot lower urgent, action-required messages — V1's urgency signal is protected."

    return adjustment, None


def analyze_message_personalized(text: str, preferences: dict | None = None, user_id: int | None = None) -> dict:
    # 1. Run V1 exactly as-is. This call, and everything inside it, is untouched.
    v1_result = analyze_message(text, preferences=preferences)

    # 2. Compute the real, feedback-derived personalization signal for this category.
    personalization = compute_personalization_adjustment(v1_result["category"], user_id=user_id)

    # 3. Apply safety guardrails before letting personalization touch the score.
    allowed_adjustment, guardrail_note = _apply_safety_guardrails(v1_result, personalization.adjustment)

    # 4. Compute the final, personalized score and decision.
    final_score = max(0.0, min(100.0, round(v1_result["attention_score"] + allowed_adjustment, 1)))
    final_decision = make_decision(final_score, suspicious=v1_result["suspicious"])

    v2_reasons = list(personalization.reasons)
    if guardrail_note:
        v2_reasons.append(guardrail_note)

    result = dict(v1_result)  # every original V1 key/value preserved as-is
    result.update({
        "v1_score": v1_result["attention_score"],
        "v1_decision": v1_result["decision"],
        "v2_adjustment": allowed_adjustment,
        "v2_reasons": v2_reasons,
        "final_score": final_score,
        "final_decision": final_decision,
        # The fields the rest of the app (dashboard/inbox/digest/analytics) reads
        # are attention_score/decision — point them at the FINAL personalized
        # values so every existing V1 page automatically reflects V2 without
        # any of those templates needing to change.
        "attention_score": final_score,
        "decision": final_decision,
    })
    return result