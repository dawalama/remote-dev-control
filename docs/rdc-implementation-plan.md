# RDC Implementation Plan

Date: July 10, 2026  
Authoritative repository: `~/remote-dev-control`  
Branch: `codex/migrate-work-sessions`

## Product decision

RDC is a goal-centered remote development supervisor. Sessions are the primary workflow. The existing terminal, browser, logs, actions, and direct manipulation dashboard become the session-linked **Control room**.

The current repository's `SessionManager`, `sessions` table, channel linkage, multi-terminal support, recovery, retry, and event timeline are the aggregate root. The prototype `/work-sessions` implementation from `agent-dev-tool` is not installed as a parallel system.

## Current migration status

Completed on the migration branch:

- Product strategy and acquisition diligence moved into the authoritative repository.
- Engineering critique reassessed against the newer May codebase.
- HTTP authorization now fails closed with domain-level read/control permissions.
- Sensitive state, context, screenshot, browser, VNC, terminal, process, and administration paths were removed from broad public access.
- CORS is restricted to local origins plus `RDC_ALLOWED_ORIGINS`.
- unhandled responses no longer disclose exception and traceback fragments.
- OpenAPI documentation requires authentication unless `RDC_EXPOSE_API_DOCS=1`.
- WebSockets share authentication, authorization, and origin checks.
- browser clients exchange bearer tokens for short-lived, path-bound WebSocket tickets.
- ticket-bearing access logs are redacted.
- the stale `ai_knowledge` test imports were removed from pytest collection.
- tests use an isolated `RDC_HOME` and never mutate the founder installation.
- repeated FastAPI startup/shutdown resets database and runtime singletons.
- CI installs locked dependencies, runs Python tests, and builds the frontend.
- the existing session schema now stores goals, acceptance criteria, control ownership, policy, budget, blockers, current step, and unresolved risks.
- durable session approvals, evidence, criteria, and founder feedback tables were added without colliding with the prototype database tables.
- supervised execution ends in `verifying`; it cannot claim completion directly.
- completion requires Git evidence, passing verification, and every criterion passed or waived.
- terminal input prompts create durable approval requests; approve/reject decisions are idempotent and relay to the linked terminal.
- human takeover and return-control guidance are durable session events.
- the shared Sessions card now creates sessions from registered projects with real form controls in desktop, mobile, and kiosk.
- session actions use explicit Start, Cancel session, Take control, Return control, and Mark complete semantics.

Verified gates:

- 23 Python tests pass.
- frontend TypeScript and production build pass.
- Git whitespace validation passes.

Known baseline debt:

- repository-wide frontend lint currently reports 48 errors and 16 warnings that predate this migration; the changed session viewer has no remaining new lint error.
- repository-wide Ruff has substantial pre-existing style debt, especially in the monolithic app and session manager.
- Vite reports an oversized main chunk and static/dynamic import overlap.

## Next execution sequence

### 1. Provider execution correctness

- Centralize provider-to-command mapping for Claude, Cursor, Gemini, shell, and deterministic diagnostic execution.
- Stop silently launching Claude when another provider was selected.
- Surface authentication/configuration failures in the session detail instead of only terminal logs.
- Add a safe retry path that retains the same session identity and evidence history.

### 2. Supervision UI completion

- Push session state and approval updates through the existing state WebSocket instead of manual refresh.
- Present failure reason, current step, elapsed time, and control owner in every layout.
- Add optional notes to approval and criterion decisions.
- Link every session to its terminal, browser, action logs, diff, and captured screenshots.
- Promote Sessions from a side tab to the product shell after founder dogfood confirms the integrated flow.

### 3. Security compatibility audit

- Exercise browser screencast tickets, voice streaming, logs, terminal reconnect, paired devices, and legacy VNC against a live server.
- Add route-level operator/viewer tests for project, channel, recipe, action, browser, and administration mutations.
- Validate Twilio webhook signatures before leaving the voice webhook prefix public.
- Remove any remaining query-token compatibility after all clients use tickets.

### 4. Evidence and recovery

- Attach authenticated browser screenshots as session evidence.
- Detect out-of-session working-tree changes and concurrent worktree ownership.
- Reconcile recovered terminal/session status deterministically after process or server loss.
- Record unresolved risks and verification output limits in the completion summary.

### 5. Founder dogfood

- Run one deterministic no-write diagnostic session.
- Run ten real development sessions across at least two projects and two device contexts.
- Record usefulness, remote supervision, interventions, approval quality, evidence sufficiency, time saved, and trust failures.
- Remove older UX only after the corresponding session workflow succeeds repeatedly.

## UX migration rules

- Sessions define goals, execution state, decisions, evidence, and outcomes.
- Control room provides direct terminal/browser/log access for intervention and debugging.
- Project selection is context for session creation, not the primary activity.
- One-off terminal work remains available when durable supervision is unnecessary.
- Settings, pairing, channels, knowledge, and administration remain global utilities.
- No older control is deleted merely because a replacement exists on paper; founder dogfood must prove it.
