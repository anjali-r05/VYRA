from datetime import datetime, timezone
from services.database import db


class Action(db.Model):
    """
    An Action is a suggestion VYRA makes (e.g. "Reply", "Add Task",
    "Confirm") — never something it does on its own. The `status` column
    is the enforcement point for the review-first workflow:

        suggested -> approved/edited/dismissed -> executed

    Nothing in this codebase transitions a row straight from "suggested"
    to "executed" — see app.py's /api/actions/<id>/... routes, each of
    which requires the row to already be "approved" before it can be
    marked "executed". There is deliberately no automatic-execution path.
    """
    __tablename__ = "actions"

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey("messages.id"), nullable=False)

    action_type = db.Column(db.String(30), nullable=False)  # reply | add_task | add_reminder | confirm | review | mark_important | dismiss
    suggestion_text = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="suggested")
    # suggested | approved | edited | dismissed | executed

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                            onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<Action {self.id} {self.action_type} {self.status}>"