# Frontend Live Spec — RDC Command Center

> **Source of truth**: Reverse-engineered from the running code in `dashboard_state.py` (desktop), `mobile_page.py` (mobile), and `app.py` (API). Updated 2026-02-21.

## Architecture

| Route | Impl | Description |
|---|---|---|
| `/` | `frontend/` (React) | React SPA — **desktop layout** (catch-all) |
| `/mobile` | `frontend/` (React) | React SPA — **mobile layout** (lazy-loaded) |
| `/old` | `dashboard_state.py` | Legacy monolithic HTML SPA (XState + vanilla JS) |
| `/old/mobile` | `mobile_page.py` | Legacy mobile HTML SPA (vanilla JS) |

**Status**: React app is now the primary UI at `/`. Old UIs preserved at `/old` for reference.

### Stack (React rewrite)
- Vite + React 19 + TypeScript + Tailwind v4
- Zustand (stores), React Router v7
- shadcn/ui (Radix primitives)
- xterm.js v6 (@xterm/xterm + FitAddon, WebLinksAddon)

### Build (code-split)
| Chunk | Size | Contents |
|---|---|---|
| `index.js` | ~352KB | App shell, desktop layout, all desktop features |
| `xterm.js` | ~333KB | xterm.js + addons (loaded when terminal mounts) |
| `vendor.js` | ~47KB | React, React Router, Zustand |
| `mobile.js` | ~39KB | Mobile layout + cards + sheets (lazy-loaded) |

### Data layer
- **Primary**: `/ws/state` WebSocket — receives full `StateSnapshot` pushes
- **Secondary**: `/ws` general event bus — real-time activity events
- **REST**: Direct API calls for mutations and one-off data loads

### Stores (Zustand)
| Store | File | State |
|---|---|---|
| `state-store` | `stores/state-store.ts` | WS connection + client registration, tasks, processes, agents, terminals, collections, phone, queueStats |
| `auth-store` | `stores/auth-store.ts` | Token, login/logout, 401 auto-logout |
| `project-store` | `stores/project-store.ts` | Projects list, collections, currentProject/Collection, scaffold/delete |
| `ui-store` | `stores/ui-store.ts` | Tab, sidebar, bottomPanel, chat, commandPalette, addProject, theme, toasts, selectedTaskId |
| `terminal-store` | `stores/terminal-store.ts` | activeProject, mode, connectedProjects, spawn/kill/restart |
| `process-store` | `stores/process-store.ts` | loadProcesses, start/stop/restart |
| `logs-store` | `stores/logs-store.ts` | Multi-pane logs (WS streaming), pause/resume, maximize, height |

---

## StateSnapshot (from `/ws/state`)

```typescript
interface StateSnapshot {
  server_state: string       // "initializing" | "ready" | "processing" | "error" | "shutting_down"
  tasks: Task[]
  processes: ProcessState[]
  agents: AgentInfo[]
  sessions: WsSession[]
  terminals: Terminal[]      // [{id, project, status, pid, waiting_for_input}]
  collections: Collection[]
  phone: PhoneState          // {configured, active, call_sid, turn_count, duration, ...}
  queue_stats: QueueStats    // {total, pending, in_progress, completed, failed, by_project}
  timestamp: string
}
```

### WS Protocol
- **Client sends**: `{type, data, client_id?, client_name?}` events
  - `register` (sent on connect with stable `client_id` from localStorage), `select_project`, `task_start`, `task_cancel`, `task_retry`, `task_create`, `task_review`, `process_start`, `process_stop`, `voice_command`, `get_state`
- **Server sends**:
  - `{type: "state", data: StateSnapshot}` — on every state change
  - `{type: "event_result", event, result}` — success/error feedback
  - `{type: "phone_action", actions[]}`, `{type: "phone_paired"}`, `{type: "phone_unpaired"}`

---

## Desktop Layout (`/`)

### React Implementation Status

