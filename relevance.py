"""
Relevance Module

Determines how relevant a message is to the user (0-100), influenced by:
- category (career/academic/family score higher by default)
- user preferences (high/low priority categories)
- personal pronoun / direct address signals

Public interface: `analyze_relevance(text, category, preferences=None) -> float`

A future version could replace this with an ML personalization model
(V2: "Is this important TO THIS USER?") without changing the interface.
"""

from typing import Optional

# Baseline relevance by category (0-100), used before preference adjustment.
CATEGORY_BASE_RELEVANCE = {
    "career": 85,
    "academic": 80,
    "projects": 75,
    "family": 80,
    "meeting": 65,
    "reminder": 55,
    "personal": 60,
    "general": 45,
    "social": 30,
    "shopping": 25,
    "promotion": 15,
    "spam": 5,
    "suspicious": 20,
}

DIRECT_ADDRESS_SIGNALS = ["you ", "your ", "please", "kindly", "dear"]


def analyze_relevance(text: str, category: str, preferences: Optional[dict] = None) -> float:
    """
    preferences: optional dict like {"career": "high", "promotion": "low"}
    mapping category -> priority_level ("high" | "normal" | "low")
    """
    text_lower = text.lower()
    base = CATEGORY_BASE_RELEVANCE.get(category, 45)

    # Direct address boosts relevance slightly (message is clearly addressed to the user)
    direct_hits = sum(1 for signal in DIRECT_ADDRESS_SIGNALS if signal in text_lower)
    base += min(direct_hits * 2, 6)

    # Apply user preference adjustment
    if preferences:
        level = preferences.get(category)
        if level == "high":
            base += 15
        elif level == "low":
            base -= 20

    return max(0.0, min(100.0, base))