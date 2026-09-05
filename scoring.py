"""
Attention Scoring Engine

Combines the individual factor scores (each normalized to 0-100) into a
single Attention Score (0-100) using the weights defined in config.py.

This module contains NO business logic about what makes a message
important — it purely combines already-computed factor scores. All
weight configuration lives in config.SCORE_WEIGHTS.

Public interface: `calculate_attention_score(factors: ScoreFactors) -> float`
"""

from dataclasses import dataclass

from config import SCORE_WEIGHTS


@dataclass
class ScoreFactors:
    relevance: float           # 0-100
    urgency: float              # 0-100
    time_sensitivity: float     # 0-100
    action_required: float      # 0-100
    sender_importance: float    # 0-100
    safety: float                # 0-100 (higher = safer)


def calculate_attention_score(factors: ScoreFactors) -> float:
    weighted_sum = (
        factors.relevance * SCORE_WEIGHTS["relevance"]
        + factors.urgency * SCORE_WEIGHTS["urgency"]
        + factors.time_sensitivity * SCORE_WEIGHTS["time_sensitivity"]
        + factors.action_required * SCORE_WEIGHTS["action_required"]
        + factors.sender_importance * SCORE_WEIGHTS["sender_importance"]
        + factors.safety * SCORE_WEIGHTS["safety"]
    )
    return round(max(0.0, min(100.0, weighted_sum)), 1)