"""
VYRA V3 — Predictive Attention

Identifies messages that may become important LATER even if V1/V2's
current decision is digest/mute. Deterministic and rule-based — this is
explicitly an MVP heuristic (per project brief), not a trained model.
No accuracy/confidence numbers are fabricated.

Reuses agent.context.analyze_context() (pure, side-effect-free) to get
deadline_strength, since V1's analyzer.py does not expose that field in
its returned dict and analyzer.py itself must not be modified.

Public interface: `compute_prediction(text, v2_result) -> dict`
"""

from agent.context import analyze_context


def compute_prediction(text: str, v2_result: dict) -> dict:
    context = analyze_context(text)

    decision = v2_result.get("final_decision", v2_result.get("decision"))
    action_required = v2_result.get("action_required", False)
    category = v2_result.get("category")

    # Weak deadlines ("today", "tonight") are common in low-priority
    # promotional/spam content — V1 itself treats these as a much weaker
    # signal than a strong deadline for exactly this reason. Predictive
    # attention must not re-introduce that false-positive class: a bare
    # weak deadline only counts as predictive if the message isn't in a
    # category V1/V2 already treat as inherently low-priority, or if an
    # action is genuinely required.
    LOW_PRIORITY_CATEGORIES = {"promotion", "shopping", "spam", "social"}

    is_predictive = False
    predicted_importance = None
    reasons = []

    if decision != "notify":
        if context.deadline_strength == "strong":
            is_predictive = True
            predicted_importance = "high"
            reasons.append(f"A specific deadline was detected ({context.deadline}) even though it's not urgent yet.")
        elif context.deadline_strength == "weak" and (action_required or category not in LOW_PRIORITY_CATEGORIES):
            is_predictive = True
            predicted_importance = "medium"
            reasons.append(f"A future time reference was detected ({context.deadline}).")
        elif action_required:
            is_predictive = True
            predicted_importance = "low"
            reasons.append("This message requires an action, even without an explicit deadline.")

    if not is_predictive:
        reasons.append("No future deadline or pending action was detected beyond V1/V2's current assessment.")

    return {
        "is_predictive": is_predictive,
        "predicted_importance": predicted_importance,
        "reasons": reasons,
    }