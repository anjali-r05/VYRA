"""
VYRA V1 — Central Configuration

This is the single source of truth for:
- Flask/environment configuration
- Attention Score weights
- Decision thresholds
- Category priority defaults
- Safety keyword lists

Nothing outside this file should hardcode scoring weights or thresholds.
"""

import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")

    # SQLAlchemy — supports DATABASE_URL (Render/Postgres) or falls back to local SQLite
    _database_url = os.environ.get("DATABASE_URL", "sqlite:///vyra.db")
    # Render provides postgres:// but SQLAlchemy needs postgresql://
    if _database_url.startswith("postgres://"):
        _database_url = _database_url.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URI = _database_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    DEBUG = os.environ.get("FLASK_DEBUG", "false").lower() == "true"


# ------------------------------------------------------------------
# ATTENTION SCORE WEIGHTS
# These must sum to 1.0. This is the ONLY place weights are defined.
# ------------------------------------------------------------------
SCORE_WEIGHTS = {
    "relevance": 0.25,
    "urgency": 0.25,
    "time_sensitivity": 0.20,
    "action_required": 0.15,
    "sender_importance": 0.10,
    "safety": 0.05,
}

assert abs(sum(SCORE_WEIGHTS.values()) - 1.0) < 1e-6, "SCORE_WEIGHTS must sum to 1.0"


# ------------------------------------------------------------------
# DECISION THRESHOLDS (0-100 scale)
# ------------------------------------------------------------------
DECISION_THRESHOLDS = {
    "notify": 80,   # score >= 80  -> NOTIFY
    "digest": 40,   # 40 <= score < 80 -> DIGEST
    # below 40 -> MUTE
}


# ------------------------------------------------------------------
# CATEGORY PRIORITY DEFAULTS (used by relevance engine + preferences)
# ------------------------------------------------------------------
DEFAULT_HIGH_PRIORITY_CATEGORIES = ["career", "academic", "projects", "family"]
DEFAULT_LOW_PRIORITY_CATEGORIES = ["promotion", "shopping", "social"]

ALL_CATEGORIES = [
    "career",
    "academic",
    "projects",
    "family",
    "personal",
    "meeting",
    "promotion",
    "shopping",
    "social",
    "spam",
    "suspicious",
    "reminder",
    "general",
]


# ------------------------------------------------------------------
# SAFETY / SUSPICION KEYWORD SIGNALS
# ------------------------------------------------------------------
SUSPICIOUS_URGENCY_PHRASES = [
    "act now", "immediately or", "urgent action required", "final notice",
    "account will be suspended", "account will be closed", "verify now",
    "click immediately", "expires today", "within 24 hours", "last warning",
    "your account has been compromised", "confirm your identity immediately",
]

SUSPICIOUS_FINANCIAL_PHRASES = [
    "wire transfer", "gift card", "bitcoin", "crypto wallet", "send money",
    "bank details", "otp", "one time password", "cvv", "card number",
    "processing fee", "claim your prize", "you have won", "lottery",
    "tax refund", "unclaimed funds",
]

SUSPICIOUS_ACCOUNT_THREAT_PHRASES = [
    "suspended", "unauthorized login", "unusual activity detected",
    "verify your account", "reset your password immediately",
    "your account has been locked", "confirm your password",
]

SUSPICIOUS_LINK_PATTERNS = [
    "bit.ly", "tinyurl", "click here", "http://", "goo.gl",
]