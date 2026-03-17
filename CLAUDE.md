# RDC — Remote Dev Ctrl

## What This Is

A command center for AI-assisted development. Server + dashboard + CLI for managing projects, terminals, tasks, actions (services and commands), and AI agents.

## Architecture

- **Backend**: Python (FastAPI) at `src/remote_dev_ctrl/server/`
- **Frontend**: React + TypeScript + Tailwind + Zustand at `frontend/`
- **CLI**: Typer at `src/remote_dev_ctrl/cli.py`
- **Database**: SQLite at `~/.rdc/data/` (auto-migrated)
- **Config**: YAML at `~/.rdc/config.yml`

## Key Directories

```
src/remote_dev_ctrl/server/
  app.py            # FastAPI app, routes, lifespan
  config.py         # Config loading, RDC home
  terminal.py       # PTY management, WebSocket relay
  worker.py         # Task execution engine
  intent.py         # AI orchestrator (natural language → actions)
  streaming.py      # SSE/streaming endpoints
  db/               # SQLite repos, migrations
  agents/           # Agent provider abstraction

frontend/src/
  layouts/           # desktop.tsx, mobile.tsx, kiosk.tsx — ALL first-class
  features/          # Feature modules (terminal, tasks, chat, browser, etc.)
  stores/            # Zustand state (state-store, ui-store, terminal-store, etc.)
  hooks/             # Shared hooks (use-orchestrator, use-voice)
  components/        # Shared UI components
```

## Development Rules

1. **All three layouts are first-class**: desktop, mobile, kiosk. Never make a change that applies to all layouts in only one place. Check all three.

2. **Frontend build**: `cd frontend && pnpm run build` (runs `tsc -b && vite build`). Must pass with zero errors — unused variables/imports are build errors.

3. **Server start**: `rdc server start` or `uvicorn remote_dev_ctrl.server.app:app --reload`

4. **Frontend dev**: `cd frontend && pnpm dev` (Vite dev server, proxies API to :8420)

5. **Component patterns**:
   - Desktop uses `EmbeddedTerminal`, `RightTabs`, `ChatFAB`
   - Kiosk uses `EmbeddedTerminal`, `KioskSideTabs`, `KioskChatPanel`, `KioskActionBar`
   - Mobile uses card components from `features/mobile/` + `MobileCommandBar` + `ChatCard`

6. **State flow**: Server → WebSocket `/ws/state` → `state-store.ts` → components subscribe via Zustand selectors

7. **Terminal architecture**: PTY relay processes (`socat`-based) survive server restarts. Session metadata persisted to `~/.rdc/terminal_sessions.json`.

8. **Database migrations**: SQL files in `src/remote_dev_ctrl/server/db/migrations/`. Auto-run on server start.

## Common Tasks

```bash
# Build frontend
cd frontend && pnpm run build

# Type-check only
cd frontend && npx tsc --noEmit

# Start server with hot-reload
rdc server start --reload

# Run frontend dev server
cd frontend && pnpm dev
```
