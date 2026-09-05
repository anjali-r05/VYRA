"""
VYRA Analyzer

The single orchestration point for the full V1 pipeline:

MESSAGE -> CONTEXT -> RELEVANCE -> URGENCY -> ACTION -> SAFETY
        -> SCORING -> DECISION -> EXPLANATION

Public interface: `analyze_message(text, preferences=None) -> dict`

This is the ONLY module that should import from all other agent
submodules — everything else stays decoupled so individual components
(e.g. relevance.py) can be swapped for ML-based versions in V2 without
touching this orchestration logic.
"""

from agent.context import analyze_context
from agent.relevance import analyze_relevance
from agent.urgency import analyze_urgency
from agent.safety import analyze_safety
from agent.scoring import calculate_attention_score, ScoreFactors
from agent.decision import make_decision
from agent.explanation import generate_explanation


ACTION_PHRASES = [
    "please confirm", "please submit", "please respond", "please reply",
    "please complete", "please sign", "please pay", "rsvp", "action required",
    "kindly reply", "confirm your attendance", "please bring", "please attend",
]


def _detect_action_required(text_lower: str, intent: str) -> bool:
    if intent in ("required_action", "interview"):
        return True
    return any(phrase in text_lower for phrase in ACTION_PHRASES)


def analyze_message(text: str, preferences: dict | None = None) -> dict:
    if not text or not text.strip():
        raise ValueError("Message content cannot be empty")

    text_lower = text.lower()

    # 1. CONTEXT — category, intent, deadline
    context = analyze_context(text)

    # 2. RELEVANCE
    relevance = analyze_relevance(text, context.category, preferences)

    # 3. URGENCY
    urgency = analyze_urgency(text, context.time_sensitive, context.deadline, context.deadline_strength)

    # 4. ACTION REQUIRED
    action_required = _detect_action_required(text_lower, context.intent)

    # 5. SAFETY
    safety = analyze_safety(text)

    # 6. SCORING — combine normalized factors
    if context.deadline_strength == "strong":
        time_sensitivity_score = 100.0
    elif context.deadline_strength == "weak":
        time_sensitivity_score = 45.0
    else:
        time_sensitivity_score = 20.0

    factors = ScoreFactors(
        relevance=relevance,
        urgency=urgency.score,
        time_sensitivity=time_sensitivity_score,
        action_required=100.0 if action_required else 10.0,
        sender_importance=60.0,  # V1 baseline; no sender identity system yet
        safety=safety.safety_score,
    )
    attention_score = calculate_attention_score(factors)

    # 7. DECISION
    decision = make_decision(attention_score, suspicious=safety.suspicious)

    # 8. EXPLANATION
    analysis_summary = {
        "category": context.category,
        "intent": context.intent,
        "urgency_level": urgency.level,
        "deadline": context.deadline,
        "action_required": action_required,
        "time_sensitive": context.time_sensitive,
        "suspicious": safety.suspicious,
        "safety_flags": safety.flags,
        "relevance": relevance,
        "decision": decision,
    }
    reasons, explanation = generate_explanation(analysis_summary)

    return {
        "attention_score": attention_score,
        "decision": decision,
        "category": context.category,
        "intent": context.intent,
        "urgency": urgency.level,
        "relevance": relevance,
        "time_sensitive": context.time_sensitive,
        "deadline": context.deadline,
        "action_required": action_required,
        "suspicious": safety.suspicious,
        "safety_flags": safety.flags,
        "reasons": reasons,
        "explanation": explanation,
    }