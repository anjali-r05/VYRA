import json
from datetime import datetime, timezone
from services.database import db


class Thread(db.Model):
    """
    A Thread groups related Messages together (e.g. "Internship - TechCorp").

    DESIGN NOTE: deliberately no `thread_id` column on Message. Adding a
    column to an existing table isn't reachable through this project's
    db.create_all()-only initialization (it creates missing TABLES, not
    missing COLUMNS on existing tables), so on an already-deployed V1/V2
    database a new Message column would silently never appear without a
    manual ALTER TABLE this project has no tooling for. Instead, Thread
    owns a JSON list of member message ids — the same
    store-structured-data-as-JSON-on-a-row convention already used by
    Decision.reasons_json. This makes Threads fully additive: a brand new
    table, zero changes to any existing table, works identically on a
    fresh install or an existing V1/V2 database.
    """
    __tablename__ = "threads"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(50), nullable=True)
    status = db.Column(db.String(30), nullable=True)  # e.g. "active", "resolved"
    message_ids_json = db.Column(db.Text, nullable=False, default="[]")

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                            onupdate=lambda: datetime.now(timezone.utc))

    @property
    def message_ids(self):
        try:
            return json.loads(self.message_ids_json)
        except (TypeError, ValueError):
            return []

    @message_ids.setter
    def message_ids(self, value):
        self.message_ids_json = json.dumps(value or [])

    def add_message(self, message_id: int):
        ids = self.message_ids
        if message_id not in ids:
            ids.append(message_id)
            self.message_ids = ids

    def __repr__(self):
        return f"<Thread {self.id} '{self.title}' ({len(self.message_ids)} messages)>"