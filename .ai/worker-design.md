# RDC Task Worker Architecture

## Overview

The worker is a background process that executes tasks by spawning agent subprocesses. It's decoupled from the API server so tasks survive API restarts.

## Architecture

```
┌─────────────────────────────────────────────┐
│                  SQLite DB                   │
│  tasks (queue) │ agents (state) │ workers    │
└────────────────┼────────────────┼────────────┘
     poll         │  update        │ heartbeat
┌────────────────▼────────────────▼────────────┐
│                 RDC Worker                    │
│  Task Poller → Agent Monitor → Health Report │
│         ↓                                    │
│    Agent Subprocesses (cursor-agent, etc.)   │
└──────────────────────────────────────────────┘
                    (independent)
┌──────────────────────────────────────────────┐
│              FastAPI Server                   │
│  POST /tasks (insert) │ WebSocket (broadcast)│
└──────────────────────────────────────────────┘
```

## How It Works

1. **API** creates tasks in DB (`status: pending`)
2. **Worker** polls DB, atomically claims next pending task
3. **Worker** spawns agent subprocess (cursor-agent or web-native)
4. **Worker** monitors subprocess for completion
5. **Worker** updates task status in DB (completed/failed)
6. **API** reads updated state, broadcasts via WebSocket

## Key Components

- `server/worker.py` — TaskWorker class with poll/spawn/monitor/heartbeat loops
- `server/agents/manager.py` — AgentManager for subprocess lifecycle
- `db/repositories.py` — TaskRepository with atomic claim/complete/fail operations

## Task Lifecycle

```
pending → claimed (by worker) → in_progress → completed/failed
                                    ↓
                              needs_review → approved/rejected
```

## CLI

```bash
rdc server start        # Server includes integrated worker
rdc server start -d     # Daemonized
```

## Recovery

- **API restarts**: Worker (integrated) restarts with server, recovers orphaned tasks
- **Agent crashes**: Worker detects via PID monitoring, marks task as failed
- **Orphan detection**: On startup, scans for tasks claimed but not running
