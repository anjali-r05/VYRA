import os
import tempfile

import pytest


@pytest.fixture
def client():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

    from app import create_app
    app = create_app()
    app.config["TESTING"] = True

    with app.test_client() as client:
        yield client

    os.close(db_fd)
    os.unlink(db_path)


def test_analyze_endpoint_returns_result(client):
    resp = client.post("/api/analyze", json={"message": "Your interview is tomorrow at 10 AM."})
    assert resp.status_code == 201
    data = resp.get_json()
    assert "attention_score" in data
    assert "decision" in data
    assert "message_id" in data


def test_analyze_endpoint_rejects_empty(client):
    resp = client.post("/api/analyze", json={"message": ""})
    assert resp.status_code == 400


def test_analyze_endpoint_rejects_missing_field(client):
    resp = client.post("/api/analyze", json={})
    assert resp.status_code == 400


def test_feedback_endpoint_persists(client):
    analyze_resp = client.post("/api/analyze", json={"message": "Meeting today at 3 PM, please confirm."})
    message_id = analyze_resp.get_json()["message_id"]

    fb_resp = client.post("/api/feedback", json={"message_id": message_id, "feedback": "correct"})
    assert fb_resp.status_code == 201
    assert fb_resp.get_json()["status"] == "saved"


def test_feedback_endpoint_rejects_invalid_value(client):
    analyze_resp = client.post("/api/analyze", json={"message": "Hello there"})
    message_id = analyze_resp.get_json()["message_id"]

    resp = client.post("/api/feedback", json={"message_id": message_id, "feedback": "maybe"})
    assert resp.status_code == 400


def test_feedback_endpoint_rejects_unknown_message(client):
    resp = client.post("/api/feedback", json={"message_id": 999999, "feedback": "correct"})
    assert resp.status_code == 404


def test_dashboard_page_loads(client):
    resp = client.get("/dashboard")
    assert resp.status_code == 200


def test_analyze_page_loads(client):
    resp = client.get("/analyze")
    assert resp.status_code == 200


def test_inbox_page_loads(client):
    resp = client.get("/inbox")
    assert resp.status_code == 200


def test_messages_api_reflects_persisted_data(client):
    client.post("/api/analyze", json={"message": "Project deadline tomorrow, please submit your report."})
    resp = client.get("/api/messages")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) >= 1

def test_action_reopen_and_user_isolation(client):
    r = client.post("/api/analyze", json={"message": "Please reply to the interview team today?"})
    assert r.status_code == 201
    action_id = r.get_json()["action_ids"][0]

    assert client.post(f"/api/actions/{action_id}/approve").status_code == 200
    assert client.post(f"/api/actions/{action_id}/reopen").get_json()["status"] == "suggested"
    assert client.post(f"/api/actions/{action_id}/dismiss").get_json()["status"] == "dismissed"
    assert client.post(f"/api/actions/{action_id}/reopen").get_json()["status"] == "suggested"

    signup = client.post("/signup", data={"name": "Second User", "email": "second@example.com", "password": "password123"}, follow_redirects=True)
    assert signup.status_code == 200
    isolated = client.get("/api/actions").get_json()
    assert all(row["id"] != action_id for row in isolated)

    client.get("/logout")
    demo_actions = client.get("/api/actions").get_json()
    assert any(row["id"] == action_id for row in demo_actions)