| Feature | Status | Notes |
|---|---|---|
| 2-column IDE layout | Done | col-span-2 terminal + col-span-1 sidebar |
| Embedded terminal (xterm.js) | Done | WS binary, auto-reconnect, ResizeObserver |
| Terminal lifecycle | Done | Spawn, resume, restart, kill, disconnect |
| Right sidebar (4 tabs) | Done | Activity, Processes, System, Tasks |
| Activity tab | Done | Paginated + WS real-time, duplicate-collapsed |
| Processes tab | Done | Full lifecycle, sync, logs, fix with AI |
| Tasks tab | Done | CRUD, modals (Add/Retry/Continue), review workflow |
| System tab | Done | Workers, server stats, restart, logs, shortcut hints |
| Floating logs panel | Done | Multi-tab, WS streaming, resize, pause/resume |
| Command bar | Done | Logs pill, Phone, Voice, Chat, Theme picker |
| Chat FAB | Done | Floating panel, orchestrator API |
| Keyboard shortcuts | Done | ⌘K (search), ⌘T (terminal), ⌘/ (chat), Escape |
| Command palette (⌘K) | Done | Project search/filter overlay |
| Add Project modal | Done | Create New (scaffold) + Connect Existing (git URL) |
| Phone FAB | Done | Call/hangup via POST /voice/call |
| Bottom panel (Contexts) | Done | Browser context thumbnails, delete, click to view |
| Pending reviews banner | Done | Yellow banner, approve/reject |
| Port assignments modal | Done | Conflict detection, editable ports, auto-assign, save |
| Browser preview modal | Done | Fullscreen iframe + side chat panel + capture |
| Screenshots overlay | Done | 2-col grid, capture, fullscreen view, copy path, delete |
| Context viewer modal | Done | Full screenshot + a11y tree + Send to Agent + Delete |
| Project settings modal | Done | Terminal command config + danger zone (disconnect) |
| Chat AI action execution | Done | Dispatches show_tab, select_project, start/stop process, create_task, spawn_agent, etc. |
| Process tab: Preview/Capture | Done | Start browser session, capture context from process |
| Voice FAB | Done | Deepgram STT via `/stt/stream`, local command parsing + orchestrator fallback |
| Terminal PIP/minimized modes | Done | Embedded, fullscreen, PIP (draggable), minimized |
| Client ID pairing | Done | Stable client IDs in localStorage for phone/device pairing |

### Header
- "REMOTE CTRL" title
- `⌘K` button → opens command palette
- Connection dot (green/red) + "Connected"/"Disconnected"
- Logout button

### Project Bar
- Horizontal pill buttons: "All" + each project name (active = blue)
- "+" button → Add Project modal (Create New / Connect Existing tabs)

### Left Column (col-span-2)

#### Embedded Terminal (top)
- xterm.js per project (Catppuccin theme, 13px Menlo, 10k scrollback)
- WS: `/terminals/{id}/ws` (binary ArrayBuffer)
- Empty state: "New Session" / "Resume Last" buttons
- Header: project name, Restart/Kill/Disconnect buttons
- ResizeObserver → auto-fit + WS resize messages

#### Bottom Panel — Contexts
- Collapsible tabbed panel below terminal
- 2-column grid of browser context snapshots
- Each: thumbnail, title/URL/ID, timestamp, Delete button
- Loads from `GET /context?project=&limit=20`

### Right Column (col-span-1) — Tabbed Sidebar

#### Tab: Activity
- Paginated from `GET /activity?limit=30&before=`
- Real-time WS updates from `/ws` event bus
- Color-coded dots, duplicate-collapsed, "Load older"

#### Tab: Processes
- Ports button → Port Assignments modal
- Sync button → `POST /projects/{project}/detect-processes?force_rediscover=true`
- Per process: status dot, name, port, PID, command
- Actions: Start/Stop/Restart, Logs (→ logs panel WS stream), Open (if port), Preview (starts browser session), Capture (context), Fix with AI

#### Tab: System
- Worker stats (active/pending/in_progress), health dot
- Server: state, uptime, memory, restart button
- Server Logs button → logs panel WS `/ws/logs`
- Screenshots button → Screenshots overlay
- Project Settings button → Project Settings modal (if project selected)
- Keyboard shortcut hints

#### Tab: Tasks (badge: running count)
- "+ Add Task" button → inline modal
- Today/Past sections, sorted newest first
- Per task: status icon, project, description, error
- Actions by status: Run, Cancel, Stop, Force Stop, Retry, Edit & Retry, Chain, View Output, Continue, Approve, Reject
- Inline modals: Add Task, Retry, Continue

