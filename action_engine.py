"""
VYRA V3 — Action Suggestions

Detects appropriate next actions from a message's actual analyzed
data (V1/V2/V3 result, extracted tasks, and whether it belongs to an
existing thread). Every suggestion is traceable to a concrete signal
in the data — nothing here is random or generic.

This module ONLY produces suggestions (status="suggested" once persisted
by app.py). It never executes anything. The review-first lifecycle
(suggested -> approved/edited/dismissed -> executed) is enforced by the
Action model's status field and by app.py's routes, which refuse to let
a row reach "executed" without first passing through "approved".

Public interface: `suggest_actions(text, result, tasks, matched_thread) -> list[dict]`
Each returned dict has: action_type, suggestion_text
"""

RESCHEDULE_WORDS = ("reschedul", "postpon", "moved to", "new time", "changed to")
QUESTION_HINT_WORDS = ("could you", "can you", "would you", "let us know", "please advise")


def suggest_actions(text: str, result: dict, tasks: list, matched_thread=None) -> list:
    text_lower = text.lower()
    suggestions = []
    seen_types = set()

    def add(action_type, suggestion_text):
        if action_type in seen_types:
            return
        seen_types.add(action_type)
        suggestions.append({"action_type": action_type, "suggestion_text": suggestion_text})

    # Suspicious messages: never suggest anything that implies trusting or
    # acting on the message's content (no "Confirm", no "Reply", no "Add
    # Task" derived from a scam's fake deadline). The only safe suggestion
    # is to have the user review it manually — consistent with V1's own
    # "suspicious is never fully hidden" philosophy in agent/decision.py.
    if result.get("suspicious"):
        add("review", "This message was flagged as suspicious by VYRA's safety check — review it carefully before taking any action.")
        return suggestions

    decision = result.get("final_decision", result.get("decision"))
    category = result.get("category")
    action_required = result.get("action_required", False)

    # Reschedule / confirmation-style meeting updates
    if category == "meeting" and any(w in text_lower for w in RESCHEDULE_WORDS):
        add("confirm", "This meeting appears to have been rescheduled — confirm the new time.")
    elif category == "meeting" and action_required:
        add("confirm", "Confirm your attendance for this meeting.")

    # A message that is itself a question or explicit request for a reply
    if "?" in text or any(w in text_lower for w in QUESTION_HINT_WORDS):
        add("reply", "This message asks a question or requests a response — consider replying.")

    # Document review requests
    if "document" in text_lower or "attached" in text_lower or "attachment" in text_lower:
        add("review", "This message references a document — review it.")

    # Extracted tasks become "Add Task" suggestions, one per distinct task
    # so each stays traceable to its own deadline/priority.
    for task in tasks:
        label = f"Add task: {task['title']}"
        if task.get("deadline"):
            label += f" (deadline: {task['deadline']})"
        add("add_task", label)

    # A real deadline with no other suggestion yet -> offer a reminder
    if result.get("deadline") and "add_task" not in seen_types:
        add("add_reminder", f"Add a reminder for the detected deadline ({result['deadline']}).")

    # Thread context
    if matched_thread is not None:
        add("open_thread", f"This message was added to an existing thread: '{matched_thread.title}'.")

    # High-priority final decision -> explicit "mark important" affordance
    if decision == "notify":
        add("mark_important", "VYRA marked this as high attention — mark it important to keep it visible.")

    # Low-priority final decision with nothing else actionable -> dismiss
    if decision == "mute" and not suggestions:
        add("dismiss", "This message is low priority with no detected action — dismiss it.")

    return suggestions