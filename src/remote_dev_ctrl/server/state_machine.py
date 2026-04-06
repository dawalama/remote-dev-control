"""Server-side state machine for RDC Command Center.

This is the canonical state machine that all clients (web, mobile, TG, voice) sync with.
It manages shared state: tasks, processes, agents, sessions.
Client-specific UI state lives in the browser.

Uses the `transitions` library for formal FSM with guards and callbacks.
"""

import asyncio
import json
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Awaitable, Optional
from dataclasses import dataclass, field

from pydantic import BaseModel
from transitions import Machine
from transitions.extensions import AsyncMachine

from .events.bus import get_event_bus, Event, EventType
from .processes import get_process_manager
from .event_store import log_event


# =============================================================================
# STATE DEFINITIONS
# =============================================================================

class ServerStates(str, Enum):
    """Top-level server states."""
    INITIALIZING = "initializing"
    READY = "ready"
    PROCESSING = "processing"
    ERROR = "error"
    SHUTTING_DOWN = "shutting_down"


class SessionStates(str, Enum):
    """Per-session states."""
    CONNECTED = "connected"
    AUTHENTICATED = "authenticated"
    WORKING = "working"  # Active in terminal or preview
    IDLE = "idle"


# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class Session:
    """A connected client session with its own state machine."""
    id: str
    state: str = SessionStates.CONNECTED.value
    project: Optional[str] = None
    terminal_project: Optional[str] = None
    preview_process: Optional[str] = None
    browser_session_id: Optional[str] = None
    connected_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    user_agent: Optional[str] = None
    client_id: Optional[str] = None
    client_name: Optional[str] = None
    
    def __post_init__(self):
        # Each session has its own state machine
        self._machine = Machine(
            model=self,
            states=[s.value for s in SessionStates],
            initial=SessionStates.CONNECTED.value,
            auto_transitions=False,
        )
        
        # Session transitions
        self._machine.add_transition('authenticate', SessionStates.CONNECTED.value, SessionStates.AUTHENTICATED.value)
        self._machine.add_transition('start_work', SessionStates.AUTHENTICATED.value, SessionStates.WORKING.value)
        self._machine.add_transition('stop_work', SessionStates.WORKING.value, SessionStates.AUTHENTICATED.value)
        self._machine.add_transition('go_idle', [SessionStates.AUTHENTICATED.value, SessionStates.WORKING.value], SessionStates.IDLE.value)
        self._machine.add_transition('resume', SessionStates.IDLE.value, SessionStates.AUTHENTICATED.value)
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "state": self.state,
            "project": self.project,
            "terminal_project": self.terminal_project,
            "preview_process": self.preview_process,
            "browser_session_id": self.browser_session_id,
            "connected_at": self.connected_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "client_id": self.client_id,
            "client_name": self.client_name,
        }


class MachineEvent(BaseModel):
    """An event that can be sent to the state machine."""
    type: str
    session_id: Optional[str] = None
    data: dict = {}
    

class StateSnapshot(BaseModel):
    """A snapshot of the current state for syncing to clients."""
    server_state: str
    tasks: list[dict]
    processes: list[dict]
    actions: list[dict] = []
    agents: list[dict]
    sessions: list[dict]
    terminals: list[dict] = []
    collections: list[dict] = []
    channels: list[dict] = []
    terminal_channels: dict[str, list[str]] = {}  # terminal_id -> [channel_ids]
    phone: dict = {}
    conversation: dict = {}
    queue_stats: dict
    timestamp: str


# =============================================================================
# SERVER STATE MACHINE
# =============================================================================

