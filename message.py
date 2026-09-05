from datetime import datetime, timezone
from services.database import db


class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    content = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(50), nullable=False, default="general")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    decision = db.relationship("Decision", backref="message", uselist=False, cascade="all, delete-orphan")
    feedback_entries = db.relationship("Feedback", backref="message", lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Message {self.id} {self.category}>"

    def to_preview(self, length: int = 100) -> str:
        text = self.content.strip().replace("\n", " ")
        return text if len(text) <= length else text[:length].rstrip() + "..."