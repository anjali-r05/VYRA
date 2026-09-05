from datetime import datetime, timezone
from services.database import db


class Preference(db.Model):
    __tablename__ = "preferences"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    category = db.Column(db.String(50), nullable=False)
    priority_level = db.Column(db.String(20), nullable=False, default="normal")  # high | normal | low

    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                            onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("user_id", "category", name="uq_user_category"),
    )

    def __repr__(self):
        return f"<Preference {self.category}:{self.priority_level}>"