class ServerStateMachine:
    """
    The canonical server state machine using `transitions` library.
    
    States:
    - initializing: Starting up, loading config
    - ready: Accepting events, normal operation
    - processing: Handling a batch of events
    - error: Recoverable error state
    - shutting_down: Graceful shutdown
    
    Features:
    - Formal state transitions with guards
    - Callbacks on state enter/exit
    - Per-session nested state machines
    - Event logging to DuckDB
    - State broadcasting to clients
    """
    
    # Define transitions: (trigger, source, dest, conditions, unless, before, after)
    TRANSITIONS = [
        # Startup
        {'trigger': 'initialize', 'source': ServerStates.INITIALIZING.value, 'dest': ServerStates.READY.value, 'after': '_on_ready'},
        
        # Normal operation
        {'trigger': 'process_event', 'source': ServerStates.READY.value, 'dest': ServerStates.PROCESSING.value},
        {'trigger': 'event_done', 'source': ServerStates.PROCESSING.value, 'dest': ServerStates.READY.value, 'after': '_broadcast_state_sync'},
        
        # Error handling
        {'trigger': 'error', 'source': '*', 'dest': ServerStates.ERROR.value, 'before': '_log_error'},
        {'trigger': 'recover', 'source': ServerStates.ERROR.value, 'dest': ServerStates.READY.value},
        
        # Shutdown
        {'trigger': 'shutdown', 'source': [ServerStates.READY.value, ServerStates.ERROR.value], 'dest': ServerStates.SHUTTING_DOWN.value, 'before': '_on_shutdown'},
    ]
    
    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._client_websockets: dict[str, Any] = {}  # client_id → WebSocket
        self._event_bus = get_event_bus()
        self._subscribers: list[Callable[[StateSnapshot], Awaitable[None]]] = []
        self._event_subscribers: list[Callable[[dict], Awaitable[None]]] = []
        self._lock = asyncio.Lock()
        self._last_error: Optional[str] = None

        # Initialize the state machine
        self._machine = Machine(
            model=self,
            states=[s.value for s in ServerStates],
            transitions=self.TRANSITIONS,
            initial=ServerStates.INITIALIZING.value,
            auto_transitions=False,
            send_event=True,  # Pass EventData to callbacks
        )
    
    # =========================================================================
    # STATE CALLBACKS
    # =========================================================================
    
    def _on_ready(self, event_data=None):
        """Called when server enters ready state."""
        print("[StateMachine] Server ready")
        log_event("server_ready", direction="system", source="server")
    
    def _on_shutdown(self, event_data=None):
        """Called when server begins shutdown."""
        print("[StateMachine] Server shutting down")
        log_event("server_shutdown", direction="system", source="server")
    
    def _log_error(self, event_data=None):
        """Log error before entering error state."""
        import traceback
        error_msg = getattr(event_data, 'kwargs', {}).get('error', 'Unknown error') if event_data else 'Unknown error'
        self._last_error = str(error_msg)
        print(f"[StateMachine] Error: {self._last_error}")
        if "recursion" in str(error_msg).lower():
            traceback.print_exc()
        log_event("server_error", direction="system", data={"error": self._last_error}, source="server")
    
    def _broadcast_state_sync(self, event_data=None):
        """Sync wrapper for broadcast (transitions callbacks are sync)."""
        # Schedule async broadcast
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._broadcast_state())
        except RuntimeError:
            pass  # No event loop running
    
    # =========================================================================
    # PUBLIC API
    # =========================================================================
    
    @property
    def current_state(self) -> str:
        return self.state
    
    def subscribe(self, callback: Callable[[StateSnapshot], Awaitable[None]]) -> Callable:
        """Subscribe to state changes."""
        self._subscribers.append(callback)
        return lambda: self._subscribers.remove(callback)
    
    def subscribe_events(self, callback: Callable[[dict], Awaitable[None]]) -> Callable:
        """Subscribe to all events (for debug/monitoring)."""
        self._event_subscribers.append(callback)
        return lambda: self._event_subscribers.remove(callback)

    # =========================================================================
    # CLIENT WEBSOCKET REGISTRY
    # =========================================================================

    def register_client_ws(self, client_id: str, websocket, session_id: str = None, client_name: str = None):
        """Register a named client WebSocket for targeted messaging."""
        self._client_websockets[client_id] = {
            "ws": websocket,
            "session_id": session_id,
            "client_name": client_name or client_id,
        }
        print(f"[StateMachine] Client registered: {client_id} (name={client_name})")

    def unregister_client_ws(self, client_id: str):
        """Remove a client WebSocket registration."""
        if client_id and client_id in self._client_websockets:
            del self._client_websockets[client_id]
            print(f"[StateMachine] Client unregistered: {client_id}")

    async def send_to_client(self, client_id: str, message: dict) -> bool:
        """Send a targeted message to a specific client. Returns True on success."""
        entry = self._client_websockets.get(client_id)
        if not entry:
            return False
        try:
            await entry["ws"].send_json(message)
            return True
        except Exception as e:
            print(f"[StateMachine] Failed to send to client {client_id}: {e}")
            return False

    def get_connected_clients(self) -> list[dict]:
        """Return list of connected client identities."""
        result = []
        for cid, entry in self._client_websockets.items():
            result.append({
                "client_id": cid,
                "client_name": entry.get("client_name", cid),
                "session_id": entry.get("session_id"),
            })
        return result

    async def _broadcast_event(self, event_type: str, data: dict, direction: str = "system", session_id: str = None) -> None:
        """Broadcast event to all event subscribers."""
        # Extract client identity from data if present
        client_id = data.get("_client_id") if data else None
        client_name = data.get("_client_name") if data else None
        
        # Clean internal fields from data for broadcast
        clean_data = {k: v for k, v in (data or {}).items() if not k.startswith("_")}
        
        event_msg = {
            "type": "event",
            "event_type": event_type,
            "data": clean_data,
            "direction": direction,
            "session_id": session_id,
            "client_id": client_id,
            "client_name": client_name,
            "timestamp": datetime.now().isoformat(),
        }
        for callback in self._event_subscribers:
            try:
                await callback(event_msg)
            except Exception as e:
                print(f"[StateMachine] Error broadcasting event: {e}")
    
    async def send(self, event: MachineEvent) -> dict:
        """
        Send an event to the state machine.
        Returns the result of processing the event.
        """
        async with self._lock:
            # Extract client info
            client_id = event.data.get("_client_id") if event.data else None
            client_name = event.data.get("_client_name") if event.data else None
            
            # Update session with client info if available
            if event.session_id and event.session_id in self._sessions:
                session = self._sessions[event.session_id]
                if client_id:
                    session.client_id = client_id
                if client_name:
                    session.client_name = client_name
                session.last_activity = datetime.now()
            
            # Log incoming event
            log_event(
                event_type=event.type,
                direction="received",
                data=event.data,
                session_id=event.session_id,
                client_id=client_id,
                client_name=client_name,
                project=event.data.get("project") if event.data else None,
                source="client",
            )
            
            # Broadcast to all event subscribers (for debug page)
            await self._broadcast_event(
                event.type, 
                event.data or {}, 
                direction="received", 
                session_id=event.session_id
            )
            
            # Auto-recover from error state
            if self.state == ServerStates.ERROR.value:
                try:
                    self.recover()
                except Exception:
                    pass

            # Transition to processing state if ready
            if self.state == ServerStates.READY.value:
                self.process_event()

            try:
                result = await self._handle_event(event)

                # Broadcast result
                await self._broadcast_event(
                    f"{event.type}_result",
                    result,
                    direction="sent",
                    session_id=event.session_id
                )

                # Return to ready state
                if self.state == ServerStates.PROCESSING.value:
                    self.event_done()

                return result
            except RecursionError:
                import traceback
                traceback.print_exc()
                # Force back to ready without triggering transitions
                self.state = ServerStates.READY.value
                return {"error": "recursion depth exceeded"}
            except Exception as e:
                try:
                    self.error(error=str(e))
                except Exception:
                    # If error transition itself fails, force state back
                    self.state = ServerStates.READY.value
                return {"error": str(e)}
    
    async def _handle_event(self, event: MachineEvent) -> dict:
        """Route event to appropriate handler."""
        handler_name = f"_handle_{event.type.lower()}"
        handler = getattr(self, handler_name, None)
        
        if handler:
            return await handler(event)
        
        return {"error": f"Unknown event type: {event.type}"}
    
    # =========================================================================
    # SESSION HANDLERS
    # =========================================================================
    
    async def _handle_session_connect(self, event: MachineEvent) -> dict:
        """Handle new session connection."""
        session = Session(
            id=event.session_id,
            user_agent=event.data.get("user_agent"),
        )
        self._sessions[session.id] = session
        return {"session": session.to_dict()}
    
    async def _handle_session_disconnect(self, event: MachineEvent) -> dict:
        """Handle session disconnection."""
        if event.session_id in self._sessions:
            del self._sessions[event.session_id]
        return {"disconnected": event.session_id}
    
    async def _handle_session_authenticate(self, event: MachineEvent) -> dict:
        """Handle session authentication."""
        session = self._sessions.get(event.session_id)
        if session:
            try:
                session.authenticate()  # Trigger session state transition
                return {"authenticated": True}
            except Exception as e:
                return {"error": f"Cannot authenticate: {e}"}
        return {"error": "Session not found"}
    
    async def _handle_select_project(self, event: MachineEvent) -> dict:
        """Handle project selection for a session."""
        session = self._sessions.get(event.session_id)
        if session:
            session.project = event.data.get("project")
            session.last_activity = datetime.now()
            return {"project": session.project}
        return {"error": "Session not found"}
    
    # =========================================================================
    # TASK HANDLERS
    # =========================================================================
    
    async def _handle_task_create(self, event: MachineEvent) -> dict:
        """Create a new task."""
        from .db.repositories import get_task_repo
        from .db.models import TaskPriority as DBTaskPriority
        
        data = event.data
        task_repo = get_task_repo()
        
        try:
            priority = DBTaskPriority(data.get("priority", "normal"))
        except ValueError:
            priority = DBTaskPriority.NORMAL
        
        from .db.repositories import resolve_project_id
        project_id = resolve_project_id(data["project"]) or data["project"]
        task = task_repo.create(
            project_id=project_id,
            description=data["description"],
            priority=priority,
        )
        
        await self._event_bus.publish(Event(
            type=EventType.TASK_CREATED,
            project=task.project,
            data={"task_id": task.id, "description": task.description},
        ))
        
        log_event("task_created", direction="sent", data={"task_id": task.id}, project=task.project, source="server")
        return {"task": task.model_dump(mode="json")}
    
    async def _handle_task_start(self, event: MachineEvent) -> dict:
        """Start a task (assign to agent)."""
        from .db.repositories import get_task_repo
        
        task_id = event.data["task_id"]
        agent = event.data.get("agent")
        task_repo = get_task_repo()
        
        task = task_repo.get(task_id)
        if not task:
            return {"error": "Task not found"}
        
        task = task_repo.start(task_id, agent)
        
        await self._event_bus.publish(Event(
            type=EventType.TASK_STARTED,
            project=task.project,
            data={"task_id": task.id},
        ))
        
        return {"task": task.model_dump(mode="json")}
    
    async def _handle_task_complete(self, event: MachineEvent) -> dict:
        """Complete a task."""
        from .db.repositories import get_task_repo
        
        task_id = event.data["task_id"]
        result = event.data.get("result")
        task_repo = get_task_repo()
        
        task = task_repo.complete(task_id, result)
        if not task:
            return {"error": "Task not found"}
        
        await self._event_bus.publish(Event(
            type=EventType.TASK_COMPLETED,
            project=task.project,
            data={"task_id": task.id, "result": result},
        ))
        
        return {"task": task.model_dump(mode="json")}
    
    async def _handle_task_fail(self, event: MachineEvent) -> dict:
        """Fail a task."""
        from .db.repositories import get_task_repo
        
        task_id = event.data["task_id"]
        error = event.data.get("error", "Unknown error")
        task_repo = get_task_repo()
        
        task = task_repo.fail(task_id, error)
        if not task:
            return {"error": "Task not found"}
        
        await self._event_bus.publish(Event(
            type=EventType.TASK_FAILED,
            project=task.project,
            data={"task_id": task.id, "error": error},
        ))
        
        return {"task": task.model_dump(mode="json")}
    
    async def _handle_task_cancel(self, event: MachineEvent) -> dict:
        """Cancel a task."""
        from .db.repositories import get_task_repo
        
        task_id = event.data["task_id"]
        task_repo = get_task_repo()
        
        task = task_repo.cancel(task_id)
        if not task:
            return {"error": "Task not found"}
        
        return {"task": task.model_dump(mode="json")}
    
    async def _handle_task_block(self, event: MachineEvent) -> dict:
        """Block a task (needs human input)."""
        from .db.repositories import get_task_repo
        
        task_id = event.data["task_id"]
        reason = event.data.get("reason", "Needs review")
        task_repo = get_task_repo()
        
        task = task_repo.block(task_id, reason)
        if not task:
            return {"error": "Task not found"}
        
        await self._event_bus.publish(Event(
            type=EventType.TASK_BLOCKED,
            project=task.project,
            data={"task_id": task.id, "reason": reason},
        ))
        
        return {"task": task.model_dump(mode="json")}
    
    async def _handle_task_review(self, event: MachineEvent) -> dict:
        """Approve or reject a task awaiting review."""
        from .db.repositories import get_task_repo
        
        task_id = event.data.get("task_id")
        approved = event.data.get("approved", False)
        comment = event.data.get("comment")
        
        if not task_id:
            return {"error": "task_id required"}
        
        task_repo = get_task_repo()
        task = task_repo.get(task_id)
        if not task:
            return {"error": "Task not found"}
        
        reviewer_id = event.session_id or "system"
        
        if approved:
            task_repo.db.execute("""
                UPDATE tasks 
                SET status = 'pending', 
                    reviewed_by = ?,
                    reviewed_at = ?
                WHERE id = ?
            """, (reviewer_id, datetime.now().isoformat(), task_id))
            task_repo.db.commit()
            
            log_event("task_approved", direction="sent", data={"task_id": task_id}, source="server")
            return {"action": "approved", "task_id": task_id}
        else:
            task_repo.cancel(task_id)
            log_event("task_rejected", direction="sent", data={"task_id": task_id, "comment": comment}, source="server")
            return {"action": "rejected", "task_id": task_id}
    
    async def _handle_task_retry(self, event: MachineEvent) -> dict:
        """Retry a failed task by creating a new one."""
        from .db.repositories import get_task_repo
        from .db.models import TaskPriority as DBTaskPriority
        
        task_id = event.data.get("task_id")
        new_description = event.data.get("description")
        
        if not task_id:
            return {"error": "task_id required"}
        
        task_repo = get_task_repo()
        original = task_repo.get(task_id)
        if not original:
            return {"error": "Task not found"}
        
        description = new_description or original.description
        priority = original.priority
        
        new_task = task_repo.create(
            project_id=original.project_id,
            description=description,
            priority=priority,
        )
        
        log_event("task_retry", direction="sent", data={"original_id": task_id, "new_id": new_task.id}, source="server")
        return {"task": {"id": new_task.id, "description": description, "status": "pending"}}
    
    # =========================================================================
    # PROCESS HANDLERS
    # =========================================================================
    
    async def _handle_process_start(self, event: MachineEvent) -> dict:
        """Start a process."""
        process_id = event.data.get("process_id")
        if not process_id:
            return {"error": "process_id required"}
            
        pm = get_process_manager()
        
        try:
            pm.start(process_id)
            log_event("process_started", direction="sent", data={"process_id": process_id}, source="server")
            return {"started": process_id}
        except Exception as e:
            return {"error": str(e)}
    
    async def _handle_process_stop(self, event: MachineEvent) -> dict:
        """Stop a process."""
        process_id = event.data.get("process_id")
        if not process_id:
            return {"error": "process_id required"}
            
        pm = get_process_manager()
        
        try:
            pm.stop(process_id)
            log_event("process_stopped", direction="sent", data={"process_id": process_id}, source="server")
            return {"stopped": process_id}
        except Exception as e:
            return {"error": str(e)}
    
    # =========================================================================
    # TERMINAL HANDLERS
    # =========================================================================
    
    async def _handle_terminal_open(self, event: MachineEvent) -> dict:
        """Open terminal for a session."""
        session = self._sessions.get(event.session_id)
        if session:
            session.terminal_project = event.data.get("project")
            try:
                session.start_work()  # Transition session state
            except Exception:
                pass  # Already in working state
            session.last_activity = datetime.now()
            return {"terminal_project": session.terminal_project}
        return {"error": "Session not found"}
    
    async def _handle_terminal_close(self, event: MachineEvent) -> dict:
        """Close terminal for a session."""
        session = self._sessions.get(event.session_id)
        if session:
            session.terminal_project = None
            try:
                session.stop_work()  # Transition session state
            except Exception:
                pass
            return {"terminal_closed": True}
        return {"error": "Session not found"}
    
    # =========================================================================
    # PREVIEW HANDLERS
    # =========================================================================
    
    async def _handle_preview_start(self, event: MachineEvent) -> dict:
        """Start preview for a process."""
        session = self._sessions.get(event.session_id)
        process_id = event.data.get("process_id")
        
        if session:
            session.preview_process = process_id
            session.browser_session_id = f"browser-{process_id}"
            try:
                session.start_work()
            except Exception:
                pass
            session.last_activity = datetime.now()
            return {"preview_process": process_id, "browser_session_id": session.browser_session_id}
        return {"error": "Session not found"}
    
    async def _handle_preview_stop(self, event: MachineEvent) -> dict:
        """Stop preview for a session."""
        session = self._sessions.get(event.session_id)
        if session:
            session.preview_process = None
            session.browser_session_id = None
            try:
                session.stop_work()
            except Exception:
                pass
            return {"preview_closed": True}
        return {"error": "Session not found"}
    
    # =========================================================================
    # AGENT HANDLERS
    # =========================================================================
    
    async def _handle_agent_spawn(self, event: MachineEvent) -> dict:
        """Spawn an agent for a project."""
        try:
            from . import app as app_module
            
            project = event.data.get("project")
            provider = event.data.get("provider")
            task = event.data.get("task")
            
            if not project:
                return {"error": "project required"}
            
            if not app_module.agent_manager:
                return {"error": "Agent manager not initialized"}
            
            state = app_module.agent_manager.spawn(
                project=project,
                provider=provider,
                task=task,
            )
            
            log_event("agent_spawned", direction="sent", data={"project": project}, source="server")
            return {"agent": state.model_dump(mode="json")}
        except Exception as e:
            return {"error": str(e)}
    
    async def _handle_agent_stop(self, event: MachineEvent) -> dict:
        """Stop an agent."""
        try:
            from . import app as app_module
            
            project = event.data.get("project")
            force = event.data.get("force", False)
            
            if not project:
                return {"error": "project required"}
            
            if not app_module.agent_manager:
                return {"error": "Agent manager not initialized"}
            
            success = app_module.agent_manager.stop(project=project, force=force)
            
            if success:
                log_event("agent_stopped", direction="sent", data={"project": project, "force": force}, source="server")
                return {"stopped": project}
            else:
                return {"error": f"Agent for {project} not found"}
        except Exception as e:
            return {"error": str(e)}
    
    # =========================================================================
    # VOICE COMMAND HANDLERS
    # =========================================================================
    
    async def _handle_voice_command(self, event: MachineEvent) -> dict:
        """Route voice commands through the orchestrator intent engine."""
        from .intent import get_intent_engine, get_action_executor, build_orchestrator_context

        message = event.data.get("intent", "")
        project = event.data.get("entities", {}).get("project")

        engine = get_intent_engine()
        executor = get_action_executor()
        ctx = build_orchestrator_context(project, event.session_id, "voice")

        try:
            result = await engine.process(message, ctx)
        except Exception as e:
            return {"error": f"Orchestrator error: {e}"}

        executed = []
        for action in result.actions:
            outcome = await executor.execute(action.name, action.params, ctx)
            executed.append(outcome)

        return {
            "response": result.response,
            "actions": executed,
        }
    
    # =========================================================================
    # CLIENT ACTION HANDLER
    # =========================================================================

    async def _handle_client_action(self, event: MachineEvent) -> dict:
        """Handle client_action events — passthrough for logging/broadcast only."""
        return {
            "action": event.data.get("action", "unknown"),
            "project": event.data.get("project"),
            "status": event.data.get("status", "ok"),
        }

    # =========================================================================
    # STATE SNAPSHOT & BROADCASTING
    # =========================================================================
    
    def get_snapshot(self) -> StateSnapshot:
        """Get current state snapshot."""
        pm = get_process_manager()
        app_module = None
        
        # Get agent manager from app globals (lazy import to avoid circular)
        try:
            from . import app as app_module
            agents = app_module.agent_manager.list() if app_module.agent_manager else []
        except (ImportError, AttributeError):
            agents = []
        
        try:
            from .db.repositories import get_task_repo
            task_repo = get_task_repo()
            tasks = [t.model_dump(mode="json") for t in task_repo.list(limit=100)]
            queue_stats = task_repo.stats()
        except Exception:
            tasks = []
            queue_stats = {"total": 0, "pending": 0, "in_progress": 0, "completed": 0, "failed": 0, "by_project": {}}
        
        # Terminal sessions
        try:
            from .terminal import get_terminal_manager
            tm = get_terminal_manager()
            terminals = []
            for session in tm.list():
                terminals.append({
                    "id": session.id,
                    "project": session.project,
                    "status": session.status.value,
                    "pid": session.pid,
                    "waiting_for_input": tm.is_waiting_for_input(session.id),
                    "command": session.command,
                })
        except Exception:
            terminals = []

        # Collections
        try:
            from .db.repositories import get_collection_repo
            collection_repo = get_collection_repo()
            collections_list = collection_repo.list()
            counts = collection_repo.project_counts()
            collections = [
                {"id": c.id, "name": c.name, "description": c.description, "sort_order": c.sort_order, "project_count": counts.get(c.id, 0)}
                for c in collections_list
            ]
        except Exception:
            collections = []

        # Phone call info
        phone_info = {"configured": False, "active": False}
        try:
            from .channels.phone import get_phone_channel
            phone = get_phone_channel()
            if phone:
                phone_info = {**phone.get_call_info(), "configured": True}
            elif app_module and hasattr(app_module, "_is_phone_configured"):
                phone_info["configured"] = bool(app_module._is_phone_configured())
        except Exception:
            pass

        # Conversation metadata
        conversation_info = {}
        try:
            from .conversation import get_conversation_manager
            conv_mgr = get_conversation_manager()
            # Get the most recently updated thread
            db = conv_mgr._db
            row = db.execute(
                "SELECT id, project, updated_at FROM conversation_threads ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
            if row:
                thread_id = row["id"]
                turn_count_row = db.execute(
                    "SELECT COUNT(*) FROM conversation_turns WHERE thread_id = ?", (thread_id,)
                ).fetchone()
                last_turn_row = db.execute(
                    "SELECT role, content, created_at FROM conversation_turns WHERE thread_id = ? ORDER BY created_at DESC LIMIT 1",
                    (thread_id,),
                ).fetchone()
                conversation_info = {
                    "thread_id": thread_id,
                    "project": row["project"],
                    "turn_count": turn_count_row[0] if turn_count_row else 0,
                    "updated_at": row["updated_at"],
                    "last_turn": {
                        "role": last_turn_row["role"],
                        "content": last_turn_row["content"][:100],
                        "created_at": last_turn_row["created_at"],
                    } if last_turn_row else None,
                }
        except Exception:
            pass

        all_actions = self._enrich_processes(pm.list())

        # Channels + terminal↔channel mapping
        channels_data = []
        terminal_channels_map: dict[str, list[str]] = {}
        try:
            from .channel_manager import get_channel_manager
            cm = get_channel_manager()
            for ch in cm.list_channels():
                channels_data.append({
                    "id": ch.id, "name": ch.name, "type": ch.type.value,
                    "project_ids": ch.project_ids,
                    "auto_mode": ch.auto_mode,
                })
                # Build terminal -> channels map
                for tid in cm.get_channel_terminals(ch.id):
                    if tid not in terminal_channels_map:
                        terminal_channels_map[tid] = []
                    terminal_channels_map[tid].append(ch.id)
        except Exception:
            pass

        return StateSnapshot(
            server_state=self.state,
            tasks=tasks,
            processes=all_actions,
            actions=all_actions,
            agents=[a.model_dump(mode="json") for a in agents],
            sessions=[s.to_dict() for s in self._sessions.values()],
            terminals=terminals,
            collections=collections,
            channels=channels_data,
            terminal_channels=terminal_channels_map,
            phone=phone_info,
            conversation=conversation_info,
            queue_stats=queue_stats,
            timestamp=datetime.now().isoformat(),
        )
    
    @staticmethod
    def _enrich_processes(process_list) -> list[dict]:
        """Serialize processes/actions, injecting preview_url from CaddyManager."""
        from .caddy import get_caddy_manager
        cm = get_caddy_manager()
        result = []
        for p in process_list:
            d = p.model_dump(mode="json")
            # Ensure kind is always present as a string
            d["kind"] = p.kind.value if hasattr(p.kind, "value") else (p.kind or "service")
            if cm:
                d["preview_url"] = cm.get_preview_url(p.id)
            result.append(d)
        return result

    async def _broadcast_state(self) -> None:
        """Broadcast state to all subscribers."""
        snapshot = self.get_snapshot()
        for callback in self._subscribers:
            try:
                await callback(snapshot)
            except Exception as e:
                print(f"[StateMachine] Error broadcasting state: {e}")
    
    def get_state_diagram(self) -> str:
        """Generate a state diagram in mermaid format for visualization."""
        lines = ["stateDiagram-v2"]
        for t in self.TRANSITIONS:
            src = t['source'] if t['source'] != '*' else '[*]'
            if isinstance(src, list):
                for s in src:
                    lines.append(f"    {s} --> {t['dest']}: {t['trigger']}")
            else:
                lines.append(f"    {src} --> {t['dest']}: {t['trigger']}")
        return "\n".join(lines)


# =============================================================================
# GLOBAL INSTANCE
# =============================================================================

_machine: Optional[ServerStateMachine] = None


def get_state_machine() -> ServerStateMachine:
    """Get the global state machine instance."""
    global _machine
    if _machine is None:
        _machine = ServerStateMachine()
    return _machine


async def init_state_machine() -> ServerStateMachine:
    """Initialize the global state machine."""
    machine = get_state_machine()
    machine.initialize()  # Trigger transition to ready
    return machine
