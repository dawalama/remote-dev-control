# Hike Demo — HomeBase Brief / Supervise / Delegate

Founder dogfood script for the ChatGPT-Voice-class outcome on RDC:

> Away from desk → **Brief me** → start work on HomeBase → get notified when blocked/done → evidence at the desk.

## Topology language

| Term | Meaning |
|------|---------|
| **HomeBase** | Machine running `rdc server` (primary UI label) |
| **Node** | Phone/tablet/browser companion |
| **HQ** | Enterprise alias for HomeBase (not required in UI) |

## Prerequisites

1. `rdc server` running on the workstation (HomeBase).
2. Preferred agent provider configured and authenticated.
3. At least one project + workstream.
4. Phone/browser Node paired (`Pair Node` QR) with remote access (LAN / tunnel + HTTPS for mic).
5. Optional: Telegram with `allowed_users` for away notifications.

## Demo script

1. Confirm UI shows **HomeBase** (desktop header / mobile status / System Settings → Server Info).
2. Pair a phone as a **Node**.
3. On the Node or HomeBase desk, type or speak: **`Brief me`**.
4. Expect a structured reply in ≤ ~5s **without an LLM inventing status**, scoped to **all of HomeBase** by default (not only the open workstream):
   - active sessions (may span projects)
   - needs-you items
   - recommended next step  
   (Exact phrases: `brief me`, `status`, `what's running`, `what needs me`.)  
   Pin a workstream (`pin here`) only when you want a filtered brief; `unpin` returns to whole HomeBase.
5. Start work (chat/voice): e.g. *“Start: fix the flaky auth test on \<project\>”*  
   → durable **supervised** session appears in Sessions Hub (same path as UI create + retry).
6. When the session hits needs-you / completed / failed:
   - workstream channel gets a scrubbed system message
   - Telegram fires only if `allowed_users` is configured
7. At the desk, open the session workspace → evidence / summary without re-explaining the goal.

## APIs

| Endpoint | Role |
|----------|------|
| `GET /homebase/brief` | Deterministic brief (optional `?workstream_id=`) |
| `POST /sessions` + `POST /sessions/{id}/retry` | Supervised create/start (shared module) |
| Orchestrator tool `brief_homebase` | Fuzzy status questions |
| Orchestrator tool `spawn_agent` | Create+start supervised session |

## Architecture note

**HomeBase is a control plane with agents as workers.**  
Brief and notify are pure services (no LLM). Coding agents run *inside* supervised sessions.

## Dogfood checklist

Run twice on a real multi-project setup and record:

- [ ] Time-to-brief for exact phrases
- [ ] False status (missing approvals / wrong recommendation)
- [ ] Missed notifications
- [ ] Trust failures (session not in hub, wrong provider path)
- [ ] Admin token not printed at startup (production-safe claim — tracked debt)

## Explicitly deferred

- Twilio phone-call channel for brief
- Browser TTS of brief
- Continuous AirPods / barge-in
- Web push / FCM
- Multi-HomeBase runners
- HQ enterprise packaging
