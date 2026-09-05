from agent.decision import make_decision
from config import DECISION_THRESHOLDS


def test_notify_threshold():
    assert make_decision(DECISION_THRESHOLDS["notify"]) == "notify"
    assert make_decision(100) == "notify"


def test_digest_threshold():
    assert make_decision(DECISION_THRESHOLDS["digest"]) == "digest"
    assert make_decision(79) == "digest"


def test_mute_threshold():
    assert make_decision(0) == "mute"
    assert make_decision(DECISION_THRESHOLDS["digest"] - 1) == "mute"


def test_boundary_just_below_notify():
    assert make_decision(DECISION_THRESHOLDS["notify"] - 0.1) == "digest"


def test_suspicious_message_never_fully_muted():
    # A low score that would normally be muted should be bumped to digest
    # if the message is flagged suspicious, so it isn't silently hidden.
    decision = make_decision(10, suspicious=True)
    assert decision != "mute"


def test_suspicious_high_score_still_notifies():
    decision = make_decision(90, suspicious=True)
    assert decision == "notify"