### Floating Elements
- **LogsPanel**: Multi-tab bottom panel, process log WS streaming, task output, system logs, resize handle, pause/resume
- **CommandBar**: Fixed bottom (48px, z-40), logs pill + Phone/Voice/Chat FABs + theme picker
- **ChatFAB**: 384×500px floating panel, orchestrator API, auto-scroll
- **CommandPalette**: Centered overlay, project search with keyboard nav

### Keyboard Shortcuts
| Shortcut | Action |
|---|---|
| `⌘K` / `Ctrl+K` | Open command palette (project search) |
| `⌘T` / `Ctrl+T` | Spawn new terminal for current project |
| `⌘/` / `Ctrl+/` | Toggle chat panel |
| `Escape` | Close command palette |

---

## Mobile Layout (`/mobile`)

### React Implementation Status

| Feature | Status | Notes |
|---|---|---|
| Status bar | Done | Project name + chevron, server state, connection dot, hamburger |
| Attention card | Done | Orange border, waiting_for_input terminals |
| Quick actions | Done | + Task, + Terminal buttons |
| Terminals card | Done | Status dot, project, kill, tap to open overlay |
| Processes card | Done | Start/stop/restart, sync, port badge, preview, logs. Always visible (empty state). |
| Tasks card | Done | Collapsible, sorted, expandable detail, per-status actions, Edit & Retry, Continue. Always visible. |
| Command bar | Done | Text input + voice + phone, orchestrator integration |
| Terminal overlay | Done | xterm.js, virtual keys, restart/kill |
| Project sheet | Done | Collection filter + project list |
| Hamburger sheet | Done | Menu items, theme picker, paired devices, project/system settings |
| Create task sheet | Done | Description input, project auto-filled |
| Activity sheet | Done | Paginated, load more |
| Contexts card | Done | Collapsible, thumbnails, tap to view, delete. Always visible. |
| Context viewer overlay | Done | Shared ContextViewerModal (screenshot + a11y tree) |
| Preview overlay | Done | Fullscreen iframe + capture/open/stop actions, resume pill |
| Voice (Deepgram STT) | Done | useVoice hook, auto-submit after 600ms, local command parsing |
| Phone integration | Done | Call/hangup via POST /voice/call with client_id pairing |
| Add project sheet | Done | Create new + connect existing tabs |
| Paired devices sheet | Done | Lists sessions from GET /auth/sessions, revoke capability |
| Project settings modal | Done | Shared with desktop (terminal command config + danger zone) |
| System settings modal | Done | Shared with desktop |
| Process log overlay | Done | Streaming logs for individual processes |

### Status Bar (sticky top)
- Left: tappable → project sheet (collection name + project name + ▼)
- Right: server state text + connection dot + hamburger button

### Cards (scrollable)
- **Attention**: Orange left border, pulsing dot, "Open" per waiting terminal
- **Quick Actions**: "+ Task" (blue) + "+ Terminal" (outline)
- **Terminals**: Status dot, project name, status, kill ×, tap → terminal overlay
- **Processes**: Status dot, name, port badge, command, Start/Stop/Restart
- **Tasks**: Collapsible, count badge, max 5 + "N more", expandable detail, status-based actions

### Terminal Overlay (z-100, fullscreen)
- xterm.js (reuses TerminalView component)
- Header: back, "Terminal", Restart/Kill
- Virtual key bar: ↑↓←→, Enter, Tab, Esc, C-c, y, n, Paste
- Second WS connection for virtual key input

### Bottom Sheets (z-150)
- **Project Selector**: Collection pills + project list
- **Hamburger Menu**: Add Project, Create Task, Activity Log, Project Settings, System Settings, Paired Devices, Theme picker (Standard/Modern/Brutalist)
- **Create Task**: Description textarea + Create button
- **Activity Log**: Paginated events, load more
- **Add Project**: Create New (scaffold) + Connect Existing tabs
- **Paired Devices**: Lists paired sessions, revoke capability

