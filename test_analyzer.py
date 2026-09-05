import pytest
from agent.analyzer import analyze_message


def test_high_urgency_interview():
    result = analyze_message(
        "Hi, your technical interview is scheduled for tomorrow at 10 AM. "
        "Please confirm your attendance and bring your resume."
    )
    assert result["category"] == "career"
    assert result["action_required"] is True
    assert result["time_sensitive"] is True
    assert result["attention_score"] >= 80
    assert result["decision"] == "notify"


def test_low_value_promotion():
    result = analyze_message("FLASH SALE! Buy shoes today and get 50% OFF!!!")
    assert result["category"] == "promotion"
    assert result["attention_score"] < 40
    assert result["decision"] == "mute"


def test_academic_deadline():
    result = analyze_message("Please submit your assignment by Friday. Late submissions will not be accepted.")
    assert result["category"] == "academic"
    assert result["action_required"] is True


def test_meeting_message():
    result = analyze_message("Sprint standup meeting today at 10 AM, please join on Zoom.")
    assert result["category"] in ("meeting", "projects")
    assert result["time_sensitive"] is True


def test_family_message():
    result = analyze_message("Mom: Dinner tonight at 8, please come home on time.")
    assert result["category"] == "family"
    assert result["relevance"] >= 60


def test_suspicious_message_detected():
    result = analyze_message(
        "URGENT: Your account has been suspended. Click here immediately "
        "to verify your identity or lose access."
    )
    assert result["suspicious"] is True
    assert result["decision"] != "mute"  # suspicious messages should never be silently muted


def test_empty_input_raises():
    with pytest.raises(ValueError):
        analyze_message("")


def test_whitespace_only_input_raises():
    with pytest.raises(ValueError):
        analyze_message("    ")


def test_very_long_input_does_not_crash():
    long_text = "This is a normal reminder message. " * 200
    result = analyze_message(long_text)
    assert 0 <= result["attention_score"] <= 100


def test_preferences_affect_relevance():
    text = "Flash sale on shoes today!"
    base = analyze_message(text)
    boosted = analyze_message(text, preferences={"promotion": "high"})
    assert boosted["relevance"] > base["relevance"]