# VYRA — Proactive Brief Live Decision Engine

This build fixes the Proactive Brief so it is not a static report.

## What changed
- Every analyzed message gets its own attention record in the live decision stream.
- The stream reads directly from the logged-in user's `messages` + `decisions` + open tasks/actions.
- New messages submitted through Analyze appear automatically after VYRA persists the analysis.
- The page polls the live Proactive Brief API every 5 seconds and reloads when a new/changed decision is detected.
- "Recalculate from Analyze" forces a fresh database read.
- "Inspect decision" opens the full message, attention score, decision, horizon, observed signals and decision reasons.
- Focus Queue remains a ranked subset rather than a duplicate inbox.
- Horizons are calculated per message: CHECK FIRST / ACT NOW / HANDLE SOON / LOOK AHEAD / MONITOR.
- No external LLM, API key, fake data or fabricated confidence value is used.
- Multi-user filtering remains enforced through the logged-in user's `user_id`.

## Demo
The bundled `instance/vyra.db` retains the existing demo workspace/data. Existing demo login is unchanged.

## Flow
Analyze -> persist message + decision -> Proactive Brief live stream -> per-message attention horizon -> ranked focus queue -> inspect decision -> Action Center for human-controlled action review.
