from agent.scoring import calculate_attention_score, ScoreFactors
from config import SCORE_WEIGHTS


def test_weights_sum_to_one():
    assert abs(sum(SCORE_WEIGHTS.values()) - 1.0) < 1e-6


def test_max_factors_yield_max_score():
    factors = ScoreFactors(
        relevance=100, urgency=100, time_sensitivity=100,
        action_required=100, sender_importance=100, safety=100,
    )
    assert calculate_attention_score(factors) == 100.0


def test_min_factors_yield_min_score():
    factors = ScoreFactors(
        relevance=0, urgency=0, time_sensitivity=0,
        action_required=0, sender_importance=0, safety=0,
    )
    assert calculate_attention_score(factors) == 0.0


def test_score_is_within_bounds():
    factors = ScoreFactors(
        relevance=70, urgency=40, time_sensitivity=60,
        action_required=20, sender_importance=50, safety=90,
    )
    score = calculate_attention_score(factors)
    assert 0 <= score <= 100


def test_relevance_and_urgency_dominate_weighting():
    # relevance + urgency = 50% of the weight, so maxing just those two
    # should meaningfully outweigh maxing the smaller-weighted factors
    high_core = ScoreFactors(
        relevance=100, urgency=100, time_sensitivity=0,
        action_required=0, sender_importance=0, safety=0,
    )
    high_minor = ScoreFactors(
        relevance=0, urgency=0, time_sensitivity=0,
        action_required=100, sender_importance=100, safety=100,
    )
    assert calculate_attention_score(high_core) > calculate_attention_score(high_minor)