"""One-time, idempotent demo workspace seeding for production databases.

The repository ships with the curated demo SQLite database at
instance/vyra.db. On a fresh Render/PostgreSQL database, this module copies
that demo workspace into the production database exactly once. It never runs
against SQLite and never overwrites existing production data.
"""

import os
import sqlite3
import logging
from pathlib import Path

from sqlalchemy import text

logger = logging.getLogger("vyra")

DEFAULT_USER_EMAIL = "default@vyra.local"


def _is_postgres(database_url: str) -> bool:
    return database_url.startswith(("postgresql://", "postgresql+psycopg2://", "postgres://"))


def seed_demo_workspace(db, database_url: str) -> None:
    """Copy the bundled curated demo workspace into a fresh PostgreSQL DB.

    The seed is intentionally conservative:
    - PostgreSQL only; local SQLite remains the source/backup.
    - Only the default demo user's workspace is copied.
    - Existing demo messages mean the seed is skipped.
    - Existing user workspaces are never modified.
    - Explicit primary keys are preserved so thread/message relationships stay intact.
    """
    if not _is_postgres(database_url):
        return

    source = Path(__file__).resolve().parents[1] / "instance" / "vyra.db"
    if not source.exists():
        logger.warning("Demo seed skipped: bundled instance/vyra.db was not found at %s", source)
        return

    from models import User, Message, Decision, Feedback, Preference, Thread, Task, Action

    demo_user = User.query.filter_by(email=DEFAULT_USER_EMAIL).first()
    if demo_user is None:
        logger.warning("Demo seed skipped: default demo user does not exist yet")
        return

    existing_messages = Message.query.filter_by(user_id=demo_user.id).count()
    if existing_messages:
        logger.info("Demo seed skipped: demo workspace already contains %d messages", existing_messages)
        return

    conn = sqlite3.connect(str(source))
    conn.row_factory = sqlite3.Row
    try:
        source_user = conn.execute(
            "SELECT id, name, email, created_at FROM users WHERE email = ?",
            (DEFAULT_USER_EMAIL,),
        ).fetchone()
        if source_user is None:
            logger.warning("Demo seed skipped: source demo user was not found")
            return

        message_rows = [dict(r) for r in conn.execute(
            "SELECT id, content, category, created_at FROM messages WHERE user_id = ? ORDER BY id",
            (source_user["id"],),
        ).fetchall()]
        if not message_rows:
            logger.warning("Demo seed skipped: source demo workspace has no messages")
            return

        message_ids = [r["id"] for r in message_rows]
        placeholders = ",".join("?" for _ in message_ids)

        def fetch(sql):
            return [dict(r) for r in conn.execute(sql, message_ids).fetchall()]

        decision_rows = fetch(
            f"SELECT id, message_id, attention_score, decision, urgency, relevance, time_sensitive, deadline, action_required, suspicious, explanation, reasons_json, created_at FROM decisions WHERE message_id IN ({placeholders}) ORDER BY id"
        )
        feedback_rows = fetch(
            f"SELECT id, message_id, original_decision, original_score, feedback, created_at FROM feedback WHERE message_id IN ({placeholders}) ORDER BY id"
        )
        task_rows = fetch(
            f"SELECT id, message_id, title, description, deadline, priority, status, created_at, completed_at FROM tasks WHERE message_id IN ({placeholders}) ORDER BY id"
        )
        action_rows = fetch(
            f"SELECT id, message_id, action_type, suggestion_text, status, created_at, updated_at FROM actions WHERE message_id IN ({placeholders}) ORDER BY id"
        )
        thread_rows = [dict(r) for r in conn.execute(
            "SELECT id, title, category, status, message_ids_json, created_at, updated_at FROM threads ORDER BY id"
        ).fetchall()]
        preference_rows = [dict(r) for r in conn.execute(
            "SELECT id, category, priority_level, updated_at FROM preferences WHERE user_id = ? ORDER BY id",
            (source_user["id"],),
        ).fetchall()]

        # Serialize first-boot seeding across Gunicorn workers. The advisory lock
        # is transaction-scoped and released automatically at commit/rollback.
        db.session.execute(text("SELECT pg_advisory_xact_lock(741208118)"))
        if Message.query.filter_by(user_id=demo_user.id).count():
            db.session.rollback()
            return

        db.session.execute(Message.__table__.insert(), [
            {**r, "user_id": demo_user.id} for r in message_rows
        ])
        if decision_rows:
            db.session.execute(Decision.__table__.insert(), decision_rows)
        if feedback_rows:
            db.session.execute(Feedback.__table__.insert(), feedback_rows)
        if preference_rows:
            db.session.execute(Preference.__table__.insert(), [
            {**r, "user_id": demo_user.id} for r in preference_rows
            ])
        if thread_rows:
            db.session.execute(Thread.__table__.insert(), thread_rows)
        if task_rows:
            db.session.execute(Task.__table__.insert(), task_rows)
        if action_rows:
            db.session.execute(Action.__table__.insert(), action_rows)

        db.session.commit()

        # Explicit IDs were preserved for relationship integrity. Reset
        # PostgreSQL sequences so the next newly-created row gets a fresh ID.
        for table_name in ("users", "messages", "decisions", "feedback", "preferences", "threads", "tasks", "actions"):
            db.session.execute(text(
                "SELECT setval(pg_get_serial_sequence(:table_name, 'id'), "
                "COALESCE((SELECT MAX(id) FROM \"" + table_name + "\"), 1), true)"
            ), {"table_name": table_name})
        db.session.commit()

        logger.info(
            "Seeded VYRA demo workspace: %d messages, %d decisions, %d feedback, %d threads, %d tasks, %d actions",
            len(message_rows), len(decision_rows), len(feedback_rows), len(thread_rows), len(task_rows), len(action_rows),
        )
    except Exception:
        db.session.rollback()
        logger.exception("Demo workspace seed failed; production startup will not silently hide the error")
        raise
    finally:
        conn.close()
