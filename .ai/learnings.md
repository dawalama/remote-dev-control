# RDC Development Learnings

Corrections and patterns learned during development.

## Rebrand: adt → rdc

The project was rebranded from `agent-dev-tool` (CLI: `adt`) to `remote-dev-ctrl` (CLI: `rdc`).
- Python module: `src/remote_dev_ctrl/`
- Config dir: `~/.rdc/` (falls back to `~/.adt/` for compat)
- DB: `rdc.db` (falls back to `adt.db`)
- Env vars: `RDC_HOME`, `RDC_SECRET_KEY` (falls back to `ADT_*`)
- Frontend localStorage: `rdc_*` (migration shim in `main.tsx`)

## All Three Layouts Are First-Class

Desktop, Mobile, and Kiosk layouts must all be updated when making feature changes.
- Desktop: `layouts/desktop.tsx`
- Mobile: `layouts/mobile.tsx`
- Kiosk: `layouts/kiosk.tsx`

## Terminal PTY Architecture

- `terminal.py` uses `socat`-based PTY relay processes that survive server restarts
- Background buffer polling pre-WebSocket, event-loop `add_reader` post-connect
- MCP detection: server-side `_mcp_approval_needed` flag in StateSnapshot
- Default command: plain `cursor-agent` (no --resume). Configurable per-project.

## cursor-agent Quirks

- `cursor-agent --resume` + MCP Server Approval = exits with code 0 (bug)
- `cursor-agent` (fresh session, no `--resume`) works correctly
- Uses Ink (React terminal UI) — requires raw mode on stdin, needs a real PTY

## CDP / rrweb Recording

- `Runtime.evaluate` scopes `var` declarations — use `window.rrweb = ...` explicitly
- Dual push/pull: CDP binding for low-latency + periodic buffer drain as fallback
- Recordings stored as chunked JSON in `~/.rdc/recordings/{rec_id}/chunk_{n}.json`

## Frontend Build

- `pnpm run build` runs `tsc -b && vite build`
- Unused variables/imports are build errors (strict TypeScript)
- Always build after changes to verify zero errors

## Auth Middleware

- Public paths defined in `middleware.py` (`PUBLIC_PATHS` and `PUBLIC_PATH_PREFIXES`)
- New SPA routes must be added to public paths so the frontend `index.html` gets served
- Frontend handles auth client-side via localStorage token

## Flexbox Height Issues

Use proper flexbox chain to prevent panels from being cut off:
```html
<div class="h-screen flex flex-col">
  <header class="shrink-0">...</header>
  <main class="flex-1 min-h-0 overflow-auto">...</main>
</div>
```
Key: `min-h-0` on flex children prevents content from expanding beyond bounds.

## Database Migrations

- SQL files in `src/remote_dev_ctrl/server/db/migrations/rdc/`
- Auto-run on server startup via `db/migrate.py`
- Single consolidated SQLite DB at `~/.rdc/data/rdc.db`
