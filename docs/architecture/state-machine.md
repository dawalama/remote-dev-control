# State Machine Architecture

This document describes the state machine architecture for RDC, which provides centralized state management across the server and all connected clients.

## Overview

RDC uses a dual state machine architecture:
- **Server**: Python `transitions` library for canonical shared state
- **Client**: XState v5 for complex UI workflows (voice, auth)
- **Client**: Redux-style reducer for UI-specific state (tabs, modals)

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                        CLIENT                                │
│  ┌───────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │ UI Actions    │───▶│ sendState    │───▶│ WebSocket    │──┼──▶
│  │ (buttons,     │    │ Event()      │    │ /ws/state    │  │
│  │  voice, etc)  │    └──────────────┘    └──────────────┘  │
│  └───────────────┘                               │           │
│         ▲                                        │           │
│         │              ┌──────────────┐          │           │
│         └──────────────│ Reducer +    │◀─────────┘           │
│                        │ XState sync  │ (state broadcasts)   │
│                        └──────────────┘                      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                        SERVER                                │
│  ┌───────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │ WebSocket     │───▶│ State Machine│───▶│ Process/Task │  │
│  │ Handler       │    │ (transitions)│    │ Managers     │  │
│  └───────────────┘    └──────────────┘    └──────────────┘  │
│         │                    │                               │
│         │                    ▼                               │
│         │              ┌──────────────┐                      │
│         └──────────────│ Broadcast    │ (to all clients)     │
│                        │ State Sync   │                      │
│                        └──────────────┘                      │
└─────────────────────────────────────────────────────────────┘
```

## Event Flow

### Client → Server
1. User triggers action (button click, voice command, etc.)
2. Client calls `sendStateEvent(eventType, data)`
3. Event sent via WebSocket to `/ws/state`
4. Server state machine processes event
5. Server calls appropriate manager (ProcessManager, TaskRepository, etc.)
6. Server broadcasts updated state to all clients
7. Server sends `event_result` back to originating client

### Server → Clients
1. State machine transitions or data changes
2. `_broadcast_state()` generates `StateSnapshot`
3. Snapshot sent to all subscribed WebSocket clients
4. Clients update local state (reducer + XState context)
5. UI re-renders with new state

## Server State Machine

**Location**: `src/remote_dev_ctrl/server/state_machine.py`

### States (ServerStates)
- `INITIALIZING` - Server starting up
- `READY` - Normal operation, accepting events
- `PROCESSING` - Handling an event
- `ERROR` - Error state (recoverable)
- `SHUTTING_DOWN` - Graceful shutdown

### Session States (per-client)
- `CONNECTED` - WebSocket connected
- `AUTHENTICATED` - Token validated
- `WORKING` - Actively processing
- `IDLE` - Waiting for input

### Event Handlers

| Event Type | Handler | Description |
|------------|---------|-------------|
| `session_connect` | `_handle_session_connect` | New client connected |
| `session_disconnect` | `_handle_session_disconnect` | Client disconnected |
| `select_project` | `_handle_select_project` | Project selection changed |
| `task_create` | `_handle_task_create` | Create new task |
| `task_start` | `_handle_task_start` | Start/assign task |
| `task_complete` | `_handle_task_complete` | Mark task complete |
| `task_fail` | `_handle_task_fail` | Mark task failed |
| `task_cancel` | `_handle_task_cancel` | Cancel task |
| `task_block` | `_handle_task_block` | Block task (needs review) |
| `task_review` | `_handle_task_review` | Approve/reject task |
| `task_retry` | `_handle_task_retry` | Retry failed task |
| `process_start` | `_handle_process_start` | Start a process |
| `process_stop` | `_handle_process_stop` | Stop a process |
| `terminal_open` | `_handle_terminal_open` | Open terminal for project |
| `terminal_close` | `_handle_terminal_close` | Close terminal |
| `preview_start` | `_handle_preview_start` | Start VNC preview |
| `preview_stop` | `_handle_preview_stop` | Stop VNC preview |
| `agent_spawn` | `_handle_agent_spawn` | Spawn agent for project |
| `agent_stop` | `_handle_agent_stop` | Stop running agent |
| `voice_command` | `_handle_voice_command` | Process voice command |

### State Snapshot

The `StateSnapshot` broadcast to clients includes:
```python
StateSnapshot(
    server_state: str,      # Current server state
    tasks: list[dict],      # All tasks
    processes: list[dict],  # All processes
    agents: list[dict],     # Active agents
    sessions: list[dict],   # Connected sessions
    queue_stats: dict,      # Task queue statistics
    timestamp: str,         # ISO timestamp
)
```

## Client State Machines

### Dashboard Machine (XState v5)

**Location**: `dashboard_state.py` - `dashboardMachine`

Handles:
- Authentication flow
- Project selection
- Terminal/preview management
- Server state synchronization

Key events:
- `AUTHENTICATE` / `LOGOUT`
- `SELECT_PROJECT`
- `OPEN_TERMINAL` / `CLOSE_TERMINAL`
- `SERVER_STATE_UPDATE` - Syncs server state to context

### Voice Machine (XState v5)

**Location**: `dashboard_state.py` - `voiceMachine`

States:
- `idle` - Not listening
- `requestingMic` - Getting microphone permission
- `connecting` - Connecting to Deepgram STT
- `streaming` - Actively transcribing
- `browserFallback` - Using Web Speech API

### UI Reducer

**Location**: `dashboard_state.py` - `reducer()`

Handles ephemeral UI state:
- Tab selection
- Modal open/close
- Loading states
- Local data cache

## Debug Tools

### Debug Page (`/debug`)

Features:
- Event timeline visualization
- Real-time state tree inspection
- Event sender for testing
- State machine diagram with current state

### State Diagram Endpoint

```
GET /debug/diagram
```

Returns Mermaid-format state diagram and current state.

## Migration Status

### Routed Through State Machine
- ✅ Process start/stop/restart
- ✅ Task create/start/cancel/complete/fail/block
- ✅ Task review (approve/reject)
- ✅ Task retry
- ✅ Project selection
- ✅ Terminal open/close
- ✅ Preview start/stop
- ✅ Agent spawn/stop
- ✅ Voice commands

### Still Using Direct API
- ⏳ Screenshot capture
- ⏳ Chat/messaging
- ⏳ Admin operations

### Future Work
- [ ] Add state persistence for recovery
- [ ] Add state machine versioning for migrations
- [ ] Consider moving UI reducer to XState for full unification
- [x] ~~Reconcile TaskQueue (file-based) with TaskRepository (SQLite)~~ — Done, all state is DB-backed

## Best Practices

### Adding New Events

1. Add handler in `state_machine.py`:
```python
async def _handle_my_event(self, event: MachineEvent) -> dict:
    # Process event
    return {"success": True, "data": result}
```

2. Update client to use `sendStateEvent()`:
```javascript
sendStateEvent('my_event', { param1: value1 });
```

3. Handle result in WebSocket `onmessage`:
```javascript
} else if (message.event === 'my_event' && message.result?.success) {
    notify('Action completed', 'success');
}
```

### Testing

Use the debug page (`/debug`) to:
1. Send test events manually
2. Observe state changes in real-time
3. Verify event flow in timeline
4. Check state machine diagram for current state