### Command Bar (fixed bottom, z-40)
- Text input → orchestrator API (with local command parsing for instant execution)
- Voice button: Deepgram STT via `/stt/stream`, auto-submit after 600ms
- Phone button: Call/hangup via `/voice/call` with client_id pairing

---

## Shared Modals (implemented)

All modals from the old dashboard are now in the React app:

- **Port Assignments**: `GET /ports`, conflict detection, editable ports, Auto-assign, Save/Cancel
- **Browser Preview**: Fullscreen iframe, side chat panel, Capture Context, Stop button
- **Project Settings**: Terminal Command input, Danger Zone (disconnect), Save
- **System Settings**: Server stats, restart, LLM config
- **Screenshots Overlay**: 2-column grid, Upload/Capture, per-screenshot: view/insert/copy/delete
- **Context Viewer**: Full screenshot + a11y tree, Send to Agent, Delete
- **Collections Manager**: Create/edit/delete collections

---

## WebSocket Endpoints

| Endpoint | Auth | Binary | Purpose |
|---|---|---|---|
| `/ws/state?token=` | Yes | No | Primary state sync, bidirectional events |
| `/ws` | No | No | Legacy event broadcast (real-time activity) |
| `/ws/logs` | ? | No | Live server log streaming |
| `/ws/process-logs/{id}` | ? | No | Per-process log streaming |
| `/terminals/{id}/ws?token=` | Yes | Yes | Terminal PTY I/O |
| `/stt/stream?token=` | Yes | Yes | Deepgram STT audio stream |

---

## API Endpoints (complete)

### Projects & Collections
| Method | Path | Description |
|---|---|---|
| GET | `/projects` | List all projects |
| POST | `/projects` | Register existing project |
| POST | `/projects/scaffold` | Scaffold new project |
| GET | `/projects/{name}` | Get project details |
| PATCH | `/projects/{name}` | Update project metadata |
| PATCH | `/projects/{name}/config` | Update project config |
| DELETE | `/projects/{name}` | Disconnect project |
| GET | `/projects/{name}/processes` | Get process configs |
| PUT | `/projects/{name}/processes` | Save process overrides |
| POST | `/projects/{name}/move` | Move project |
| POST | `/projects/{name}/detect-processes` | Auto-detect processes |
| GET | `/projects/{name}/agent-sessions` | List agent sessions |
| GET | `/browse` | Browse filesystem |
| GET | `/collections` | List collections |
| POST | `/collections` | Create collection |
| PATCH | `/collections/{id}` | Update collection |
| DELETE | `/collections/{id}` | Delete collection |

### Tasks
| Method | Path | Description |
|---|---|---|
| GET | `/tasks` | List tasks |
| POST | `/tasks` | Create task |
| GET | `/tasks/{id}` | Get task |
| PATCH | `/tasks/{id}` | Update task |
| POST | `/tasks/{id}/run` | Queue for execution |
| POST | `/tasks/{id}/cancel` | Cancel |
| POST | `/tasks/{id}/retry` | Retry |
| POST | `/tasks/{id}/review` | Approve/reject |
| GET | `/tasks/{id}/output` | Get output |
| GET | `/tasks/pending-review` | Pending reviews |
| GET | `/tasks/stats` | Queue statistics |
| POST | `/tasks/chain` | Create chained tasks |

### Agents
| Method | Path | Description |
|---|---|---|
| GET | `/agents` | List all agents |
| POST | `/agents/spawn` | Spawn agent |
| GET | `/agents/{project}` | Get agent status |
| POST | `/agents/{project}/stop` | Stop agent |
| POST | `/agents/{project}/retry` | Retry agent |
| GET | `/agents/{project}/logs` | Agent logs |
| POST | `/agents/{project}/assign` | Assign task |

### Processes & Ports
| Method | Path | Description |
|---|---|---|
| GET | `/processes` | List processes |
| POST | `/processes/register` | Register process |
| POST | `/processes/{id}/start` | Start |
| POST | `/processes/{id}/stop` | Stop |
| POST | `/processes/{id}/restart` | Restart |
| POST | `/processes/{id}/attach` | Attach to PID |
| POST | `/processes/{id}/create-fix-task` | Create AI fix task |
| GET | `/processes/{id}/logs` | Process logs |
| GET | `/ports` | List port assignments |
| POST | `/ports/assign` | Auto-assign |
| POST | `/ports/set` | Set port |
| DELETE | `/ports/{project}/{service}` | Release port |
| GET | `/ports/{port}/info` | Port info |
| POST | `/ports/{port}/kill` | Kill on port |

