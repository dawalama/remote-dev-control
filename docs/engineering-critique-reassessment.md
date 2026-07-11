# Engineering Critique Reassessment

Date: July 10, 2026  
Authoritative repository: `~/remote-dev-control` at baseline `4345c42`  
Compared repository: `~/agent-dev-tool` at baseline `27df8f9` plus the uncommitted founder-session work

## Executive conclusion

The recent work was implemented against an older checkout, but most of its product and security reasoning remains valid. The code should not be copied wholesale. The current repository contains newer channel, email, action, terminal, and mission-session systems that must be preserved.

The correct integration strategy is:

1. Retain the current repository as authoritative.
2. Reapply the host-control security boundary directly to its newer routes and clients.
3. Extend the existing durable `sessions` aggregate instead of adding the older parallel `work_sessions` aggregate.
4. Rebuild the Work Sessions interface over the current channel/session manager.
5. Preserve the current dashboard as a session-linked Control room.

## What remains valid

### Critical: the authorization boundary is incomplete

Confirmed in the current repository:

- HTTP authorization is allow-by-omission: authenticated routes without an explicit permission mapping are accepted.
- Sensitive context, screenshots, browser viewer/proxy, and VNC paths are in public prefixes.
- `/state` exposes the full server state with optional authentication.
- several WebSocket handlers either have no authentication or accept long-lived bearer tokens in query strings.
- viewer tokens are not consistently separated from terminal, browser, process, and state-changing control operations.
- OpenAPI documentation is public by default.

Disposition: **retain and fix immediately**.

### Critical: browser-origin and error disclosure defaults are unsafe

Confirmed in the current repository:

- CORS allows every origin while credentials are enabled.
- unhandled HTTP responses include exception strings and traceback fragments.
- WebSocket origin validation is absent.

Disposition: **retain and fix immediately**.

### High: goals need evidence-backed completion

The current mission session manager records terminals, events, logs, retries, and generated summaries. It does not make completion depend on acceptance criteria, Git evidence, verification results, or explicit waivers.

Disposition: **retain, but implement by extending current sessions**.

### High: approval and control ownership need durable state

The current session statuses (`pending`, `running`, `waiting`, `done`, `failed`, `cancelled`) identify lifecycle but do not represent approval requests, human takeover, return-control guidance, or an authoritative control owner.

Disposition: **retain, but integrate with the current session manager and terminal linkage**.

### High: Sessions should become the primary UX

The current repository already replaced Tasks with Sessions across layouts and added session viewers. That validates the direction. Its session list is still channel-scoped and oriented around terminal execution rather than goal definition, acceptance, supervision, and evidence.

Disposition: **retain and accelerate**. Promote the existing Sessions surface rather than introduce a separate top-level product.

### Medium: lifecycle and singleton test isolation is weak

The current Python baseline cannot collect all tests because `tests/test_visual_streaming.py` still imports the obsolete `ai_knowledge` package. Runtime managers and repositories also need repeatable startup/shutdown coverage.

Disposition: **retain**. Repair stale tests first, then port lifecycle regression tests selectively.

### Medium: dependency and CI gates are incomplete

The repository has only eight discoverable Python tests before collection fails. Frontend build is the documented gate, but a comprehensive locked CI workflow and isolated test home are still needed.

Disposition: **retain**.

## What the newer repository already solved or materially improved

- A durable `sessions` table and `SessionManager` now exist.
- Sessions link to multiple terminals through `session_terminals`.
- Session recovery, retry, terminal monitoring, log capture, and event timelines exist.
- Desktop, mobile, and kiosk already contain session UI surfaces.
- Channels, workstreams, email, and conversation persistence provide a richer supervision context.
- Terminal redraw and persistence work is newer than the older checkout and must be preserved.
- SSRF hardening and email robustness landed after the old checkout.

These improvements make the old standalone `work_sessions.py`, old migration, and old Work Sessions page useful as behavioral prototypes, not files to copy unchanged.

## What should not be reapplied unchanged

- Do not replace `app.py`, `worker.py`, repositories, configuration, or frontend layouts wholesale; that would remove newer capabilities.
- Do not create a second session aggregate and a competing `/work-sessions` lifecycle without first reconciling it with `SessionManager`.
- Do not copy the older database migration number; the current repository already has migrations through `20260410000015`.
- Do not copy the old frontend polling implementation as the final design. The current architecture already identifies WebSocket push as the intended session update path.
- Do not copy direct `useEffect` usage from the prototype; current project rules require declarative data flow or `useMountEffect` for true external synchronization.

## Revised implementation sequence

1. Close HTTP and WebSocket authorization gaps while keeping current browser, terminal, channel, and voice clients functional.
2. Restore a trustworthy baseline: stale imports, isolated RDC home, lifecycle tests, locked CI, frontend build/lint.
3. Extend `sessions` with goal, acceptance criteria, control owner, blocker, policy, budget, unresolved risks, and evidence-gated completion.
4. Add durable approval, evidence, criterion evaluation, and feedback tables keyed to the existing session ID.
5. Connect task/provider steps and API-direct orchestrator tool calls to the existing session timeline.
6. Make the session list the primary shell; make terminal/browser/log panes session-linked Control room resources.
7. Run the founder dogfood protocol against real providers and registered projects.

## Migration evidence

- `~/remote-dev-control` origin: `git@github.com:dawalama/remote-dev-control.git`
- current baseline: `4345c42` (`fix: improve imported project dev action detection`)
- old checkout origin: `git@github.com:dawalama/agent-dev-tool.git`
- old baseline: `27df8f9` (`More rename`)
- authoritative checkout was clean before migration.
- migration work is isolated on branch `codex/migrate-work-sessions` in a separate worktree.
