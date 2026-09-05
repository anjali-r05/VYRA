"""VYRA Voice Agent — live, deterministic workspace assistant.

Voice is the interface, not the intelligence layer: browser speech recognition
produces text, this module resolves the user's intent, reads the signed-in
workspace, and returns an answer grounded in persisted VYRA data. No external
LLM/API is required.
"""

import re
from datetime import datetime, timezone


STOP_WORDS = {
    "what", "what's", "whats", "tell", "show", "give", "me", "my", "the",
    "this", "that", "is", "are", "do", "does", "can", "please", "right",
    "now", "currently", "today", "about", "with", "for", "and", "or", "to",
    "of", "in", "on", "a", "an", "it", "i", "need", "needs", "should",
    "could", "would", "you", "there", "anything", "something", "please",
}


def _fmt_dt(value):
    if not value:
        return ""
    try:
        dt = value if hasattr(value, "strftime") else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    except Exception:
        return str(value)


def _name(item):
    return (getattr(item, "title", "") or "").strip()


def _priority_order(value):
    return {"high": 0, "medium": 1, "low": 2}.get((value or "low").lower(), 3)


def _normalize(text):
    text = (text or "").lower().strip()
    text = text.replace("’", "'")
    text = re.sub(r"[^a-z0-9\s']+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _tokens(text):
    return [t for t in re.findall(r"[a-z0-9']+", _normalize(text)) if t not in STOP_WORDS]


def _contains_any(text, phrases):
    return any(p in text for p in phrases)


def _live_snapshot(user_id):
    from models import Action, Decision, Message, Task, Thread

    messages = (
        Message.query.filter_by(user_id=user_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(250)
        .all()
    )
    message_ids = {m.id for m in messages}
    decisions = (
        {
            d.message_id: d
            for d in Decision.query.filter(Decision.message_id.in_(message_ids)).all()
        }
        if message_ids else {}
    )

    tasks = (
        Task.query.join(Message, Message.id == Task.message_id)
        .filter(Message.user_id == user_id, Task.status == "pending")
        .order_by(Task.id.desc())
        .limit(150)
        .all()
    )

    # Keep every review/open action available. The answer layer distinguishes
    # "waiting for approval" from already-approved actions.
    actions = (
        Action.query.join(Message, Message.id == Action.message_id)
        .filter(Message.user_id == user_id, Action.status.in_(("suggested", "edited", "approved")))
        .order_by(Action.id.desc())
        .limit(150)
        .all()
    )

    threads = []
    for thread in Thread.query.order_by(Thread.updated_at.desc(), Thread.id.desc()).limit(100).all():
        if any(mid in message_ids for mid in thread.message_ids):
            threads.append(thread)

    message_by_id = {m.id: m for m in messages}
    return {
        "messages": messages,
        "message_by_id": message_by_id,
        "decisions": decisions,
        "tasks": tasks,
        "actions": actions,
        "threads": threads,
    }


def _top_attention(snapshot):
    rows = []
    for message in snapshot["messages"]:
        decision = snapshot["decisions"].get(message.id)
        if not decision:
            continue
        rows.append((float(decision.attention_score or 0), message, decision))
    rows.sort(
        key=lambda x: (
            x[0],
            x[2].urgency == "high",
            bool(x[2].suspicious),
            bool(x[2].action_required),
            x[1].created_at or datetime.min,
        ),
        reverse=True,
    )
    return rows


def _message_score(message, decision, query_tokens):
    haystack = _normalize(message.content)
    overlap = sum(1 for token in query_tokens if len(token) >= 3 and token in haystack)
    exact_bonus = 5 if any(token in haystack for token in ("urgent", "office", "deadline", "immediately")) else 0
    signal_bonus = (20 if decision.urgency == "high" else 0) + (15 if decision.action_required else 0)
    return overlap * 18 + exact_bonus + signal_bonus + float(decision.attention_score or 0) * 0.01


def _find_message_reference(snapshot, text):
    rows = [
        (message, snapshot["decisions"].get(message.id))
        for message in snapshot["messages"]
        if snapshot["decisions"].get(message.id)
    ]
    if not rows:
        return None

    query_tokens = _tokens(text)
    if not query_tokens:
        return max(rows, key=lambda x: float(x[1].attention_score or 0))

    ranked = []
    for message, decision in rows:
        ranked.append((_message_score(message, decision, query_tokens), message, decision))
    ranked.sort(key=lambda x: x[0], reverse=True)

    # Require some lexical evidence when the user appears to reference a
    # particular message. Otherwise use VYRA's highest-attention item.
    best = ranked[0]
    if any(token in _normalize(best[1].content) for token in query_tokens):
        return best[1], best[2]
    return rows[0][0], rows[0][1]


def _find_action(snapshot, text):
    lowered = _normalize(text)
    actions = snapshot["actions"]
    if not actions:
        return None

    number = re.search(r"(?:action|item|suggestion|number)?\s*(?:number\s*)?(\d+)\b", lowered)
    if number:
        idx = int(number.group(1)) - 1
        if 0 <= idx < len(actions):
            return actions[idx]

    query_tokens = set(_tokens(text))
    candidates = []
    for action in actions:
        message = snapshot["message_by_id"].get(action.message_id)
        haystack = _normalize(f"{action.suggestion_text} {message.content if message else ''}")
        overlap = sum(1 for token in query_tokens if len(token) >= 3 and token in haystack)
        if overlap:
            candidates.append((overlap, action))
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1] if candidates else (actions[0] if len(actions) == 1 else None)


def _answer_attention(snapshot):
    rows = _top_attention(snapshot)
    if not rows:
        return "I don't have any analyzed messages yet. Add a message in Analyze and VYRA will calculate what deserves your attention."

    lead_score, lead_msg, lead_dec = rows[0]
    parts = [
        f"You have {len(rows)} analyzed attention items. Your top priority is: {lead_msg.to_preview(180)}",
        f"VYRA scored it {round(lead_score)} out of 100 and marked it {lead_dec.urgency} urgency.",
    ]

    signals = []
    if lead_dec.suspicious:
        signals.append("a safety signal")
    if lead_dec.action_required:
        signals.append("an action is required")
    if lead_dec.time_sensitive:
        signals.append("it is time-sensitive")
    if lead_dec.deadline:
        signals.append(f"a deadline was detected ({lead_dec.deadline})")
    if lead_dec.relevance >= 70:
        signals.append("it has high relevance")
    if signals:
        parts.append("Why it needs attention: " + ", ".join(signals) + ".")

    if lead_dec.reasons:
        parts.append("VYRA's reason: " + " ".join(str(r).rstrip(".") for r in lead_dec.reasons[:2]) + ".")

    if lead_dec.action_required:
        parts.append("Recommended next move: resolve the required action while the signal is active.")
    elif lead_dec.time_sensitive or lead_dec.deadline:
        parts.append("Recommended next move: handle it before the time-sensitive window closes.")
    else:
        parts.append("Recommended next move: review this item before lower-priority work.")

    if len(rows) > 1:
        second = rows[1]
        parts.append(f"Next after that: {second[1].to_preview(100)}")
    return " ".join(parts)


def _answer_why(snapshot, text):
    reference = _find_message_reference(snapshot, text)
    if not reference:
        return "I need at least one analyzed message before I can explain an attention decision."
    message, decision = reference
    score = round(float(decision.attention_score or 0))
    reasons = [str(r).strip().rstrip(".") for r in (decision.reasons or []) if str(r).strip()]
    parts = [
        f"Here is why VYRA marked this important: {message.to_preview(180)}",
        f"Attention score: {score} out of 100. Urgency: {decision.urgency}.",
    ]
    if decision.suspicious:
        parts.append("There is a safety signal, so VYRA recommends reviewing it before acting.")
    if decision.action_required:
        parts.append("The message requires an action.")
    if decision.time_sensitive:
        parts.append("It contains a time-sensitive signal.")
    if decision.deadline:
        parts.append(f"Detected deadline: {decision.deadline}.")
    if decision.relevance >= 70:
        parts.append("It has high relevance to your current attention queue.")
    if reasons:
        parts.append("Reason from the analysis: " + "; ".join(reasons[:4]) + ".")
    elif decision.explanation:
        parts.append("Analysis explanation: " + str(decision.explanation).strip())
    return " ".join(parts)


def _answer_tasks(snapshot):
    tasks = sorted(snapshot["tasks"], key=lambda t: (_priority_order(t.priority), -(t.id or 0)))
    if not tasks:
        return "You have no pending tasks in your current workspace."
    chunks = [f"You have {len(tasks)} pending tasks."]
    for i, task in enumerate(tasks[:7], 1):
        deadline = f" Deadline: {task.deadline}." if task.deadline else ""
        title = _name(task) or (task.description or "Untitled task").strip()
        chunks.append(f"Task {i}: {title}. Priority: {task.priority}.{deadline}")
    if len(tasks) > 7:
        chunks.append(f"There are {len(tasks) - 7} more pending tasks in Task Center.")
    return " ".join(chunks)


def _answer_actions(snapshot):
    actions = snapshot["actions"]
    reviewable = [a for a in actions if a.status in ("suggested", "edited")]
    approved = [a for a in actions if a.status == "approved"]
    if not actions:
        return "You have no open actions in your current workspace."

    chunks = []
    if reviewable:
        chunks.append(f"You have {len(reviewable)} action{'' if len(reviewable) == 1 else 's'} waiting for your approval.")
        for i, action in enumerate(reviewable[:7], 1):
            chunks.append(f"Action {i}: {action.suggestion_text[:150]}. Status: {action.status}.")
    if approved:
        chunks.append(f"You also have {len(approved)} approved action{'' if len(approved) == 1 else 's'} waiting for execution in Action Center.")
    return " ".join(chunks)


def _answer_latest(snapshot):
    if not snapshot["messages"]:
        return "There are no messages in your current workspace yet."
    msg = snapshot["messages"][0]
    decision = snapshot["decisions"].get(msg.id)
    if not decision:
        return f"Your latest message is: {msg.to_preview(180)}"
    return (
        f"Your latest analyzed message is: {msg.to_preview(180)} "
        f"VYRA scored it {round(float(decision.attention_score or 0))} out of 100, "
        f"with {decision.urgency} urgency and a {decision.decision} decision."
    )


def _answer_brief(snapshot, user_id):
    from agent.proactive_brief import generate_proactive_brief

    brief = generate_proactive_brief(user_id=user_id)
    critical = brief.get("critical_count", 0)
    important = brief.get("important_count", 0)
    upcoming = brief.get("upcoming_count", 0)
    safety = brief.get("safety_count", 0)
    total = critical + important + upcoming + safety
    open_loops = brief.get("open_loops", 0)
    next_move = (brief.get("next_move") or {}).get("detail") or "Review the highest-priority attention item."

    chunks = [
        f"Your live Proactive Brief has {total} attention items: {critical} critical, {important} important, {upcoming} upcoming, and {safety} safety.",
        f"You have {brief.get('pending_task_count', len(snapshot['tasks']))} pending tasks and {brief.get('pending_action_count', len(snapshot['actions']))} open actions.",
        f"VYRA's next-best attention decision is: {next_move}",
    ]

    focus = brief.get("focus_queue") or []
    if focus:
        titles = [str(item.get("title") or item.get("preview") or "").strip() for item in focus[:3]]
        titles = [t for t in titles if t]
        if titles:
            chunks.append("Top focus: " + " Next: ".join(titles) + ".")
    if open_loops:
        chunks.append(f"There are {open_loops} open attention loops to keep in view.")
    return " ".join(chunks)


def _answer_attention_for_reference(snapshot, text):
    """Answer 'what needs my attention' and variants with actual live detail."""
    return _answer_attention(snapshot)


def _detect_intent(text):
    """High-recall intent resolver for speech transcripts.

    SpeechRecognition can vary punctuation, contractions and exact wording, so
    routing is phrase-family based rather than dependent on one exact sentence.
    """
    t = _normalize(text)

    # State-changing commands have highest priority so "approve this action"
    # cannot be mistaken for a generic action-list query.
    if _contains_any(t, (
        "approve action", "approve this", "approve that", "approve item",
        "dismiss action", "dismiss this", "dismiss that", "dismiss item",
        "reopen action", "reopen this", "reopen that", "reopen item",
        "review action", "review this action", "review again",
    )) or re.search(r"\b(approve|dismiss|reopen)\s+(?:number\s*)?\d+\b", t):
        return "action_review"

    if _contains_any(t, (
        "proactive brief", "attention brief", "daily brief", "my brief",
        "brief for me", "give me a brief", "summarize my attention",
        "attention summary", "what is my brief", "whats my brief",
    )):
        return "brief"

    if _contains_any(t, (
        "waiting for approval", "waiting for my approval", "need my approval",
        "pending action", "pending actions", "open actions", "my actions",
        "actions do i have", "what actions", "suggested action", "suggested actions",
    )):
        return "actions"

    if _contains_any(t, (
        "pending task", "pending tasks", "my tasks", "what tasks", "tasks do i have",
        "to do", "todo", "things i need to do", "what do i need to do",
        "what do i have to do", "what should i do",
    )):
        return "tasks"

    if _contains_any(t, (
        "what changed", "latest changes", "anything new", "what is new", "whats new",
        "new message", "new messages", "latest message", "latest messages", "newest message",
        "what came in", "what just came in",
    )):
        return "latest"

    if _contains_any(t, (
        "what needs my attention", "what need my attention", "what needs attention",
        "what should i focus", "what do i focus", "what do i need to focus",
        "what is urgent", "whats urgent", "what's urgent", "anything urgent",
        "anything important", "what is important right now", "what's important right now",
        "what matters right now", "what matters most", "what should i handle first",
        "what should i deal with first", "prioritize", "priority", "top priority",
        "what do i need to handle", "what should i handle first", "tell me what i should handle first", "what deserves my attention", "where should i focus",
    )):
        return "attention"

    if _contains_any(t, (
        "why", "explain why", "why is this", "why is that", "why does this",
        "why does that", "why is it important", "why is it urgent", "why does it need attention",
        "why should i care", "why should i handle this",
    )):
        return "why"

    return "help"


def query_voice(user_id, text):
    """Resolve a natural-language voice command against live workspace data."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "message": "I didn't hear a command. Try asking what needs your attention."}

    snapshot = _live_snapshot(user_id)
    intent = _detect_intent(text)

    if intent == "attention":
        answer = _answer_attention_for_reference(snapshot, text)
        return {"ok": True, "intent": intent, "message": answer, "requires_confirmation": False}

    if intent == "why":
        answer = _answer_why(snapshot, text)
        return {"ok": True, "intent": intent, "message": answer, "requires_confirmation": False}

    if intent == "brief":
        answer = _answer_brief(snapshot, user_id)
        return {"ok": True, "intent": intent, "message": answer, "requires_confirmation": False}

    if intent == "tasks":
        answer = _answer_tasks(snapshot)
        return {"ok": True, "intent": intent, "message": answer, "requires_confirmation": False}

    if intent == "actions":
        answer = _answer_actions(snapshot)
        return {"ok": True, "intent": intent, "message": answer, "requires_confirmation": False}

    if intent == "latest":
        answer = _answer_latest(snapshot)
        return {"ok": True, "intent": intent, "message": answer, "requires_confirmation": False}

    if intent == "action_review":
        action = _find_action(snapshot, text)
        if not action:
            return {
                "ok": True,
                "intent": intent,
                "message": "I couldn't safely identify one action. Say the action number, for example: approve action 1.",
                "requires_confirmation": False,
            }
        lowered = _normalize(text)
        if "approve" in lowered:
            desired = "approve"
        elif "dismiss" in lowered:
            desired = "dismiss"
        else:
            desired = "reopen"
        return {
            "ok": True,
            "intent": intent,
            "message": f"I found action {action.id}: {action.suggestion_text}. Do you want me to {desired} it?",
            "requires_confirmation": True,
            "confirmation_token": {"action_id": action.id, "operation": desired},
        }

    return {
        "ok": True,
        "intent": "help",
        "message": "Try asking: What needs my attention? Give me my Proactive Brief. What are my pending tasks? What actions are waiting for approval? Why is this urgent? Or approve action 1.",
        "requires_confirmation": False,
    }


def confirm_voice_action(user_id, action_id, operation):
    from models import Action, Message
    from services.database import db

    try:
        action_id = int(action_id)
    except (TypeError, ValueError):
        return {"ok": False, "message": "That action reference is invalid."}

    action = db.session.get(Action, action_id)
    if not action:
        return {"ok": False, "message": "That action no longer exists."}

    message = db.session.get(Message, action.message_id)
    if not message or message.user_id != user_id:
        return {"ok": False, "message": "I can't access that action from this workspace."}

    if operation == "approve":
        if action.status not in ("suggested", "edited"):
            return {"ok": False, "message": f"I can't approve it because its current status is {action.status}."}
        action.status = "approved"
        db.session.commit()
        return {"ok": True, "message": "Approved. The action is now waiting for execution in Action Center. I did not execute it automatically."}

    if operation == "dismiss":
        if action.status == "executed":
            return {"ok": False, "message": "That action is already executed and cannot be dismissed."}
        action.status = "dismissed"
        db.session.commit()
        return {"ok": True, "message": "Dismissed. You can reopen it later from Action Center."}

    if operation == "reopen":
        if action.status == "executed":
            return {"ok": False, "message": "Executed actions cannot be reopened."}
        action.status = "suggested"
        db.session.commit()
        return {"ok": True, "message": "Reopened. The action is back in your review queue."}

    return {"ok": False, "message": "I don't recognize that action operation."}
