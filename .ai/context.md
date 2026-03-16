# RDC (Remote Dev Ctrl) - Development Context

Last Updated: 2026-03-12

## Project Overview

RDC is a command center for AI-assisted development. FastAPI server + React dashboard + CLI for managing projects, terminals, tasks, processes, and AI agents.

- **Web dashboard**: `http://127.0.0.1:8420` (three layouts: desktop, mobile, kiosk)
- **CLI**: `rdc` (Typer-based)
- **Remote access**: Cloudflare Tunnel + Caddy reverse proxy

## Architecture

### Backend (`src/remote_dev_ctrl/server/`)

| File | Purpose |
|------|---------|
| `app.py` | FastAPI routes, WebSocket handlers, API endpoints |
| `config.py` | Config loading, RDC_HOME resolution |
| `terminal.py` | PTY management (`socat`-based), WebSocket relay |
| `worker.py` | Task execution engine (polls DB, spawns agents) |
| `intent.py` | AI orchestrator (natural language → dashboard actions) |
| `streaming.py` | SSE/streaming endpoints |
| `knowledge.py` | Bridge between DB projects and knowledge indexer |
| `middleware.py` | Auth middleware, rate limiting, public path config |
| `browser.py` | Browserless container management, CDP context capture |
| `agents/manager.py` | Agent provider abstraction |
| `db/connection.py` | SQLite connection pool |
| `db/repositories.py` | SQLite repositories (Project, Task, Event, etc.) |
| `db/models.py` | Pydantic models for all DB tables |
| `db/migrate.py` | SQL migration runner (auto-runs on startup) |

### Frontend (`frontend/src/`)

| Directory | Purpose |
|-----------|---------|
| `layouts/` | `desktop.tsx`, `mobile.tsx`, `kiosk.tsx` — all first-class |
| `features/` | Feature modules (terminal, tasks, chat, browser, wiki, mobile) |
| `stores/` | Zustand state (state-store, ui-store, terminal-store, etc.) |
| `hooks/` | Shared hooks (use-orchestrator, use-voice) |
| `components/` | Shared UI components |
| `lib/` | API client, WebSocket manager, utilities |

### Key Subsystems

1. **Terminal System** (`terminal.py`)
   - PTY relay via `socat` subprocesses — survive server restarts
   - Session metadata in `~/.rdc/terminal_sessions.json`
   - WebSocket relay at `/terminals/{id}/ws`
   - MCP approval detection for cursor-agent

2. **Task/Worker System** (`worker.py`)
   - API creates tasks in SQLite (`status: pending`)
   - Worker polls DB, claims tasks, spawns agent subprocesses
   - Agent monitoring, completion handling, orphan recovery
   - Worker heartbeat for liveness detection

3. **State Synchronization**
   - Server → WebSocket `/ws/state` → `state-store.ts` → Zustand selectors
   - All connected clients get real-time state updates

4. **Browser Preview** (`browser.py`)
   - Browserless Docker containers for shared browser sessions
   - CDP-based context capture (screenshot + accessibility tree)
   - rrweb session recording with chunked storage
   - PinchTab integration for connecting to existing browser tabs

5. **Knowledge Base** (`knowledge.py` + `frontend/src/features/wiki/`)
   - Indexes `.ai/` directories from registered projects
   - Full-page route at `/kb` with tree nav, search, markdown viewer
   - Create/edit docs via REST API

6. **AI Orchestrator** (`intent.py`)
   - Natural language → structured actions (open terminal, create task, etc.)
   - Used by chat panels and voice commands

## Database

Single SQLite database at `~/.rdc/data/rdc.db` (consolidated from previous multi-DB setup).

Schema managed by SQL migrations in `src/remote_dev_ctrl/server/db/migrations/rdc/`.
Migrations auto-run on server startup via `db/migrate.py`.

Key tables: `projects`, `tasks`, `events`, `workers`, `browser_sessions`, `contexts`, `agent_registry`, `process_configs`, `settings`.

## CLI Commands

```bash
rdc server start [--reload]    # Start FastAPI server
rdc server start -d            # Daemonized
rdc add <name> <path>          # Register a project
rdc list                       # List projects
rdc remove <name>              # Remove a project
```

## Development

```bash
# Frontend build (must pass with zero errors)
cd frontend && pnpm run build

# Frontend dev server (proxies API to :8420)
cd frontend && pnpm dev

# Server with hot-reload
rdc server start --reload

# Type-check only
cd frontend && npx tsc --noEmit
```

## File Locations

- Main server: `src/remote_dev_ctrl/server/`
- Frontend: `frontend/src/`
- Database: `~/.rdc/data/rdc.db`
- Migrations: `src/remote_dev_ctrl/server/db/migrations/rdc/`
- Config: `~/.rdc/config.yml`
- Agent logs: `~/.rdc/logs/agents/`
- Process logs: `~/.rdc/logs/processes/`
- Browser recordings: `~/.rdc/recordings/`
- Context snapshots: `~/.rdc/contexts/`
