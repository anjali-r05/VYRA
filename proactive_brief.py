"""VYRA — Proactive Brief / Attention Decision Engine.

The brief is intentionally generated fresh from the logged-in user's persisted
workspace. It does not call an external LLM and does not persist a fake daily
summary. Instead it fuses existing VYRA signals into a small, explainable
attention queue:

  messages + decisions + tasks + actions + threads + prediction
             -> signal convergence -> attention horizon -> next move

All user-owned data is filtered by user_id. The returned structure is UI-safe
and contains no fabricated confidence/probability values.
"""

from datetime import datetime, timezone

from services.database import db
from agent.predictive_attention import compute_prediction

CRITICAL_SCAN_LIMIT = 40
PREDICTIVE_SCAN_LIMIT = 20
MAX_ITEMS_PER_BUCKET = 6
FOCUS_QUEUE_LIMIT = 5


def _signal_strength(item):
    """Count independent, observable signals supporting an attention item."""
    return len(item.get("signals", []))


def _signal_label(count):
    if count >= 4:
        return "CONVERGING SIGNALS"
    if count == 3:
        return "STRONG SIGNAL"
    if count == 2:
        return "2 SIGNALS"
    return "SINGLE SIGNAL"


def _decorate(item, decision=None, task=None, actions=None, prediction=None, thread=None):
    signals = []
    if decision is not None:
        if decision.urgency in ("high", "medium"):
            signals.append(f"{decision.urgency.title()} urgency")
        if decision.action_required:
            signals.append("Action required")
        if decision.time_sensitive or decision.deadline:
            signals.append("Time-sensitive")
        if decision.suspicious:
            signals.append("Safety flag")
        if decision.relevance >= 70:
            signals.append("High relevance")
    if task is not None:
        signals.append(f"{task.priority.title()} priority task")
        if task.deadline:
            signals.append("Task deadline")
    if actions:
        signals.append(f"{len(actions)} pending action" + ("s" if len(actions) != 1 else ""))
    if thread is not None:
        signals.append("Thread context")
    if prediction and prediction.get("is_predictive"):
        signals.append("Future-risk signal")

    item["signals"] = list(dict.fromkeys(signals))
    item["signal_count"] = _signal_strength(item)
    item["signal_label"] = _signal_label(item["signal_count"])
    return item



def _build_message_focus(message, decision, task_count=0, action_count=0, prediction=None):
    """Create one independently calculated attention record for a message."""
    score = float(decision.attention_score or 0)
    signals = []
    reasons = list(decision.reasons or [])

    if decision.suspicious:
        signals.append("Safety signal")
    if decision.action_required:
        signals.append("Action required")
    if decision.urgency == "high":
        signals.append("High urgency")
    elif decision.urgency == "medium":
        signals.append("Medium urgency")
    if decision.time_sensitive:
        signals.append("Time-sensitive")
    if decision.deadline:
        signals.append("Deadline detected")
    if decision.relevance >= 70:
        signals.append("High relevance")
    if task_count:
        signals.append(f"{task_count} open task" + ("s" if task_count != 1 else ""))
    if action_count:
        signals.append(f"{action_count} open action" + ("s" if action_count != 1 else ""))
    if prediction and prediction.get("is_predictive"):
        signals.append("Future-looking signal")

    # Every message gets its own horizon. This is intentionally based on the
    # persisted decision, not on the position of the message in a static list.
    if decision.suspicious:
        horizon = "safety"
        label = "CHECK FIRST"
        next_move = "Review the safety signal before acting."
    elif decision.action_required and (decision.urgency == "high" or decision.time_sensitive or decision.deadline or score >= 75):
        horizon = "critical"
        label = "ACT NOW"
        next_move = "Resolve the required action while the signal is active."
    elif decision.decision == "notify" or score >= 60 or decision.action_required:
        horizon = "important"
        label = "HANDLE SOON"
        next_move = "Close this open loop before it becomes more demanding."
    elif decision.time_sensitive or decision.deadline or (prediction and prediction.get("is_predictive")):
        horizon = "upcoming"
        label = "LOOK AHEAD"
        next_move = "Prepare early; VYRA sees a future attention trigger."
    else:
        horizon = "monitor"
        label = "MONITOR"
        next_move = "No immediate action; keep it in the background."

    if not reasons:
        reasons = [f"VYRA assigned an attention score of {round(score)}/100 and classified it as {decision.decision}."]

    return {
        "message_id": message.id,
        "content": message.content,
        "preview": message.to_preview(150),
        "category": message.category,
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "score": round(score, 1),
        "decision": decision.decision,
        "urgency": decision.urgency,
        "relevance": round(float(decision.relevance or 0), 1),
        "deadline": decision.deadline,
        "time_sensitive": bool(decision.time_sensitive),
        "action_required": bool(decision.action_required),
        "suspicious": bool(decision.suspicious),
        "horizon": horizon,
        "horizon_label": label,
        "next_move": next_move,
        "signals": list(dict.fromkeys(signals)),
        "reasons": reasons[:5],
        "explanation": decision.explanation,
        "task_count": task_count,
        "action_count": action_count,
        "signal_count": len(list(dict.fromkeys(signals))),
    }

