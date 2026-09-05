"""
VYRA V3 — Orchestrator

Pipeline:

    MESSAGE
       |
       v
    analyzer_v2.analyze_message_personalized()   <- V1 + V2, completely unchanged
       |
       v
    _extend_suspicion_detection()                  <- V3 safety extension, see below
       |
       v
    adaptive_attention.compute_adaptive_adjustment()   <- bounded, safety-aware
       |
       v
    predictive_attention.compute_prediction()           <- informational, does not change the score
       |
       v
    task_extractor.extract_tasks()                        <- informational, does not change the score
       |
       v
    FINAL V3 score/decision + prediction + tasks

Public interface: `analyze_message_v3(text, preferences=None) -> dict`

Like analyzer_v2.py before it, this returns a superset of the wrapped
layer's dict — every V1 and V2 key is preserved, plus new "v3_*",
"prediction", and "tasks" keys. attention_score/decision are updated to
point at the FINAL V3 values so the rest of the app (dashboard, inbox,
digest, analytics) automatically reflects V3 without needing changes.
"""

import re

from agent.analyzer_v2 import analyze_message_personalized
from agent.decision import make_decision
from agent.adaptive_attention import compute_adaptive_adjustment
from agent.predictive_attention import compute_prediction
from agent.task_extractor import extract_tasks

# V3 SAFETY EXTENSION — NOT a modification of agent/safety.py or config.py.
#
# V1's own safety check (agent/safety.py) does exact-substring matching
# against a fixed phrase list in config.py. During Phase 3G hardening
# testing, a realistic phishing message ("Your password will expire
# today. Click this shortened link to prevent account closure.") was
# found to slip past V1's check purely on wording — none of its phrases
# are an exact substring of V1's list ("expires today" != "will expire
# today"; "click here"/specific link domains != "this shortened link";
# "account will be closed" != "prevent account closure") even though the
# message is unambiguously the same phishing/account-threat pattern V1's
# own categories are designed to catch.
#
# Rather than edit the protected agent/safety.py or config.py, this is an
# ADDITIVE, OR-ONLY supplementary check: it can only ever turn
# `suspicious` from False to True, NEVER the reverse. V1's own
# suspicious=True verdict is always still authoritative and is never
# weakened or overridden by this function. This follows the same
# "extend without modifying" pattern already used by
# agent/task_extractor.py's TASK_SUPPLEMENTARY_STRONG_PATTERNS.
_SUPPLEMENTARY_SUSPICIOUS_PATTERNS = [
    r"\bwill expire\b.{0,20}\btoday\b",
    r"\bshortened link\b",
    r"\bprevent account closure\b",
    r"\baccount closure\b",
]


def _extend_suspicion_detection(text: str, v2_result: dict) -> dict:
    if v2_result["suspicious"]:
        return v2_result  # already suspicious via V1 — nothing to add

    text_lower = text.lower()
    matched = [p for p in _SUPPLEMENTARY_SUSPICIOUS_PATTERNS if re.search(p, text_lower)]
    if not matched:
        return v2_result

    result = dict(v2_result)
    result["suspicious"] = True
    result["safety_flags"] = list(v2_result.get("safety_flags", [])) + [
        "V3 supplementary check: phishing/account-threat pattern detected beyond V1's exact-phrase list."
    ]
    return result


def analyze_message_v3(text: str, preferences: dict | None = None, user_id: int | None = None) -> dict:
    # 1. Run V1+V2 exactly as before. This call, and everything inside it, is untouched.
    v2_result = analyze_message_personalized(text, preferences=preferences, user_id=user_id)

    # 1b. V3 additive safety extension — see module docstring above. Can
    # only add a suspicious flag, never remove V1's own verdict. If it
    # flips suspicious on, final_decision must be recomputed through V1's
    # own make_decision() so the "suspicious is never fully muted" rule
    # (agent/decision.py) still applies consistently to the newly-flagged
    # message, exactly as it would if V1 itself had caught it.
    was_already_suspicious = v2_result["suspicious"]
    v2_result = _extend_suspicion_detection(text, v2_result)
    if v2_result["suspicious"] and not was_already_suspicious:
        v2_result["final_decision"] = make_decision(v2_result["final_score"], suspicious=True)

    # 2. Adaptive Attention — bounded, safety-aware contextual adjustment.
    adaptive_adjustment, adaptive_reasons = compute_adaptive_adjustment(v2_result)

    v3_score = max(0.0, min(100.0, round(v2_result["final_score"] + adaptive_adjustment, 1)))
    v3_decision = make_decision(v3_score, suspicious=v2_result["suspicious"])

    # 3. Predictive Attention — informational only, never changes the score/decision.
    prediction = compute_prediction(text, v2_result)

    # 4. Task Extraction — informational only, persisted separately by app.py
    #    once the parent Message row has a real id.
    tasks = extract_tasks(text)

    result = dict(v2_result)  # every V1 + V2 key/value preserved as-is
    result.update({
        "v3_adjustment": adaptive_adjustment,
        "v3_reasons": adaptive_reasons,
        "v3_score": v3_score,
        "v3_decision": v3_decision,
        "prediction": prediction,
        "tasks": tasks,
        # Point the fields the rest of the app reads at the FINAL V3 values.
        "attention_score": v3_score,
        "decision": v3_decision,
    })
    return result