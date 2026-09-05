"""
Explanation Engine

Generates a transparent, human-readable explanation from the ACTUAL
analysis results. Nothing here is hardcoded per-example — every sentence
is built conditionally from the real detected signals.

Public interface: `generate_explanation(analysis) -> (reasons: list[str], explanation: str)`
"""


def generate_explanation(analysis: dict) -> tuple[list, str]:
    reasons = []

    category = analysis["category"]
    intent = analysis["intent"]
    urgency_level = analysis["urgency_level"]
    deadline = analysis.get("deadline")
    action_required = analysis["action_required"]
    time_sensitive = analysis["time_sensitive"]
    suspicious = analysis["suspicious"]
    safety_flags = analysis.get("safety_flags", [])
    relevance = analysis["relevance"]
    decision = analysis["decision"]

    if intent == "interview":
        reasons.append("Interview detected")
    if deadline:
        reasons.append(f"Deadline detected ({deadline})")
    if action_required:
        reasons.append("Action required")
    if time_sensitive:
        reasons.append("Time-sensitive content")
    if urgency_level == "high":
        reasons.append("High urgency language detected")
    if relevance >= 75:
        reasons.append("High personal relevance")
    elif relevance <= 25:
        reasons.append("Low personal relevance")
    if category in ("promotion", "shopping", "social", "spam"):
        reasons.append(f"Classified as {category} content")
    if suspicious:
        reasons.extend(safety_flags)

    if not reasons:
        reasons.append("No strong urgency, deadline, or action signals detected")

    # Build a natural-language explanation from the same signals
    if decision == "notify":
        lead = "This message deserves immediate attention because"
        clauses = []
        if intent == "interview":
            clauses.append("it contains an interview")
        if deadline:
            clauses.append(f"a deadline was detected ({deadline})")
        if action_required:
            clauses.append("it requires action from you")
        if time_sensitive and not deadline:
            clauses.append("it is time-sensitive")
        if not clauses:
            clauses.append("it scored highly on relevance and urgency")
        explanation = f"{lead} {', '.join(clauses)}."

    elif decision == "digest":
        if suspicious:
            explanation = (
                "This message has been placed in your digest with a safety "
                "warning because it shows signs of suspicious content, so it "
                "wasn't treated as fully trustworthy."
            )
        else:
            explanation = (
                "This is a relevant, worth-knowing message, but no immediate "
                "interruption is required, so it has been added to your digest."
            )

    else:  # mute
        explanation = (
            "This appears to be low-priority content with no detected "
            "deadline, meaningful personal relevance, or required action."
        )

    return reasons, explanation