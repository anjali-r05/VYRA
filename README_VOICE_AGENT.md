# VYRA Voice Agent

The Voice Agent is a live interface to the existing VYRA workspace. It does not maintain a separate demo state.

## Flow
Microphone -> browser SpeechRecognition -> Flask voice intent resolver -> current user's database -> response -> browser SpeechSynthesis.

## Supported voice intents
- What needs my attention / what should I focus on / what's urgent
- Give me my Proactive Brief
- What are my pending tasks
- What actions are waiting for approval
- What changed / latest / newest message
- Why is this urgent / why is this important
- Approve action N
- Dismiss action N
- Reopen action N

Approval, dismissal and reopen requests always require a second confirmation click. Executed actions cannot be changed through voice.

## Browser note
Speech synthesis is widely available. Speech recognition has more limited browser support, so the UI provides a clear fallback message when recognition is unavailable. Chrome/Edge are recommended for the demo.