### Terminals
| Method | Path | Description |
|---|---|---|
| GET | `/terminals` | List sessions |
| POST | `/terminals` | Create |
| GET | `/terminals/{id}` | Get session |
| DELETE | `/terminals/{id}` | Destroy |
| POST | `/terminals/{id}/resize` | Resize |
| POST | `/terminals/{id}/restart` | Restart |

### Browser Sessions
| Method | Path | Description |
|---|---|---|
| POST | `/browser/start/{process_id}` | Start session |
| GET | `/browser/sessions` | List sessions |
| GET | `/browser/sessions/{id}` | Get session |
| POST | `/browser/sessions/{id}/stop` | Stop session |

### Context Capture
| Method | Path | Description |
|---|---|---|
| POST | `/context/capture` | Capture context |
| GET | `/context` | List contexts |
| GET | `/context/{id}` | Get context |
| GET | `/context/{id}/screenshot` | Get screenshot |
| DELETE | `/context/{id}` | Delete context |
| POST | `/context/upload` | Upload context |

### Orchestrator / Chat
| Method | Path | Description |
|---|---|---|
| POST | `/orchestrator` | NL command |
| POST | `/chat/message` | Chat with context |
| GET | `/config/nanobot` | Get LLM config |
| PATCH | `/config/nanobot` | Update LLM config |

### Voice / Phone / TTS
| Method | Path | Description |
|---|---|---|
| POST | `/voice/call` | Initiate call |
| POST | `/voice/hangup` | Hang up |
| GET | `/voice/call/status` | Call status |
| POST | `/voice/pair` | Pair client |
| POST | `/voice/unpair` | Unpair |
| POST | `/tts/speak` | Text to speech |

### Admin / System
| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check |
| GET | `/admin/logs` | Server log tail |
| GET | `/admin/status` | Server stats |
| POST | `/admin/restart` | Graceful reload |
| GET | `/activity` | Paginated activity log |
| GET | `/tokens` | List API tokens |
| POST | `/tokens` | Create token |
| DELETE | `/tokens/{id}` | Revoke token |
| GET | `/auth/sessions` | List paired device sessions |
| DELETE | `/auth/sessions/{id}` | Revoke device session |

---

## Hooks

### `useOrchestrator` (`hooks/use-orchestrator.ts`)
Shared hook for sending NL commands and executing returned client-side actions.
- **Local command parsing**: Regex patterns for instant execution of common voice commands (open terminal, select project, show tab, refresh) — no server round-trip
- **Server fallback**: Sends to `POST /orchestrator` for complex commands, executes returned actions
- **Actions handled**: `select_project`, `select_collection`, `open_terminal`, `show_tab`, `navigate`, `start/stop_process`, `create_task`, `create_project`, `start_preview`, `show_logs`
- Used by: `CommandBar` (desktop), `MobileCommandBar` (mobile), `ChatFAB` (has its own inline action handler)

### `useVoice` (`hooks/use-voice.ts`)
Deepgram STT via WebSocket with mic capture.
- Callbacks stored in refs to avoid stale closures in the WS `onmessage` handler
- Returns: `{ listening, transcript, interim, error, start, stop, toggle }`

### `useVoice` + `useOrchestrator` flow
1. Mic audio → WS `/stt/stream` → Deepgram → final transcript
2. `onFinal` callback → `orchestrator.send(text)`
3. `send()` tries local regex patterns first (instant), falls back to server orchestrator
4. Server response actions executed on the client

---

## Remaining Work

React app is now the primary UI at `/`. Old UIs at `/old` for reference.

### Final cleanup
1. Remove `dashboard_state.py` and `mobile_page.py` (once confident in parity)
2. Remove `/old` routes from `app.py`
3. Optional: TTS playback (server-side via Deepgram Aura, not yet wired to frontend)
4. Optional: File browser for "Connect Existing" project tab (currently text input only)
