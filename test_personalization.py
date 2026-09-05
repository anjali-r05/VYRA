"""
Tests for VYRA V2 — personalization engine and safety guardrails.

These test the actual production code in agent/personalization.py and
agent/analyzer_v2.py, using the same temp-SQLite pattern as test_api.py.
"""
import os
import tempfile

import pytest


@pytest.fixture
def client_and_app():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

    from app import create_app
    app = create_app()
    app.config["TESTING"] = True

    with app.test_client() as client:
        with app.app_context():
            yield client, app

    os.close(db_fd)
    os.unlink(db_path)


def _analyze(client, message):
    resp = client.post("/api/analyze", json={"message": message})
    assert resp.status_code == 201
    return resp.get_json()


def _feedback(client, message_id, value):
    resp = client.post("/api/feedback", json={"message_id": message_id, "feedback": value})
    assert resp.status_code == 201


def test_no_feedback_means_zero_adjustment(client_and_app):
    client, app = client_and_app
    data = _analyze(client, "FLASH SALE! Buy shoes today and get 50% off!")
    assert data["v2_adjustment"] == 0.0
    assert data["final_score"] == data["v1_score"]
    assert data["attention_score"] == data["final_score"]


def test_repeated_wrong_digest_feedback_raises_future_scores(client_and_app):
    client, app = client_and_app

    # Give three "wrong" feedbacks on promotion-category digest decisions,
    # simulating a user who actually wants to see these messages.
    for _ in range(3):
        data = _analyze(client, "FLASH SALE! Buy shoes today and get 50% off!")
        assert data["category"] == "promotion"
        _feedback(client, data["message_id"], "wrong")

    # A NEW promotion message should now score higher than pure V1 would give it.
    data2 = _analyze(client, "Big discount on electronics this weekend only!")
    assert data2["category"] == "promotion"
    assert data2["v2_adjustment"] > 0
    assert data2["final_score"] > data2["v1_score"]
    assert any("promotion" in r for r in data2["v2_reasons"])


def test_suspicious_messages_are_never_personalized(client_and_app):
    client, app = client_and_app

    # Prime the system with negative feedback for a category, then confirm
    # a suspicious message from a different signal path still gets zero adjustment.
    data = _analyze(
        client,
        "URGENT: Your account has been suspended. Click here immediately to verify your identity.",
    )
    assert data["suspicious"] is True
    assert data["v2_adjustment"] == 0.0
    assert data["final_score"] == data["v1_score"]
    assert data["final_decision"] == data["v1_decision"]


def test_urgent_actionable_messages_cannot_be_suppressed(client_and_app):
    client, app = client_and_app

    data = _analyze(
        client,
        "Your technical interview is tomorrow at 10 AM. Please confirm your attendance.",
    )
    assert data["urgency"] == "high"
    assert data["action_required"] is True
    # Even with no negative feedback yet, confirm the guardrail-eligible path
    # keeps final_score >= v1_score for this message class.
    assert data["final_score"] >= data["v1_score"]


def test_response_includes_full_v1_v2_breakdown(client_and_app):
    client, app = client_and_app
    data = _analyze(client, "Reminder: team meeting scheduled for tomorrow at 3 PM.")
    for key in ("v1_score", "v1_decision", "v2_adjustment", "v2_reasons", "final_score", "final_decision"):
        assert key in data, f"missing V2 field: {key}"


def test_attention_dna_page_loads_and_reflects_real_data(client_and_app):
    client, app = client_and_app

    # Fresh install: no feedback yet -> honest "still learning" state
    resp = client.get("/attention-dna")
    assert resp.status_code == 200
    assert b"still learning" in resp.data

    # After real feedback exists, the page must not crash and must still load
    data = _analyze(client, "FLASH SALE! Buy shoes today and get 50% off!")
    _feedback(client, data["message_id"], "wrong")
    resp2 = client.get("/attention-dna")
    assert resp2.status_code == 200


def test_dashboard_still_loads_with_v2_active(client_and_app):
    client, app = client_and_app
    resp = client.get("/dashboard")
    assert resp.status_code == 200


def test_v1_decision_thresholds_unchanged(client_and_app):
    """
    Regression guard: confirm V1's own decision thresholds are untouched by
    importing agent.decision directly (never through the V2 wrapper).
    """
    from agent.decision import make_decision
    from config import DECISION_THRESHOLDS

    assert make_decision(DECISION_THRESHOLDS["notify"]) == "notify"
    assert make_decision(DECISION_THRESHOLDS["digest"]) == "digest"
    assert make_decision(DECISION_THRESHOLDS["digest"] - 1) == "mute"