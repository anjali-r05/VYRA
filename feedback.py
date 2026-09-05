from datetime import datetime, timezone
from services.database import db


class Feedback(db.Model):
    __tablename__ = "feedback"

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey("messages.id"), nullable=False)

    original_decision = db.Column(db.String(20), nullable=False)
    original_score = db.Column(db.Float, nullable=False)
    feedback = db.Column(db.String(20), nullable=False)  # correct | wrong

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<Feedback {self.id} {self.feedback}>"