def generate_proactive_brief(user_id: int | None = None) -> dict:
    from models import Message, Decision, Task, Action, Thread

    critical_items = []
    important_items = []
    upcoming_items = []
    safety_items = []
    seen_thread_ids = set()

    # Only use threads that contain messages belonging to this workspace.
    recent_threads = Thread.query.order_by(Thread.updated_at.desc()).limit(80).all()
    owned_message_ids = None
    if user_id is not None:
        owned_message_ids = {
            m.id for m in Message.query.filter_by(user_id=user_id).all()
        }
    thread_by_message = {}
    for thread in recent_threads:
        for message_id in thread.message_ids:
            if owned_message_ids is None or message_id in owned_message_ids:
                thread_by_message[message_id] = thread

    # Pending actions are a real "open loop" signal, scoped to the user.
    pending_actions_query = Action.query.filter(
        Action.status.in_(["suggested", "approved", "edited"])
    )
    if user_id is not None:
        pending_actions_query = (
            pending_actions_query
            .join(Message, Message.id == Action.message_id)
            .filter(Message.user_id == user_id)
        )
    pending_actions = pending_actions_query.order_by(Action.updated_at.desc()).all()
    pending_actions_by_message = {}
    for action in pending_actions:
        pending_actions_by_message.setdefault(action.message_id, []).append(action)

    # ---- ACT NOW / SAFETY ----
    notify_query = (
        db.session.query(Message, Decision)
        .join(Decision, Decision.message_id == Message.id)
        .filter(Decision.decision == "notify")
    )
    if user_id is not None:
        notify_query = notify_query.filter(Message.user_id == user_id)
    notify_rows = (
        notify_query
        .order_by(Decision.attention_score.desc(), Message.created_at.desc())
        .limit(CRITICAL_SCAN_LIMIT)
        .all()
    )

    for message, decision in notify_rows:
        thread = thread_by_message.get(message.id)
        if thread and thread.id in seen_thread_ids:
            continue

        actions = pending_actions_by_message.get(message.id, [])
        reasons = []
        if decision.suspicious:
            reasons.append("VYRA detected a safety signal; review before acting.")
        if decision.action_required:
            reasons.append("The message explicitly requires an action.")
        if decision.deadline:
            reasons.append(f"Deadline detected: {decision.deadline}.")
        if decision.time_sensitive:
            reasons.append("The message contains a time-sensitive signal.")
        if actions:
            reasons.append(f"{len(actions)} action suggestion(s) are still awaiting review.")
        if not reasons:
            reasons.append("Its current attention score crosses VYRA's notify threshold.")

        item = {
            "title": thread.title if thread else message.to_preview(90),
            "subtitle": message.category,
            "reasons": reasons,
            "score": decision.attention_score,
            "message_id": message.id,
            "thread_id": thread.id if thread else None,
            "action_count": len(actions),
        }
        _decorate(item, decision=decision, actions=actions, thread=thread)

        if decision.suspicious:
            safety_items.append(item)
        elif decision.action_required:
            critical_items.append(item)
        else:
            important_items.append(item)

        if thread:
            seen_thread_ids.add(thread.id)

    # ---- OPEN TASK LOOPS ----
    pending_tasks_query = Task.query.filter(Task.status == "pending")
    if user_id is not None:
        pending_tasks_query = (
            pending_tasks_query
            .join(Message, Message.id == Task.message_id)
            .filter(Message.user_id == user_id)
        )
    pending_tasks = (
        pending_tasks_query
        .order_by(
            db.case((Task.priority == "high", 0), (Task.priority == "medium", 1), else_=2),
            Task.created_at.desc(),
        )
        .limit(CRITICAL_SCAN_LIMIT)
        .all()
    )

    for task in pending_tasks:
        actions = pending_actions_by_message.get(task.message_id, [])
        reasons = [f"Priority is {task.priority}."]
        if task.deadline:
            reasons.append(f"Stored deadline: {task.deadline}.")
        if actions:
            reasons.append(f"The source message has {len(actions)} pending action suggestion(s).")
        item = {
            "title": task.title,
            "subtitle": "Outstanding task",
            "reasons": reasons,
            "score": None,
            "task_id": task.id,
            "message_id": task.message_id,
            "action_count": len(actions),
        }
        _decorate(item, task=task, actions=actions)
        if task.priority == "high":
            important_items.append(item)
        else:
            upcoming_items.append(item)

    # ---- LOOK AHEAD: ephemeral predictive scan ----
    recent_non_notify_query = (
        db.session.query(Message, Decision)
        .join(Decision, Decision.message_id == Message.id)
        .filter(Decision.decision != "notify", Decision.suspicious.is_(False))
    )
    if user_id is not None:
        recent_non_notify_query = recent_non_notify_query.filter(Message.user_id == user_id)
    recent_non_notify = (
        recent_non_notify_query
        .order_by(Message.created_at.desc())
        .limit(PREDICTIVE_SCAN_LIMIT)
        .all()
    )
    for message, decision in recent_non_notify:
        prediction = compute_prediction(
            message.content,
            {
                "final_decision": decision.decision,
                "action_required": decision.action_required,
                "category": message.category,
            },
        )
        if prediction["is_predictive"] and prediction["predicted_importance"] in ("high", "medium"):
            actions = pending_actions_by_message.get(message.id, [])
            item = {
                "title": message.to_preview(90),
                "subtitle": message.category,
                "reasons": prediction["reasons"],
                "score": decision.attention_score,
                "message_id": message.id,
                "action_count": len(actions),
            }
            _decorate(item, decision=decision, actions=actions, prediction=prediction)
            upcoming_items.append(item)

    # Highest signal first inside each horizon.
    sort_key = lambda x: (x.get("signal_count", 0), x.get("score") or 0)
    for bucket in (critical_items, important_items, upcoming_items, safety_items):
        bucket.sort(key=sort_key, reverse=True)

    critical_items = critical_items[:MAX_ITEMS_PER_BUCKET]
    important_items = important_items[:MAX_ITEMS_PER_BUCKET]
    upcoming_items = upcoming_items[:MAX_ITEMS_PER_BUCKET]
    safety_items = safety_items[:MAX_ITEMS_PER_BUCKET]

    # ---- LIVE MESSAGE-BY-MESSAGE DECISION FEED ----
    # This is deliberately separate from the compact focus queue. The queue
    # answers "what deserves me next?" while this feed proves that every
    # newly analysed message is independently re-evaluated from live DB state.
    recent_message_query = (
        db.session.query(Message, Decision)
        .join(Decision, Decision.message_id == Message.id)
    )
    if user_id is not None:
        recent_message_query = recent_message_query.filter(Message.user_id == user_id)
    recent_message_rows = (
        recent_message_query
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(30)
        .all()
    )

    task_counts = {}
    task_query = Task.query.filter(Task.status == "pending")
    if user_id is not None:
        task_query = task_query.join(Message, Message.id == Task.message_id).filter(Message.user_id == user_id)
    for task in task_query.all():
        task_counts[task.message_id] = task_counts.get(task.message_id, 0) + 1

    action_counts = {}
    for action in pending_actions:
        action_counts[action.message_id] = action_counts.get(action.message_id, 0) + 1

    message_focus = []
    for message, decision in recent_message_rows:
        prediction = None
        if decision.decision != "notify" and not decision.suspicious:
            prediction = compute_prediction(
                message.content,
                {
                    "final_decision": decision.decision,
                    "action_required": decision.action_required,
                    "category": message.category,
                },
            )
        message_focus.append(
            _build_message_focus(
                message,
                decision,
                task_count=task_counts.get(message.id, 0),
                action_count=action_counts.get(message.id, 0),
                prediction=prediction,
            )
        )

    # ---- Attention forecast ----
    # This is a transparent workload index, not a psychological or ML score.
    weighted_load = (
        len(safety_items) * 3
        + len(critical_items) * 3
        + len(important_items) * 2
        + len(upcoming_items)
    )
    attention_load = min(100, weighted_load * 10)
    if attention_load >= 70:
        load_label = "High focus load"
    elif attention_load >= 40:
        load_label = "Moderate focus load"
    elif attention_load > 0:
        load_label = "Light focus load"
    else:
        load_label = "Clear runway"

    open_loops = len(pending_tasks) + len(pending_actions)
    focus_queue = []
    for bucket_name, bucket in (
        ("safety", safety_items),
        ("critical", critical_items),
        ("important", important_items),
        ("upcoming", upcoming_items),
    ):
        for item in bucket:
            focus_queue.append({**item, "horizon": bucket_name})
    focus_queue.sort(
        key=lambda x: (
            {"safety": 4, "critical": 4, "important": 2, "upcoming": 1}[x["horizon"]],
            x.get("signal_count", 0),
            x.get("score") or 0,
        ),
        reverse=True,
    )
    focus_queue = focus_queue[:FOCUS_QUEUE_LIMIT]

    # A deterministic recommendation based on the strongest current queue item.
    if safety_items:
        top = safety_items[0]
        next_move = {
            "label": "Review the safety signal before acting",
            "detail": top["title"],
            "type": "safety",
            "why": "Safety signals override ordinary attention because acting too quickly can create avoidable risk.",
        }
    elif critical_items:
        top = critical_items[0]
        next_move = {
            "label": "Resolve the highest-priority decision",
            "detail": top["title"],
            "type": "critical",
            "why": f"It has {top['signal_count']} converging attention signal(s), making it the strongest current focus candidate.",
        }
    elif important_items:
        top = important_items[0]
        next_move = {
            "label": "Close one important open loop",
            "detail": top["title"],
            "type": "important",
            "why": "Clearing an important unresolved item reduces the amount of attention carried into the next horizon.",
        }
    elif upcoming_items:
        top = upcoming_items[0]
        next_move = {
            "label": "Prepare for the next attention trigger",
            "detail": top["title"],
            "type": "upcoming",
            "why": "VYRA found a forward-looking signal before it crossed the immediate notify threshold.",
        }
    else:
        next_move = {
            "label": "Protect the clear runway",
            "detail": "Nothing currently requires a decision from you.",
            "type": "clear",
            "why": "No active attention horizon has crossed VYRA's current thresholds.",
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "critical_count": len(critical_items),
        "important_count": len(important_items),
        "upcoming_count": len(upcoming_items),
        "safety_count": len(safety_items),
        "critical_items": critical_items,
        "important_items": important_items,
        "upcoming_items": upcoming_items,
        "safety_items": safety_items,
        "focus_queue": focus_queue,
        "next_move": next_move,
        "attention_load": attention_load,
        "load_label": load_label,
        "open_loops": open_loops,
        "pending_action_count": len(pending_actions),
        "pending_task_count": len(pending_tasks),
        "focus_slots": 3,
        "recommended_focus": min(3, len(focus_queue)),
        "is_empty": not (critical_items or important_items or upcoming_items or safety_items),
        "message_focus": message_focus,
        "message_count": len(message_focus),
    }
