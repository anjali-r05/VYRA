from datetime import datetime, timezone
from services.database import db


class Task(db.Model):
    __tablename__ = "tasks"

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey("messages.id"), nullable=False)

    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, nullable=True)
    deadline = db.Column(db.String(120), nullable=True)
    priority = db.Column(db.String(20), nullable=False, default="low")  # high | medium | low
    status = db.Column(db.String(20), nullable=False, default="pending")  # pending | completed

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f"<Task {self.id} '{self.title}' {self.status}>"