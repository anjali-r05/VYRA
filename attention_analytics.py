"""
VYRA V3 — Attention Analytics

Read-only aggregation over the real Task/Thread/Action tables (Phase 3C/3D)
plus V1/V2 Decision/Feedback data already used by the existing Analytics
page. Every number here is a COUNT() or GROUP BY over rows that already
exist — nothing is fabricated, nothing is randomly generated, and nothing
here writes to the database.

HONESTY NOTE on "predictive attention items": Predictions are deliberately
NOT persisted anywhere (see agent/predictive_attention.py and the Phase 3
architecture decision to keep them ephemeral, recomputed on demand). That
means there is no historical "total predictive items ever detected" to
report truthfully. This module instead reports a live count over a
bounded recent window of messages — the same technique agent/proactive_brief.py
already uses for its "Upcoming" bucket — and labels it explicitly as a
recent-window figure, not an all-time total, so the UI never implies more
precision than the data actually supports.

Public interface: `compute_attention_analytics() -> dict`
"""

from services.database import db
from agent.predictive_attention import compute_prediction

PREDICTIVE_RECENT_WINDOW = 30


def compute_attention_analytics(user_id: int | None = None) -> dict:
    from models import Task, Thread, Action, Decision, Message

    task_query = db.session.query(Task).join(Message, Message.id == Task.message_id)
    action_query = db.session.query(Action).join(Message, Message.id == Action.message_id)
    decision_query = db.session.query(Decision).join(Message, Message.id == Decision.message_id)
    if user_id is not None:
        task_query = task_query.filter(Message.user_id == user_id)
        action_query = action_query.filter(Message.user_id == user_id)
        decision_query = decision_query.filter(Message.user_id == user_id)

    tasks_total = task_query.count()
    tasks_pending = task_query.filter(Task.status == "pending").count()
    tasks_completed = task_query.filter(Task.status == "completed").count()
    tasks_with_deadline = task_query.filter(Task.deadline.isnot(None)).count()

    if user_id is None:
        threads_total = Thread.query.count()
    else:
        owned_ids = {m.id for m in Message.query.filter_by(user_id=user_id).all()}
        threads_total = sum(1 for t in Thread.query.all() if any(mid in owned_ids for mid in t.message_ids))

    actions_by_status = dict(action_query.with_entities(Action.action_type, db.func.count(Action.id)).group_by(Action.action_type).all())
    action_status_counts = dict(action_query.with_entities(Action.status, db.func.count(Action.id)).group_by(Action.status).all())
    actions_suggested_total = action_query.count()
    actions_approved = action_status_counts.get("approved", 0)
    actions_edited = action_status_counts.get("edited", 0)
    actions_dismissed = action_status_counts.get("dismissed", 0)
    actions_executed = action_status_counts.get("executed", 0)

    suspicious_count = decision_query.filter(Decision.suspicious.is_(True)).count()

    recent_query = (db.session.query(Message, Decision)
                    .join(Decision, Decision.message_id == Message.id)
                    .filter(Decision.decision != "notify")
                    .filter(Decision.suspicious.is_(False)))
    if user_id is not None:
        recent_query = recent_query.filter(Message.user_id == user_id)
    recent_non_notify = recent_query.order_by(Message.created_at.desc()).limit(PREDICTIVE_RECENT_WINDOW).all()

    predictive_count_recent = 0
    for message, decision in recent_non_notify:
        pseudo_v2_result = {
            "final_decision": decision.decision,
            "action_required": decision.action_required,
            "category": message.category,
        }
        prediction = compute_prediction(message.content, pseudo_v2_result)
        if prediction["is_predictive"]:
            predictive_count_recent += 1

    return {
        "tasks_total": tasks_total,
        "tasks_pending": tasks_pending,
        "tasks_completed": tasks_completed,
        "tasks_with_deadline": tasks_with_deadline,
        "threads_total": threads_total,
        "actions_by_type": actions_by_status,
        "actions_total": actions_suggested_total,
        "actions_approved": actions_approved,
        "actions_edited": actions_edited,
        "actions_dismissed": actions_dismissed,
        "actions_executed": actions_executed,
        "suspicious_count": suspicious_count,
        "predictive_count_recent": predictive_count_recent,
        "predictive_window_size": min(PREDICTIVE_RECENT_WINDOW, len(recent_non_notify)),
    }
