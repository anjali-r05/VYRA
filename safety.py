"""
Safety Module

Basic suspicion/scam detection for V1. This is intentionally lightweight —
it is ONE factor in the attention system, not a full cybersecurity product.

Public interface: `analyze_safety(text) -> SafetyResult`
"""

from dataclasses import dataclass, field

from config import (
    SUSPICIOUS_URGENCY_PHRASES,
    SUSPICIOUS_FINANCIAL_PHRASES,
    SUSPICIOUS_ACCOUNT_THREAT_PHRASES,
    SUSPICIOUS_LINK_PATTERNS,
)


@dataclass
class SafetyResult:
    suspicious: bool
    safety_score: float  # 0-100, higher = safer
    flags: list = field(default_factory=list)


def analyze_safety(text: str) -> SafetyResult:
    text_lower = text.lower()
    flags = []

    if any(p in text_lower for p in SUSPICIOUS_URGENCY_PHRASES):
        flags.append("Suspicious urgency language detected")

    if any(p in text_lower for p in SUSPICIOUS_FINANCIAL_PHRASES):
        flags.append("Unusual financial request detected")

    if any(p in text_lower for p in SUSPICIOUS_ACCOUNT_THREAT_PHRASES):
        flags.append("Account-threat language detected")

    if any(p in text_lower for p in SUSPICIOUS_LINK_PATTERNS):
        flags.append("Suspicious or shortened link detected")

    suspicious = len(flags) > 0
    # Each flag reduces the safety score; floor at 10 to avoid a hard 0.
    safety_score = max(10.0, 100.0 - (len(flags) * 30))

    return SafetyResult(suspicious=suspicious, safety_score=safety_score, flags=flags)