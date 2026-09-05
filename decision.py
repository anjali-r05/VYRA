import json
from datetime import datetime, timezone
from services.database import db


class Decision(db.Model):
    __tablename__ = "decisions"

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey("messages.id"), nullable=False)

    attention_score = db.Column(db.Float, nullable=False)
    decision = db.Column(db.String(20), nullable=False)  # notify | digest | mute

    urgency = db.Column(db.String(20), nullable=False)       # low | medium | high
    relevance = db.Column(db.Float, nullable=False)           # 0-100
    time_sensitive = db.Column(db.Boolean, default=False)
    deadline = db.Column(db.String(120), nullable=True)
    action_required = db.Column(db.Boolean, default=False)
    suspicious = db.Column(db.Boolean, default=False)

    explanation = db.Column(db.Text, nullable=False)
    reasons_json = db.Column(db.Text, nullable=False, default="[]")

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    @property
    def reasons(self):
        try:
            return json.loads(self.reasons_json)
        except (TypeError, ValueError):
            return []

    @reasons.setter
    def reasons(self, value):
        self.reasons_json = json.dumps(value or [])

    def __repr__(self):
        return f"<Decision {self.id} {self.decision} {self.attention_score}>"