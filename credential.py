from datetime import datetime, timezone
from services.database import db


class UserCredential(db.Model):
    """Authentication credentials kept separate so existing User rows remain migration-safe."""
    __tablename__ = "user_credentials"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship("User", backref=db.backref("credential", uselist=False, cascade="all, delete-orphan"))
