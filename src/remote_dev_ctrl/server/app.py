"""FastAPI server for RDC Command Center."""

import asyncio
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, UploadFile, File, Form, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from pydantic import BaseModel

from .debug_page import DEBUG_PAGE_HTML

from .config import Config, ensure_rdc_home, get_rdc_home
from .agents import AgentManager, AgentStatus
from .events import EventBus, Event, get_event_bus
from .events.bus import EventType
from .state_machine import get_state_machine, init_state_machine, MachineEvent
from .vault import get_secret
from .auth import get_auth_manager, TokenInfo, Role
from .audit import audit, AuditAction, get_audit_logger
from .middleware import AuthMiddleware
from .db import init_databases, close_databases, TaskRepository, EventRepository
from .db.repositories import get_project_repo, get_event_repo, resolve_project_id
from .orchestrator import Orchestrator, set_orchestrator


# Pydantic models for API
class SpawnRequest(BaseModel):
    project: str
    provider: str | None = None
    task: str | None = None
    worktree: str | None = None


class TaskRequest(BaseModel):
    project: str | None = None
    description: str = ""
    priority: str = "normal"
    requires_review: bool = False  # If true, task goes to awaiting_review first
    review_prompt: str | None = None  # What to show reviewer
    parent_task_id: str | None = None  # For task chaining
    recipe_id: str | None = None  # If set, description is rendered from recipe
    model: str | None = None  # cursor-agent model id (e.g. "opus-4.6", "sonnet-4.6")
    provider: str | None = None  # "cursor" (default), "web" (web-native agent)


class AssignRequest(BaseModel):
    task: str


# Global state
config: Config | None = None
agent_manager: AgentManager | None = None
task_repo: TaskRepository | None = None
event_repo: EventRepository | None = None
event_bus: EventBus | None = None
orchestrator: Orchestrator | None = None
process_manager = None
caddy_manager = None
vnc_manager = None
telegram_bot = None
phone_channel = None
auth_manager = None
connected_clients: set[WebSocket] = set()
_shutdown_event: asyncio.Event = asyncio.Event()  # Set by /admin/restart so WS handlers exit promptly


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    global config, agent_manager, task_repo, event_repo, event_bus, orchestrator, process_manager, caddy_manager, vnc_manager, telegram_bot, phone_channel, auth_manager
    
    ensure_rdc_home()
    
    # Initialize SQLite databases
    init_databases()
    from .conversation import init_conversation_schema
    init_conversation_schema()
    task_repo = TaskRepository()
    event_repo = EventRepository()
    
    config = Config.load()
    agent_manager = AgentManager(config)
    event_bus = get_event_bus()
    auth_manager = get_auth_manager()
    
    # Initialize state machine
    state_machine = await init_state_machine()
    
    # Initialize process manager
    from .processes import get_process_manager
    process_manager = get_process_manager()
    
    # Initialize VNC manager
    from .vnc import get_vnc_manager
    vnc_manager = get_vnc_manager()
    
    # Register process event handlers for real-time updates
    def on_process_event(event_name: str):
        def handler(process_id: str, *args):
            # Broadcast to WebSocket clients
            import asyncio
            state = process_manager.get(process_id)
            if state:
                msg = {
                    "type": f"process.{event_name}",
                    "process_id": process_id,
                    "project": state.project,
                    "status": state.status.value,
                    "error": state.error,
                }
                for client in list(connected_clients):
                    try:
                        asyncio.create_task(client.send_json(msg))
                    except Exception:
                        connected_clients.discard(client)
        return handler
    
    process_manager.on("started", on_process_event("started"))
    process_manager.on("stopped", on_process_event("stopped"))
    process_manager.on("exited", on_process_event("exited"))

    # Initialize Caddy reverse proxy if configured
    if config.caddy.enabled:
        from .caddy import CaddyManager, set_caddy_manager, sanitize_subdomain
        caddy_manager = CaddyManager(config.caddy)
        set_caddy_manager(caddy_manager)
        if await caddy_manager.start():
            # Add routes for already-running processes
            for p in process_manager.list():
                if p.status.value == "running" and p.port:
                    sub = sanitize_subdomain(p.project, p.name)
                    await caddy_manager.add_route(p.id, sub, p.port)

            # Hook process events to manage routes dynamically
            def on_caddy_started(process_id: str, state):
                if state.port:
                    sub = sanitize_subdomain(state.project, state.name)
                    asyncio.create_task(caddy_manager.add_route(process_id, sub, state.port))

            def on_caddy_stopped(process_id: str, *args):
                asyncio.create_task(caddy_manager.remove_route(process_id))

            process_manager.on("started", on_caddy_started)
            process_manager.on("stopped", on_caddy_stopped)
            process_manager.on("exited", on_caddy_stopped)
        else:
            caddy_manager = None
            set_caddy_manager(None)

    # Initialize orchestrator
    orchestrator = Orchestrator(
        config=config,
        agent_manager=agent_manager,
        task_repo=task_repo,
        event_repo=event_repo,
    )
    orchestrator.set_loop(asyncio.get_running_loop())
    set_orchestrator(orchestrator)
    
    # Create initial admin token if none exist
    if not auth_manager.has_any_tokens():
        token, info = auth_manager.create_initial_admin_token()
        print("\n" + "=" * 60)
        print("INITIAL ADMIN TOKEN CREATED")
        print("=" * 60)
        print(f"Token: {token}")
        print("\nSave this token! It will not be shown again.")
        print("Use it to authenticate API requests:")
        print(f"  curl -H 'Authorization: Bearer {token}' http://...")
        print("=" * 60 + "\n")
        
        audit(
            AuditAction.AUTH_TOKEN_CREATED,
            actor_type="system",
            resource_type="token",
            resource_id=info.id,
            metadata={"name": info.name, "role": info.role.value},
        )
    
    # Recover active sessions from previous server run
    try:
        from .session_manager import get_session_manager
        get_session_manager().recover_sessions()
    except Exception:
        logger.debug("Session recovery failed", exc_info=True)

    # Subscribe to events to broadcast to WebSocket clients
    @event_bus.subscribe()
    async def broadcast_event(event: Event):
        if connected_clients:
            message = event.to_json()
            dead_clients: set[WebSocket] = set()
            for client in list(connected_clients):
                try:
                    await client.send_text(message)
                except Exception:
                    dead_clients.add(client)
            connected_clients.difference_update(dead_clients)
    
    # Start Telegram bot if configured (optional: server can run without network)
    if config.channels.telegram.enabled:
        token = get_secret("TELEGRAM_BOT_TOKEN") or config.channels.telegram.token
        if token:
            try:
                from .channels.telegram import TelegramBot

                dashboard_url = config.channels.telegram.dashboard_url if hasattr(config.channels.telegram, 'dashboard_url') else None

                telegram_bot = TelegramBot(
                    token=token,
                    allowed_users=config.channels.telegram.allowed_users or None,
                    on_command=handle_telegram_command,
                    dashboard_url=dashboard_url,
                )
                await telegram_bot.start()
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    "Telegram bot failed to start (no network?): %s. Server running without Telegram.",
                    e,
                )
                telegram_bot = None

    # Start phone channel if configured
    if config.channels.voice.enabled:
        try:
            from .channels.phone import PhoneChannel, set_phone_channel

            account_sid = get_secret("TWILIO_ACCOUNT_SID") or config.channels.voice.account_sid
            auth_token = get_secret("TWILIO_AUTH_TOKEN") or config.channels.voice.auth_token
            twilio_number = get_secret("TWILIO_PHONE_NUMBER") or config.channels.voice.phone_number
            user_phone = config.channels.voice.user_phone_number
            webhook_url = config.channels.voice.webhook_base_url

            if account_sid and auth_token and twilio_number and user_phone and webhook_url:
                phone_channel = PhoneChannel(
                    account_sid=account_sid,
                    auth_token=auth_token,
                    twilio_number=twilio_number,
                    user_phone=user_phone,
                    webhook_base_url=webhook_url,
                )
                phone_channel.start()
                set_phone_channel(phone_channel)
                import logging as _logging
                _logging.getLogger(__name__).info("Phone channel started")
            else:
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "Phone channel enabled but missing credentials. Need: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER, user_phone_number, webhook_base_url"
                )
        except ImportError as e:
            import logging as _logging
            _logging.getLogger(__name__).warning("Phone channel: twilio not installed: %s", e)
        except Exception as e:
            import logging as _logging
            _logging.getLogger(__name__).warning("Phone channel failed to start: %s", e)

    # Start the orchestrator (auto-assigns tasks to agents)
    if config.agents.auto_spawn:
        await orchestrator.start()
    
    # Ensure all existing projects have a default channel
    try:
        from .channel_manager import get_channel_manager
        from .db.repositories import ProjectRepository
        cm = get_channel_manager()
        for proj in ProjectRepository().list():
            cm.ensure_project_channel(proj.id, proj.name)
    except Exception as e:
        logger.warning(f"Channel sync for existing projects failed: {e}")

    # Re-attach to any relay terminal sessions that survived the last shutdown
    from .terminal import get_terminal_manager as _get_tm
    from .state_machine import get_state_machine
    await _get_tm().rediscover_sessions()

    def _schedule_broadcast_on_stop(_session_id: str):
        try:
            loop = asyncio.get_event_loop()
            asyncio.ensure_future(get_state_machine()._broadcast_state(), loop=loop)
        except Exception:
            pass

    _get_tm()._on_session_stopped = _schedule_broadcast_on_stop

    # Emit server started event
    event_bus.emit(EventType.SERVER_STARTED)

    from .channel_manager import emit
    emit("system.server_started", data={"version": "0.1.1"})
    
    try:
        yield
    finally:
        # Cleanup runs on normal shutdown and on Ctrl+C (cancellation)
        if orchestrator:
            try:
                await orchestrator.stop()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Orchestrator stop error")
        if telegram_bot:
            try:
                await asyncio.wait_for(telegram_bot.stop(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("Telegram bot stop timed out after 10s")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Telegram bot stop error")
        if phone_channel:
            try:
                await phone_channel.stop()
                from .channels.phone import set_phone_channel
                set_phone_channel(None)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Phone channel stop error")
        if caddy_manager:
            try:
                await caddy_manager.stop()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Caddy stop error")
        if vnc_manager:
            vnc_manager.stop_all()
        try:
            from .browser import get_browser_manager
            await get_browser_manager().stop_all()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        if process_manager:
            process_manager.stop_all()
        # Destroy all terminal sessions so background PTY tasks don't block shutdown
        from .terminal import get_terminal_manager
        get_terminal_manager().destroy_all()
        event_bus.emit(EventType.SERVER_STOPPED)
        close_databases()


async def handle_telegram_command(command: str, args: str, user_id: int) -> str:
    """Handle commands from Telegram."""
    from .processes import get_process_manager
    
    try:
        if command == "status":
            agents = agent_manager.list() if agent_manager else []
            running = len([a for a in agents if a.status not in (AgentStatus.STOPPED, AgentStatus.ERROR)])
            stats = task_repo.stats() if task_repo else {}
            pm = get_process_manager()
            processes = pm.list() if pm else []
            running_procs = len([p for p in processes if p.status == "running"])
            
            return (
                f"📊 *System Status*\n\n"
                f"🤖 Agents: `{running}` running / `{len(agents)}` total\n"
                f"📋 Tasks: `{stats.get('pending', 0)}` pending, `{stats.get('in_progress', 0)}` in progress\n"
                f"⚙️ Processes: `{running_procs}` running / `{len(processes)}` total\n"
                f"👥 Clients: `{len(connected_clients)}` connected"
            )
        
        elif command == "agents":
            agents = agent_manager.list() if agent_manager else []
            if not agents:
                return "🤖 *Agents*\n\nNo agents found."
            
            lines = ["🤖 *Agents*\n"]
            for a in agents:
                status_icon = {"working": "🟢", "idle": "⚪", "error": "🔴", "stopped": "⬛"}.get(a.status.value, "⚪")
                lines.append(f"{status_icon} `{a.project}` - {a.status.value}")
                if a.current_task:
                    lines.append(f"    └ _{a.current_task[:50]}_")
            return "\n".join(lines)
        
        elif command == "tasks":
            tasks = task_repo.list(limit=20) if task_repo else []
            if not tasks:
                return "📋 *Tasks*\n\nNo pending tasks.\n\n_Use /add to create a task._"
            
            lines = ["📋 *Tasks*\n"]
            for t in tasks[:10]:
                status_icon = {
                    "pending": "⏳", "assigned": "👤", "in_progress": "🔄",
                    "blocked": "🚫", "completed": "✅", "failed": "❌", "cancelled": "⬛"
                }.get(t.status.value, "⚪")
                priority_badge = {"urgent": "🔴", "high": "🟠", "normal": "", "low": "⚪"}.get(t.priority.value, "")
                lines.append(f"{status_icon} `{t.id}` {priority_badge}")
                lines.append(f"    {t.project}: _{t.description[:40]}_")
            
            if len(tasks) > 10:
                lines.append(f"\n_...and {len(tasks) - 10} more_")
            return "\n".join(lines)
        
        elif command == "processes":
            pm = get_process_manager()
            processes = pm.list() if pm else []
            if not processes:
                return "⚙️ *Processes*\n\nNo processes configured."
            
            lines = ["⚙️ *Processes*\n"]
            for p in processes[:15]:
                status_icon = {"running": "🟢", "stopped": "⬛", "failed": "🔴"}.get(p.status, "⚪")
                port_info = f":{p.port}" if p.port else ""
                lines.append(f"{status_icon} `{p.id}`{port_info} - {p.project}")
            return "\n".join(lines)
        
        elif command == "projects":
            from .db.repositories import get_project_repo
            db_projects = get_project_repo().list()

            if not db_projects:
                return "📁 *Projects*\n\nNo projects registered."

            lines = ["📁 *Projects*\n"]
            for p in db_projects:
                lines.append(f"• `{p.name}`")
            return "\n".join(lines)
        
        elif command == "spawn":
            parts = args.split(maxsplit=1)
            project = parts[0] if parts else ""
            task = parts[1] if len(parts) > 1 else None
            
            if not project:
                return "Usage: `/spawn <project> [task]`"
            
            try:
                state = agent_manager.spawn(project, task=task)
                return f"✅ *Agent Spawned*\n\nProject: `{project}`\nPID: `{state.pid}`"
            except Exception as e:
                return f"❌ *Error*\n\n`{e}`"
        
        elif command == "stop":
            if not args:
                return "Usage: `/stop <project>`"
            
            if agent_manager.stop(args):
                return f"✅ *Agent Stopped*\n\nProject: `{args}`"
            else:
                return f"❌ Agent not found: `{args}`"
        
        elif command == "add_task":
            parts = args.split(maxsplit=1)
            project = parts[0] if parts else ""
            description = parts[1] if len(parts) > 1 else ""
            
            if not project or not description:
                return "Usage: `/add <project> <task description>`"
            
            pid = resolve_project_id(project)
            if not pid:
                return f"❌ Unknown project: `{project}`"
            task = task_repo.create(project_id=pid, description=description)
            return (
                f"✅ *Task Created*\n\n"
                f"ID: `{task.id}`\n"
                f"Project: `{project}`\n"
                f"Description: _{description[:100]}_\n\n"
                f"_Use /run {task.id} to start it._"
            )
        
        elif command == "run_task":
            task_id = args.strip()
            if not task_id:
                return "Usage: `/run <task_id>`"
            
            task = task_repo.get(task_id)
            if not task:
                return f"❌ Task not found: `{task_id}`"
            
            task_repo.start(task_id)
            return f"▶️ *Task Started*\n\nID: `{task_id}`\nProject: `{task.project}`"
        
        elif command == "cancel_task":
            task_id = args.strip()
            if not task_id:
                return "Usage: `/cancel <task_id>`"
            
            task = task_repo.cancel(task_id)
            if not task:
                return f"❌ Task not found: `{task_id}`"
            
            return f"❌ *Task Cancelled*\n\nID: `{task_id}`"
        
        elif command == "logs":
            parts = args.split()
            project = parts[0] if parts else ""
            lines = int(parts[1]) if len(parts) > 1 else 20
            
            if not project:
                return "Usage: `/logs <project> [lines]`"
            
            # Get agent logs
            from .config import get_rdc_home
            log_file = get_rdc_home() / "agents" / f"{project}.log"
            
            if not log_file.exists():
                return f"No logs found for `{project}`"
            
            try:
                content = log_file.read_text()
                log_lines = content.strip().split("\n")
                recent = log_lines[-lines:]
                return "\n".join(recent)[-3000]  # Telegram limit
            except Exception as e:
                return f"Error reading logs: `{e}`"
        
        elif command == "start_action":
            process_id = args.strip()
            if not process_id:
                return "Usage: action_id required"

            pm = get_process_manager()
            try:
                pm.start(process_id)
                return f"▶️ *Action Started*\n\n`{process_id}`"
            except Exception as e:
                return f"❌ Failed to start: `{e}`"

        elif command == "stop_action":
            process_id = args.strip()
            if not process_id:
                return "Usage: action_id required"

            pm = get_process_manager()
            try:
                pm.stop(process_id)
                return f"🛑 *Action Stopped*\n\n`{process_id}`"
            except Exception as e:
                return f"❌ Failed to stop: `{e}`"
        
        elif command == "message":
            import re
            text = args.lower().strip()
            original_text = args.strip()
            
            # Get list of valid project names for fuzzy matching
            from .db.repositories import get_project_repo
            try:
                valid_projects = [p.name.lower() for p in get_project_repo().list()]
            except Exception:
                valid_projects = []
            
            def fuzzy_match_project(name: str) -> str | None:
                """Find best matching project name."""
                name = name.lower().strip()
                # Exact match
                for p in valid_projects:
                    if p == name:
                        return p
                # Starts with
                for p in valid_projects:
                    if p.startswith(name) or name.startswith(p):
                        return p
                # Contains
                for p in valid_projects:
                    if name in p or p in name:
                        return p
                return None
            
            # Natural language parsing with intent extraction
            
            # Select/switch/use project
            select_match = re.search(r'(?:select|switch(?:\s+to)?|use|set|choose)\s+(?:project\s+)?(?:to\s+)?(?:the\s+)?["\']?([a-zA-Z0-9_-]+)["\']?(?:\s+project)?', text)
            if select_match:
                raw_project = select_match.group(1)
                # Filter out common words that aren't project names
                if raw_project not in ('to', 'the', 'project', 'a', 'an', 'my', 'for'):
                    matched = fuzzy_match_project(raw_project)
                    if matched:
                        return f"select_project:{matched}"
                    else:
                        # Project not found - return special value to show selection
                        return f"select_project_notfound:{raw_project}"
            
            # If they said "select project" without a name, show list
            if ("select" in text or "switch" in text or "choose" in text) and "project" in text:
                return "show_projects"
            
            # Spawn/start agent - "spawn agent for documaker" or "start documaker agent"
            spawn_match = re.search(r'(?:spawn|start|run|launch)\s+(?:an?\s+)?(?:agent\s+)?(?:for\s+)?(?:the\s+)?["\']?([a-zA-Z0-9_-]+)["\']?(?:\s+agent)?', text)
            if spawn_match and ("spawn" in text or "agent" in text or "launch" in text):
                project = spawn_match.group(1)
                if project not in ('agent', 'an', 'the', 'for'):
                    return await handle_telegram_command("spawn", project, user_id)
            
            # Stop agent - "stop documaker agent" or "kill agent for documaker"
            stop_match = re.search(r'(?:stop|kill|terminate)\s+(?:the\s+)?(?:agent\s+)?(?:for\s+)?["\']?([a-zA-Z0-9_-]+)["\']?(?:\s+agent)?', text)
            if stop_match and ("agent" in text or "kill" in text):
                project = stop_match.group(1)
                if project not in ('agent', 'the', 'for'):
                    return await handle_telegram_command("stop", project, user_id)
            
            # View logs - "logs for documaker" or "show documaker logs"
            logs_match = re.search(r'(?:logs?|output|show\s+logs?)\s+(?:for\s+)?(?:the\s+)?["\']?([a-zA-Z0-9_-]+)["\']?', text)
            if not logs_match:
                # Try reverse: "documaker logs"
                logs_match = re.search(r'["\']?([a-zA-Z0-9_-]+)["\']?\s+logs?', text)
            if logs_match and "log" in text:
                project = logs_match.group(1)
                if project not in ('the', 'for', 'show', 'view'):
                    return await handle_telegram_command("logs", project, user_id)
            
            # Start/stop process
            proc_match = re.search(r'(?:start|stop)\s+(?:process\s+)?["\']?([a-zA-Z0-9_-]+)["\']?', text)
            if proc_match and "process" in text:
                process_id = proc_match.group(1)
                if "start" in text:
                    return await handle_telegram_command("start_action", process_id, user_id)
                else:
                    return await handle_telegram_command("stop_action", process_id, user_id)
            
            # Add task - "add task to project: description" or "add fix the bug to documaker"
            add_match = re.search(r'(?:add|create)\s+(?:task\s+)?(?:to\s+)?["\']?([a-zA-Z0-9_-]+)["\']?[:\s]+(.+)', text)
            if add_match:
                project = add_match.group(1)
                description = add_match.group(2).strip()
                return await handle_telegram_command("add_task", f"{project} {description}", user_id)
            
            # Simple queries
            if "status" in text or "how are" in text or "what's up" in text or "overview" in text:
                return await handle_telegram_command("status", "", user_id)
            elif "agents" in text or "workers" in text:
                return await handle_telegram_command("agents", "", user_id)
            elif "tasks" in text or "queue" in text or "todo" in text or "pending" in text:
                return await handle_telegram_command("tasks", "", user_id)
            elif "process" in text:
                return await handle_telegram_command("processes", "", user_id)
            elif "project" in text:
                return await handle_telegram_command("projects", "", user_id)
            elif "help" in text:
                return (
                    "🎤 *Voice Commands*\n\n"
                    "• _What's the status?_\n"
                    "• _Show me the tasks_\n"
                    "• _Select project documaker_\n"
                    "• _Spawn agent for documaker_\n"
                    "• _Stop agent documaker_\n"
                    "• _Logs for documaker_\n"
                    "• _Add fix the bug to documaker_\n"
                    "• _Start process my-app_\n"
                )
            else:
                return (
                    "🤔 I didn't understand that.\n\n"
                    "Try saying:\n"
                    "• _What's the status?_\n"
                    "• _Show tasks_\n"
                    "• _Select project X_\n"
                    "• _Spawn agent for X_\n\n"
                    "Or say _help_ for more."
                )
        
        else:
            return f"Unknown command: `{command}`"
    
    except Exception as e:
        logger.error(f"Telegram command error: {e}")
        return f"❌ *Error*\n\n`{e}`"


import logging
import traceback

# Configure logging to show errors
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rdc.server")
logging.getLogger("httpx").setLevel(logging.WARNING)


# Suppress noisy access logs for high-frequency polling endpoints
class _QuietPollFilter(logging.Filter):
    """Filter out repetitive access log lines for polling endpoints."""
    _QUIET_PATHS = ("/browser/sessions", "/recordings", "/ws/state", "/pinchtab/status")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(p in msg for p in self._QUIET_PATHS)


logging.getLogger("uvicorn.access").addFilter(_QuietPollFilter())

app = FastAPI(
    title="RDC Command Center",
    description="Agent orchestration and task management",
    version="0.1.0",
    lifespan=lifespan,
)

project_repo = get_project_repo()
event_repo = get_event_repo()

# Mount static files for vendor assets
from fastapi.staticfiles import StaticFiles
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Mount React frontend (built with Vite)
FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "dist"
if FRONTEND_DIST.exists() and (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="frontend-assets")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Log all unhandled exceptions with full traceback."""
    tb = traceback.format_exc()
    logger.error(f"Unhandled exception on {request.method} {request.url.path}:\n{tb}")
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc), "traceback": tb.split("\n")[-5:]},
    )

# Auth middleware (can be disabled via config)
# Set auth_enabled=False for development without tokens
app.add_middleware(AuthMiddleware, auth_enabled=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict this
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_permissions_policy(request, call_next):
    """Add Permissions-Policy header for clipboard access in iframes."""
    response = await call_next(request)
    response.headers["Permissions-Policy"] = "clipboard-read=*, clipboard-write=*"
    return response


# =============================================================================
# Health & Status
# =============================================================================

@app.get("/debug", response_class=HTMLResponse)
async def debug_page():
    """Serve the state machine debug page."""
    return DEBUG_PAGE_HTML


@app.get("/debug/diagram")
async def state_diagram():
    """Get the server state machine diagram in Mermaid format."""
    machine = get_state_machine()
    
    # Get session info
    sessions = []
    for sid, session in machine._sessions.items():
        sessions.append({
            "id": sid,
            "state": session.state,
            "project": session.project,
            "terminal_project": session.terminal_project,
            "preview_process": session.preview_process,
            "client_name": getattr(session, 'client_name', None),
        })
    
    return {
        "current_state": machine.current_state,
        "diagram": machine.get_state_diagram(),
        "sessions": sessions,
        "format": "mermaid",
    }


@app.get("/debug/session/{session_id}")
async def session_state(session_id: str):
    """Get a specific session's state and diagram."""
    machine = get_state_machine()
    session = machine._sessions.get(session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Generate session state diagram
    diagram = f"""stateDiagram-v2
    [*] --> connected
    connected --> authenticated: authenticate
    authenticated --> idle: ready
    idle --> working: start_work
    working --> idle: stop_work
    working --> working: activity"""
    
    return {
        "id": session_id,
        "state": session.state,
        "project": session.project,
        "terminal_project": session.terminal_project,
        "preview_process": session.preview_process,
        "client_id": session.client_id,
        "client_name": session.client_name,
        "connected_at": session.connected_at.isoformat(),
        "last_activity": session.last_activity.isoformat(),
        "diagram": diagram,
        "current_state": session.state,
    }


@app.get("/events")
async def get_events(
    minutes: int = 30,
    event_type: Optional[str] = None,
    direction: Optional[str] = None,
    project: Optional[str] = None,
    limit: int = 500,
):
    """Get events from the event store."""
    from .event_store import get_event_store
    
    store = get_event_store()
    start_time = datetime.now() - timedelta(minutes=minutes)
    
    events = store.query(
        start_time=start_time,
        event_type=event_type,
        direction=direction,
        project=project,
        limit=limit,
    )
    
    return {"events": events, "count": len(events)}


@app.get("/events/stats")
async def get_event_stats(minutes: int = 30):
    """Get event statistics."""
    from .event_store import get_event_store
    
    store = get_event_store()
    return store.get_stats(minutes=minutes)


@app.get("/state")
async def get_ui_state():
    """Get current UI state for AI consumption."""
    from .db.repositories import get_task_repo, get_project_repo
    from .processes import get_process_manager
    from .vnc import get_vnc_manager
    
    try:
        task_repo = get_task_repo()
        project_repo = get_project_repo()
        pm = get_process_manager()
        vnc = get_vnc_manager()
        
        tasks = task_repo.list()
        projects = project_repo.list()
        
        processes = []
        for pid, proc in pm._processes.items():
            processes.append({
                "id": pid,
                "name": proc.get("name", pid),
                "status": proc.get("status", "unknown"),
                "port": proc.get("port"),
            })
        
        vnc_sessions = [
            {"id": s.id, "process_id": s.process_id, "status": s.status.value}
            for s in vnc.list_sessions()
        ]
        
        # Build available actions based on current state
        available_actions = []
        available_actions.append({"action": "SET_TAB", "options": ["tasks", "processes", "workers", "system"]})
        available_actions.append({"action": "SELECT_PROJECT", "options": [p.get("name") for p in projects]})
        
        for proc in processes:
            if proc["status"] == "running":
                available_actions.append({"action": "STOP_PROCESS", "target": proc["id"]})
                available_actions.append({"action": "START_PREVIEW", "target": proc["id"]})
            else:
                available_actions.append({"action": "START_PROCESS", "target": proc["id"]})
        
        return {
            "state": {
                "projects": [p.get("name") for p in projects],
                "tasks": [{"id": t.get("id"), "title": t.get("title"), "status": t.get("status")} for t in tasks[:20]],
                "processes": processes,
                "vncSessions": vnc_sessions,
            },
            "availableActions": available_actions,
        }
    except Exception as e:
        return {"error": str(e), "state": {}, "availableActions": []}


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


@app.get("/status")
async def status():
    """Get overall system status."""
    agents = agent_manager.list() if agent_manager else []
    queue_stats = task_repo.stats() if task_repo else {}
    
    return {
        "agents": {
            "total": len(agents),
            "running": len([a for a in agents if a.status not in (AgentStatus.STOPPED, AgentStatus.ERROR)]),
            "agents": [
                {
                    "project": a.project,
                    "status": a.status.value,
                    "provider": a.provider,
                    "task": a.current_task,
                }
                for a in agents
            ],
        },
        "queue": queue_stats,
        "connected_clients": len(connected_clients),
    }


# =============================================================================
# Available Models
# =============================================================================

_MODELS_CACHE_FILE = "models_cache.json"
_MODELS_IN_MEMORY: list[dict] | None = None


def _models_cache_path() -> Path:
    return get_rdc_home() / "data" / _MODELS_CACHE_FILE


def _cost_tier(prompt_cost: float, completion_cost: float) -> str:
    """Classify into cost tier based on per-token pricing."""
    avg = (prompt_cost + completion_cost) / 2
    if avg <= 0:
        return "free"
    if avg < 0.000002:
        return "cheap"        # < $2 / 1M tokens avg
    if avg < 0.000010:
        return "moderate"     # < $10 / 1M tokens avg
    if avg < 0.000030:
        return "expensive"    # < $30 / 1M tokens avg
    return "premium"          # $30+ / 1M tokens avg


def _classify_model(m: dict) -> dict:
    """Transform a raw OpenRouter model entry into our enriched format."""
    model_id = m.get("id", "")
    name = m.get("name", model_id)
    provider = model_id.split("/")[0] if "/" in model_id else ""

    pricing = m.get("pricing") or {}
    prompt_cost = float(pricing.get("prompt") or 0)
    completion_cost = float(pricing.get("completion") or 0)
    ctx = m.get("context_length") or 0

    supported = m.get("supported_parameters") or []
    has_reasoning = "reasoning" in supported or "include_reasoning" in supported
    has_tools = "tools" in supported
    has_vision = False
    arch = m.get("architecture") or {}
    input_mods = arch.get("input_modalities") or []
    if "image" in input_mods:
        has_vision = True

    # Build compact tags list
    tags: list[str] = []
    tier = _cost_tier(prompt_cost, completion_cost)
    tags.append(tier)
    if has_reasoning:
        tags.append("reasoning")
    if has_tools:
        tags.append("tools")
    if has_vision:
        tags.append("vision")
    if ctx >= 200_000:
        tags.append("long-context")

    # Build a richer label: "Name  [$tier] [reasoning] [tools]"
    tag_str = "  ".join(f"[{t}]" for t in tags)

    return {
        "id": model_id,
        "label": name,
        "provider": provider,
        "tags": tags,
        "cost_tier": tier,
        "context_length": ctx,
        "prompt_cost": prompt_cost,
        "completion_cost": completion_cost,
        "has_reasoning": has_reasoning,
        "has_tools": has_tools,
        "has_vision": has_vision,
        "tag_str": tag_str,
    }


def _load_models_from_disk() -> list[dict] | None:
    """Load cached models from disk. Cache never expires — refresh manually from settings."""
    path = _models_cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return data.get("models") or None
    except Exception:
        return None


def _save_models_to_disk(models: list[dict]) -> None:
    """Persist models to disk cache."""
    path = _models_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps({
            "cached_at": datetime.now().isoformat(),
            "count": len(models),
            "models": models,
        }, indent=2))
    except Exception as e:
        logger.warning(f"Failed to write models cache: {e}")


async def _fetch_and_classify_openrouter() -> list[dict]:
    """Fetch from OpenRouter, classify each model, return enriched list."""
    import httpx

    from .vault import get_secret
    api_key = get_secret("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            "https://openrouter.ai/api/v1/models",
            headers=headers,
        )
        resp.raise_for_status()

    data = resp.json()
    models = []
    for m in data.get("data", []):
        models.append(_classify_model(m))
    return models


async def _fetch_ollama_models() -> list[dict]:
    """Fetch available models from local Ollama."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get("http://localhost:11434/api/tags")
            if resp.status_code != 200:
                return []
        data = resp.json()
        models = []
        for m in data.get("models", []):
            name = m.get("name", "")
            display = name.replace(":latest", "")
            models.append({
                "id": f"ollama/{display}",
                "label": f"Ollama — {display}",
                "provider": "ollama",
                "tags": ["free", "local"],
                "cost_tier": "free",
                "context_length": 0,
                "prompt_cost": 0,
                "completion_cost": 0,
                "has_reasoning": False,
                "has_tools": False,
                "has_vision": False,
                "tag_str": "[free] [local]",
            })
        return models
    except Exception:
        return []


async def _get_all_models(refresh: bool = False) -> list[dict]:
    """Get models from memory → disk → API, with cascading fallback."""
    global _MODELS_IN_MEMORY

    if not refresh and _MODELS_IN_MEMORY is not None:
        return _MODELS_IN_MEMORY

    # Try disk cache (unless forcing refresh)
    if not refresh:
        disk = _load_models_from_disk()
        if disk:
            _MODELS_IN_MEMORY = disk
            return disk

    # Fetch live from both providers in parallel
    openrouter_models: list[dict] = []
    ollama_models: list[dict] = []
    try:
        openrouter_models, ollama_models = await asyncio.gather(
            _fetch_and_classify_openrouter(),
            _fetch_ollama_models(),
        )
    except Exception as e:
        logger.warning(f"Failed to fetch models: {e}")
        # Fall back to stale disk cache
        disk = _load_models_from_disk()
        if disk:
            _MODELS_IN_MEMORY = disk
            return disk
        if _MODELS_IN_MEMORY:
            return _MODELS_IN_MEMORY
        return []

    all_models = openrouter_models + ollama_models

    # Persist and cache in memory
    _save_models_to_disk(all_models)
    _MODELS_IN_MEMORY = all_models
    return all_models


@app.get("/models")
async def list_models(refresh: bool = False):
    """List available models from OpenRouter + local Ollama.

    Returns enriched model objects with cost tier, tags, and capability flags.
    Results are cached to disk (24h) and memory. Use ?refresh=true to re-fetch.
    """
    models = await _get_all_models(refresh=refresh)

    # Invalidate model router cache so it picks up new models
    if refresh:
        try:
            from .intent import _get_model_router
            _get_model_router().invalidate()
        except Exception:
            pass

    # Prepend the "Default" option
    return [{"id": "", "label": "Default", "provider": "", "tags": [], "cost_tier": "", "tag_str": ""}] + models


# =============================================================================
# Device Pairing
# =============================================================================

# In-memory pairing sessions: {id: {token: str|None, created: datetime}}
_pair_sessions: dict[str, dict] = {}

@app.post("/auth/pair")
async def create_pair_session():
    """Create a new pairing session (no auth required). Desktop calls this."""
    # Clean up expired sessions (older than 5 minutes)
    cutoff = datetime.now() - timedelta(minutes=5)
    expired = [k for k, v in _pair_sessions.items() if v["created"] < cutoff]
    for k in expired:
        del _pair_sessions[k]

    session_id = secrets.token_urlsafe(16)
    _pair_sessions[session_id] = {"token": None, "created": datetime.now()}
    return {"id": session_id}


@app.get("/auth/pair/{session_id}")
async def poll_pair_session(session_id: str):
    """Poll a pairing session (no auth required). Desktop polls this."""
    session = _pair_sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Pairing session not found or expired")
    if session["token"]:
        # Clean up after successful retrieval
        token = session["token"]
        del _pair_sessions[session_id]
        return {"status": "complete", "token": token}
    return {"status": "pending"}


@app.post("/auth/pair/{session_id}/approve")
async def approve_pair_session(session_id: str, request: Request):
    """Approve a pairing session (requires auth). Creates a new child token for the paired device."""
    session = _pair_sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Pairing session not found or expired")
    # Extract the caller's token from the Authorization header
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        parent_token = auth_header[7:]
    else:
        raise HTTPException(401, "Token required to approve pairing")
    # Get device name from body or User-Agent
    device_name = "Unknown Device"
    try:
        body = await request.json()
        device_name = body.get("device_name", device_name)
    except Exception:
        ua = request.headers.get("user-agent", "")
        if "Mobile" in ua:
            device_name = "Mobile"
        elif ua:
            device_name = "Desktop"
    # Create a new child token instead of sharing the raw token
    try:
        plain_token, _info = auth_manager.create_paired_token(parent_token, device_name)
    except ValueError:
        raise HTTPException(401, "Invalid token")
    session["token"] = plain_token
    return {"status": "approved"}


@app.get("/auth/me")
async def get_auth_me(request: Request):
    """Return identity info for the current token."""
    token_info = getattr(request.state, "token_info", None)
    if not token_info:
        raise HTTPException(401, "Not authenticated")
    return {
        "id": token_info.id,
        "name": token_info.name,
        "role": token_info.role.value,
        "device_name": token_info.device_name,
        "parent_token_id": token_info.parent_token_id,
        "is_parent": token_info.parent_token_id is None,
    }


@app.get("/auth/sessions")
async def list_paired_sessions(request: Request):
    """List paired device sessions scoped to the caller's token."""
    token_info = getattr(request.state, "token_info", None)
    if token_info:
        sessions = auth_manager.list_paired_sessions_for_token(token_info)
    else:
        sessions = auth_manager.list_paired_sessions()
    return [s.model_dump(mode="json") for s in sessions]


@app.delete("/auth/sessions/{token_id}")
async def revoke_paired_session(token_id: str, request: Request):
    """Revoke a paired device session with ownership check."""
    caller = getattr(request.state, "token_info", None)
    target = auth_manager.get_token_by_id(token_id)
    if not target or target.revoked:
        raise HTTPException(404, "Paired session not found")

    # Ownership check
    if caller:
        allowed = (
            caller.id == token_id  # self-disconnect
            or caller.id == target.parent_token_id  # parent revoking child
        )
        if not allowed:
            raise HTTPException(403, "Not allowed to revoke this session")

    if auth_manager.revoke_token(token_id):
        return {"status": "revoked"}
    raise HTTPException(404, "Session not found")


# =============================================================================
# Agent Management
# =============================================================================

@app.get("/agents")
async def list_agents():
    """List all agents."""
    agents = agent_manager.list() if agent_manager else []
    return [
        {
            "project": a.project,
            "status": a.status.value,
            "provider": a.provider,
            "pid": a.pid,
            "task": a.current_task,
            "worktree": a.worktree,
            "started_at": a.started_at.isoformat() if a.started_at else None,
            "last_activity": a.last_activity.isoformat() if a.last_activity else None,
            "error": a.error,
        }
        for a in agents
    ]


@app.post("/agents/spawn")
async def spawn_agent(req: SpawnRequest, request: Request):
    """Spawn a new agent."""
    token_info = getattr(request.state, "token_info", None)
    
    try:
        state = agent_manager.spawn(
            project=req.project,
            provider=req.provider,
            task=req.task,
            worktree=req.worktree,
        )
        
        audit(
            AuditAction.AGENT_SPAWN,
            actor_type="user" if token_info else "system",
            actor_id=token_info.id if token_info else None,
            resource_type="agent",
            resource_id=req.project,
            channel="api",
            metadata={"provider": state.provider, "task": req.task[:100] if req.task else None},
        )
        
        event_bus.emit(
            EventType.AGENT_SPAWNED,
            project=req.project,
            provider=state.provider,
            pid=state.pid,
        )
        
        return {
            "success": True,
            "agent": {
                "project": state.project,
                "status": state.status.value,
                "provider": state.provider,
                "pid": state.pid,
            },
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/agents/{project}/stop")
async def stop_agent(project: str, force: bool = False):
    """Stop an agent."""
    success = agent_manager.stop(project, force=force)
    
    if success:
        event_bus.emit(EventType.AGENT_STOPPED, project=project)
        return {"success": True}
    else:
        raise HTTPException(status_code=404, detail=f"Agent not found: {project}")


class RetryRequest(BaseModel):
    task: str | None = None  # Optional new task, otherwise retry with same


@app.post("/agents/{project}/retry")
async def retry_agent(project: str, request: Request, body: RetryRequest | None = None):
    """Retry a failed agent with optional new task."""
    agent = agent_manager.get(project)
    
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent not found: {project}")
    
    # Allow retry from error or stopped state
    if agent.status not in (AgentStatus.ERROR, AgentStatus.STOPPED):
        raise HTTPException(status_code=400, detail=f"Agent cannot be retried from state: {agent.status}")
    
    # Get task - use new one if provided, otherwise use the previous task
    task = None
    if body and body.task:
        task = body.task
    elif agent.current_task:
        task = agent.current_task
    
    try:
        # Stop first to clean up
        agent_manager.stop(project, force=True)
        
        # Respawn with task
        state = agent_manager.spawn(project, task=task)
        
        # Log to SQLite
        event_repo.log(
            "agent.retry",
            project=project,
            message=f"Agent retried with task: {task[:50] if task else 'none'}",
        )
        
        event_bus.emit(EventType.AGENT_STARTED, project=project, task=task)
        
        token_info = getattr(request.state, "token_info", None)
        audit(
            AuditAction.AGENT_RETRY,
            actor_type="user" if token_info else "system",
            actor_id=token_info.id if token_info else None,
            resource_type="agent",
            resource_id=project,
            channel="api",
        )
        
        return {
            "success": True,
            "agent": {
                "project": state.project,
                "status": state.status.value,
                "pid": state.pid,
                "task": state.current_task,
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/agents/{project}")
async def get_agent(project: str):
    """Get agent status."""
    agent = agent_manager.get(project)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent not found: {project}")
    
    return {
        "project": agent.project,
        "status": agent.status.value,
        "provider": agent.provider,
        "pid": agent.pid,
        "task": agent.current_task,
        "worktree": agent.worktree,
        "started_at": agent.started_at.isoformat() if agent.started_at else None,
        "last_activity": agent.last_activity.isoformat() if agent.last_activity else None,
        "error": agent.error,
        "retry_count": agent.retry_count,
    }


@app.get("/agents/{project}/logs")
async def get_agent_logs(project: str, lines: int = 100):
    """Get agent logs."""
    logs = agent_manager.get_logs(project, lines=lines)
    return {"project": project, "logs": logs}


@app.post("/agents/{project}/assign")
async def assign_to_agent(project: str, req: AssignRequest):
    """Assign a task to an agent."""
    try:
        state = agent_manager.assign_task(project, req.task)
        
        event_bus.emit(
            EventType.TASK_ASSIGNED,
            project=project,
            task=req.task,
        )
        
        return {
            "success": True,
            "agent": {
                "project": state.project,
                "status": state.status.value,
                "task": state.current_task,
            },
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# =============================================================================
# Task Queue
# =============================================================================

@app.get("/tasks")
async def list_tasks(
    project: str | None = None,
    status: str | None = None,
    include_completed: bool = True,
):
    """List tasks in the queue."""
    from .db.models import TaskStatus as DBTaskStatus
    
    status_filter = None
    if status:
        try:
            status_filter = DBTaskStatus(status)
        except ValueError:
            pass
    
    # Use SQLite repository — resolve name to UUID for filtering
    proj_id = resolve_project_id(project) if project else None
    tasks = task_repo.list(status=status_filter, project_id=proj_id, limit=100)
    
    # Filter out cancelled unless requested
    tasks = [t for t in tasks if t.status != DBTaskStatus.CANCELLED]
    
    return [
        {
            "id": t.id,
            "project": t.project,
            "description": t.description,
            "priority": t.priority.value,
            "status": t.status.value,
            "assigned_to": t.assigned_to,
            "created_at": t.created_at.isoformat(),
            "started_at": t.started_at.isoformat() if t.started_at else None,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
            "result": t.result,
            "error": t.error,
            "metadata": t.metadata,
        }
        for t in tasks
    ]


@app.get("/recipes")
async def get_recipes():
    """Return list of available recipes (built-in + user)."""
    from .recipes import list_recipes
    result = list_recipes()
    logging.info(f"GET /recipes returning {len(result)} recipes")
    return result


@app.post("/recipes")
async def create_recipe(request: Request):
    """Create a user recipe."""
    from .db.models import RecipeModel
    from .db.repositories import get_recipe_repo

    body = await request.json()
    name = (body.get("name") or "").strip()
    prompt_template = (body.get("prompt_template") or "").strip()
    if not name or not prompt_template:
        raise HTTPException(status_code=400, detail="name and prompt_template are required")

    recipe = RecipeModel(
        name=name,
        description=body.get("description", ""),
        prompt_template=prompt_template,
        model=body.get("model"),
        inputs=body.get("inputs"),
        tags=body.get("tags"),
    )
    created = get_recipe_repo().create(recipe)
    return {"id": created.id, "name": created.name}


@app.patch("/recipes/{recipe_id}")
async def update_recipe(recipe_id: str, request: Request):
    """Update a recipe. For built-ins, creates a DB override."""
    from .recipes import BUILTIN_RECIPES
    from .db.models import RecipeModel
    from .db.repositories import get_recipe_repo

    repo = get_recipe_repo()
    existing = repo.get(recipe_id)
    body = await request.json()

    if existing:
        # Update existing DB record
        if "name" in body:
            existing.name = body["name"]
        if "description" in body:
            existing.description = body["description"]
        if "prompt_template" in body:
            existing.prompt_template = body["prompt_template"]
        if "model" in body:
            existing.model = body["model"]
        if "inputs" in body:
            existing.inputs = body["inputs"]
        if "tags" in body:
            existing.tags = body["tags"]
        updated = repo.update(existing)
        return {"id": updated.id, "name": updated.name}

    # No DB record — must be a built-in, create a DB override
    builtin = BUILTIN_RECIPES.get(recipe_id)
    if not builtin:
        raise HTTPException(status_code=404, detail="Recipe not found")

    override = RecipeModel(
        id=recipe_id,
        name=body.get("name", builtin.name),
        description=body.get("description", builtin.description),
        prompt_template=body.get("prompt_template", builtin.prompt_template),
        model=body.get("model", builtin.model),
        inputs=body.get("inputs", builtin.inputs),
        tags=body.get("tags", list(builtin.tags)),
    )
    created = repo.create(override)
    return {"id": created.id, "name": created.name}


@app.delete("/recipes/{recipe_id}")
async def delete_recipe(recipe_id: str):
    """Delete a recipe. For built-in overrides, restores the default."""
    from .recipes import BUILTIN_RECIPES
    from .db.repositories import get_recipe_repo

    repo = get_recipe_repo()
    deleted = repo.delete(recipe_id)

    if not deleted and recipe_id not in BUILTIN_RECIPES:
        raise HTTPException(status_code=404, detail="Recipe not found")

    if recipe_id in BUILTIN_RECIPES:
        return {"ok": True, "restored": True}
    return {"ok": True}


@app.post("/tasks")
async def create_task(req: TaskRequest, request: Request):
    """Create a new task."""
    from .db.models import TaskPriority as DBTaskPriority, TaskStatus as DBTaskStatus

    try:
        priority = DBTaskPriority(req.priority)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid priority: {req.priority}")

    # Handle recipe: use user-provided description if present, else render from template
    description = req.description
    metadata = None
    model = req.model  # explicit model from request takes priority
    if req.recipe_id:
        from .recipes import render_recipe, BUILTIN_RECIPES
        from .db.repositories import get_recipe_repo

        # Resolve recipe name and model for display
        recipe_name = req.recipe_id
        builtin = BUILTIN_RECIPES.get(req.recipe_id)
        if builtin:
            recipe_name = builtin.name
            if not model:
                model = builtin.model
        else:
            try:
                db_recipe = get_recipe_repo().get(req.recipe_id)
                if db_recipe:
                    recipe_name = db_recipe.name
                    if not model:
                        model = db_recipe.model
            except Exception:
                pass

        if not description.strip():
            # No user-provided description — render from template
            rendered = render_recipe(req.recipe_id, req.project or "default")
            if rendered is None:
                raise HTTPException(status_code=400, detail=f"Unknown recipe: {req.recipe_id}")
            description = rendered

        metadata = {"recipe_id": req.recipe_id, "recipe_name": recipe_name}

    # Store model in metadata so the worker can read it
    if model:
        metadata = metadata or {}
        metadata["model"] = model

    # Store provider in metadata so the worker can dispatch correctly
    if req.provider:
        metadata = metadata or {}
        metadata["provider"] = req.provider

    if not description.strip():
        raise HTTPException(status_code=400, detail="Task description is required")

    # Use SQLite repository — resolve project name to UUID
    proj_id = resolve_project_id(req.project or "default") or ""
    task = task_repo.create(
        project_id=proj_id,
        description=description,
        priority=priority,
        parent_task_id=req.parent_task_id,
        metadata=metadata,
    )
    
    # If requires review, update status
    if req.requires_review:
        task = task_repo.request_review(task.id, req.review_prompt or req.description) or task
    
    # Log event to SQLite
    event_repo.log(
        "task.created",
        project=req.project,
        task_id=task.id,
        message=f"Task created{' (requires review)' if req.requires_review else ''}: {req.description[:50]}",
    )
    
    event_bus.emit(
        EventType.TASK_CREATED,
        project=req.project,
        task_id=task.id,
        description=req.description,
    )

    # Broadcast state so connected dashboards see the new task immediately
    await get_state_machine()._broadcast_state()

    token_info = getattr(request.state, "token_info", None)
    audit(
        AuditAction.TASK_CREATED,
        actor_type="user" if token_info else "system",
        actor_id=token_info.id if token_info else None,
        resource_type="task",
        resource_id=task.id,
        channel="api",
        metadata={"project": req.project, "priority": req.priority},
    )
    
    return {
        "success": True,
        "task": {
            "id": task.id,
            "project": task.project,
            "description": task.description,
            "priority": task.priority.value,
            "status": task.status.value,
        },
    }


@app.get("/tasks/pending-review")
async def get_pending_review():
    """Get all tasks awaiting human review."""
    from .db.models import TaskStatus as DBTaskStatus
    
    tasks = task_repo.list(status=DBTaskStatus.AWAITING_REVIEW, limit=50)
    
    return [
        {
            "id": t.id,
            "project": t.project,
            "description": t.description,
            "priority": t.priority.value,
            "review_prompt": getattr(t, 'review_prompt', None),
            "created_at": t.created_at.isoformat(),
        }
        for t in tasks
    ]


@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """Get a task by ID."""
    task = task_repo.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    
    return {
        "id": task.id,
        "project": task.project,
        "description": task.description,
        "priority": task.priority.value,
        "status": task.status.value,
        "assigned_to": task.assigned_to,
        "created_at": task.created_at.isoformat(),
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "result": task.result,
        "error": task.error,
        "retry_count": task.retry_count,
    }


class TaskUpdate(BaseModel):
    status: str | None = None
    description: str | None = None
    priority: str | None = None


@app.patch("/tasks/{task_id}")
async def update_task(task_id: str, update: TaskUpdate, request: Request):
    """Update a task's fields."""
    task = task_repo.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    
    if update.status == "cancelled":
        task = task_repo.cancel(task_id)
        if task:
            event_repo.log("task.cancelled", project=task.project, task_id=task.id)
            await get_state_machine()._broadcast_state()
            return {"success": True, "task": {"id": task.id, "status": task.status.value}}
        raise HTTPException(status_code=400, detail="Cannot cancel task")
    
    raise HTTPException(status_code=400, detail="Unsupported update operation")


@app.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request):
    """Cancel a task."""
    task = task_repo.cancel(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found or cannot be cancelled: {task_id}")
    
    event_repo.log(
        "task.cancelled",
        project=task.project,
        task_id=task.id,
        message="Task cancelled",
    )
    
    token_info = getattr(request.state, "token_info", None)
    audit(
        AuditAction.TASK_CANCELLED,
        actor_type="user" if token_info else "system",
        actor_id=token_info.id if token_info else None,
        resource_type="task",
        resource_id=task_id,
        channel="api",
    )
    
    return {"success": True, "task_id": task_id}


class TaskRetryRequest(BaseModel):
    description: str | None = None  # Optional new description
    priority: str | None = None  # Optional new priority
    model: str | None = None  # Optional model override
    provider: str | None = None  # Optional provider override ("cursor", "web")


class ChainedTaskRequest(BaseModel):
    project: str
    description: str
    priority: str = "normal"
    depends_on: list[str] | None = None  # Task IDs to wait for
    use_output_from: str | None = None  # Task ID whose output to inject as {{output}}


@app.post("/tasks/chain")
async def create_chained_task(req: ChainedTaskRequest, request: Request):
    """Create a task that depends on other tasks.
    
    The description can include {{output}} which will be replaced with
    the output from the use_output_from task when it runs.
    """
    from .db.models import TaskPriority as DBTaskPriority
    
    try:
        priority = DBTaskPriority(req.priority)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid priority: {req.priority}")
    
    # Build dependencies list
    depends_on = req.depends_on or []
    if req.use_output_from and req.use_output_from not in depends_on:
        depends_on.append(req.use_output_from)
    
    # If using output from another task, check if it's complete and substitute
    description = req.description
    if req.use_output_from:
        source_task = task_repo.get(req.use_output_from)
        if source_task and source_task.status.value == "completed" and source_task.output:
            # Substitute output into description
            description = description.replace("{{output}}", source_task.output)
    
    chain_proj_id = resolve_project_id(req.project) or ""
    task = task_repo.create(
        project_id=chain_proj_id,
        description=description,
        priority=priority,
        depends_on=depends_on if depends_on else None,
        metadata={"use_output_from": req.use_output_from} if req.use_output_from else None,
    )
    
    event_bus.emit(
        EventType.TASK_CREATED,
        project=req.project,
        task_id=task.id,
    )
    
    return {
        "success": True,
        "task": {
            "id": task.id,
            "project": task.project,
            "description": task.description,
            "priority": task.priority.value,
            "status": task.status.value,
            "depends_on": task.depends_on,
        },
    }


@app.get("/tasks/{task_id}/output")
async def get_task_output(task_id: str):
    """Get the captured output from a completed task."""
    task = task_repo.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    
    return {
        "task_id": task_id,
        "status": task.status.value,
        "output": task.output,
        "output_artifacts": task.output_artifacts,
    }


class ReviewDecision(BaseModel):
    approved: bool
    comment: str | None = None
    modified_description: str | None = None  # Allow reviewer to edit task


@app.post("/tasks/{task_id}/review")
async def review_task(task_id: str, decision: ReviewDecision, request: Request):
    """Approve or reject a task awaiting review."""
    from .db.models import TaskStatus as DBTaskStatus
    
    task = task_repo.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    
    if task.status != DBTaskStatus.AWAITING_REVIEW:
        raise HTTPException(status_code=400, detail=f"Task is not awaiting review: {task.status}")
    
    token_info = getattr(request.state, "token_info", None)
    reviewer_id = token_info.id if token_info else "anonymous"
    
    if decision.approved:
        # Mark as pending so orchestrator picks it up
        task_repo.approve(task_id, reviewer_id, decision.modified_description)
        
        event_repo.log(
            "task.approved",
            project=task.project,
            task_id=task_id,
            message=f"Approved by {reviewer_id}",
        )
        
        return {"success": True, "action": "approved", "task_id": task_id}
    else:
        # Rejected - cancel the task
        task_repo.cancel(task_id)
        
        event_repo.log(
            "task.rejected",
            project=task.project,
            task_id=task_id,
            message=f"Rejected by {reviewer_id}: {decision.comment or 'no reason'}",
        )
        
        return {"success": True, "action": "rejected", "task_id": task_id}


@app.post("/tasks/{task_id}/retry")
async def retry_task(task_id: str, request: Request, body: TaskRetryRequest | None = None):
    """Retry a failed task (creates a new task with same or updated params)."""
    from .db.models import TaskPriority as DBTaskPriority
    
    original = task_repo.get(task_id)
    if not original:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    
    # Use provided values or fall back to original
    description = body.description if body and body.description else original.description
    priority = original.priority
    if body and body.priority:
        try:
            priority = DBTaskPriority(body.priority)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid priority: {body.priority}")
    
    # Create new task — use the original task's project_id
    retry_proj_id = original.project_id or resolve_project_id(original.project) or ""
    # Build metadata: carry over model/provider from original, allow overrides
    orig_meta = original.metadata or {}
    retry_meta: dict = {"retried_from": task_id}
    # Carry over original model/provider, then apply overrides
    if orig_meta.get("model"):
        retry_meta["model"] = orig_meta["model"]
    if orig_meta.get("provider"):
        retry_meta["provider"] = orig_meta["provider"]
    if body and body.model is not None:
        retry_meta["model"] = body.model if body.model else retry_meta.pop("model", None)
    if body and body.provider is not None:
        retry_meta["provider"] = body.provider if body.provider else retry_meta.pop("provider", None)

    new_task = task_repo.create(
        project_id=retry_proj_id,
        description=description,
        priority=priority,
        metadata=retry_meta,
    )
    
    event_repo.log(
        "task.retried",
        project=original.project,
        task_id=new_task.id,
        message=f"Retried from {task_id}",
    )
    
    event_bus.emit(
        EventType.TASK_CREATED,
        project=original.project,
        task_id=new_task.id,
        description=description,
    )
    
    return {
        "success": True,
        "original_task_id": task_id,
        "new_task": {
            "id": new_task.id,
            "project": new_task.project,
            "description": new_task.description,
            "priority": new_task.priority.value,
            "status": new_task.status.value,
        },
    }


@app.post("/tasks/{task_id}/run")
async def run_task_now(task_id: str, request: Request):
    """Queue a task for execution by the worker.
    
    The task is marked as pending with high priority so the worker picks it up next.
    The actual agent spawning is handled by the worker process.
    """
    task = task_repo.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    
    from .db.models import TaskStatus as DBTaskStatus, TaskPriority as DBTaskPriority
    
    if task.status not in (DBTaskStatus.PENDING, DBTaskStatus.BLOCKED):
        raise HTTPException(status_code=400, detail=f"Task is not pending: {task.status}")

    task_repo.requeue(task_id)
    
    event_repo.log(
        "task.queued",
        project=task.project,
        task_id=task_id,
        message=f"Task queued for execution: {task.description[:50]}",
    )
    
    await get_state_machine()._broadcast_state()
    
    return {
        "success": True,
        "task_id": task_id,
        "message": "Task queued for worker execution",
        "status": "pending",
    }


@app.get("/tasks/stats")
async def task_stats():
    """Get queue statistics."""
    return task_repo.stats()


@app.delete("/tasks/{task_id}")
async def delete_task(task_id: str, request: Request):
    """Delete a completed/failed/cancelled task."""
    task = task_repo.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    try:
        deleted = task_repo.delete(task_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=400, detail="Failed to delete task")

    event_repo.log("task.deleted", project=task.project, task_id=task.id,
                   message=f"Task deleted: {task.description[:50]}")
    machine = get_state_machine()
    await machine._broadcast_state()
    return {"success": True, "task_id": task_id}


class TaskCleanupRequest(BaseModel):
    status: str | None = None  # "failed", "completed", "cancelled"
    project: str | None = None
    older_than_hours: int | None = None


@app.post("/tasks/cleanup")
async def cleanup_tasks(req: TaskCleanupRequest, request: Request):
    """Bulk-delete finished tasks matching filters."""
    project_id = None
    if req.project:
        project_id = resolve_project_id(req.project)
    count = task_repo.delete_batch(
        status=req.status,
        project_id=project_id,
        older_than_hours=req.older_than_hours,
    )
    if count > 0:
        event_repo.log("tasks.cleanup", message=f"Cleaned up {count} tasks")
        machine = get_state_machine()
        await machine._broadcast_state()
    return {"deleted": count}


# =============================================================================
# Worker Status
# =============================================================================

@app.get("/workers")
async def list_workers():
    """List all registered workers."""
    from .db.connection import get_db
    
    db = get_db("logs")
    rows = db.execute("""
        SELECT id, hostname, pid, status, last_heartbeat, max_concurrent, current_load, started_at
        FROM workers
        ORDER BY last_heartbeat DESC
    """).fetchall()
    
    workers = []
    for row in rows:
        workers.append({
            "id": row[0],
            "hostname": row[1],
            "pid": row[2],
            "status": row[3],
            "last_heartbeat": row[4],
            "max_concurrent": row[5],
            "current_load": row[6],
            "started_at": row[7],
        })
    
    return {"workers": workers}


@app.get("/workers/status")
async def worker_status():
    """Get worker system status."""
    from .db.connection import get_db
    from datetime import datetime, timedelta
    
    logs_db = get_db("logs")
    tasks_db = get_db("tasks")
    
    # Count workers by status
    cutoff = (datetime.now() - timedelta(seconds=60)).isoformat()
    
    active = logs_db.execute("""
        SELECT COUNT(*) FROM workers WHERE status = 'running' AND last_heartbeat >= ?
    """, (cutoff,)).fetchone()[0]
    
    total = logs_db.execute("SELECT COUNT(*) FROM workers").fetchone()[0]
    
    # Task queue stats
    pending = tasks_db.execute("SELECT COUNT(*) FROM tasks WHERE status = 'pending'").fetchone()[0]
    in_progress = tasks_db.execute("SELECT COUNT(*) FROM tasks WHERE status = 'in_progress'").fetchone()[0]
    claimed = tasks_db.execute("SELECT COUNT(*) FROM tasks WHERE claimed_by IS NOT NULL AND status = 'in_progress'").fetchone()[0]
    
    return {
        "workers": {
            "active": active,
            "total": total,
        },
        "tasks": {
            "pending": pending,
            "in_progress": in_progress,
            "claimed": claimed,
        },
        "healthy": active > 0 or pending == 0,
    }


# =============================================================================
# Process Management (Dev Servers, etc.)
# =============================================================================

class ProcessRegisterRequest(BaseModel):
    project: str
    name: str
    command: str
    cwd: str
    port: int | None = None
    kind: str = "service"


@app.get("/actions")
async def list_processes(project: str | None = None):
    """List all managed processes."""
    processes = process_manager.list(project=project)
    return [
        {
            "id": p.id,
            "project": p.project,
            "name": p.name,
            "type": p.process_type.value,
            "command": p.command,
            "status": p.status.value,
            "pid": p.pid,
            "port": p.port,
            "started_at": p.started_at.isoformat() if p.started_at else None,
            "error": p.error,
        }
        for p in processes
    ]


@app.post("/actions/register")
async def register_process(req: ProcessRegisterRequest):
    """Register a new process configuration."""
    from .db.models import ActionKind
    kind = ActionKind.COMMAND if req.kind == "command" else ActionKind.SERVICE
    state = process_manager.register(
        project=req.project,
        name=req.name,
        command=req.command,
        cwd=req.cwd,
        port=req.port,
        kind=kind,
    )
    machine = get_state_machine()
    await machine._broadcast_state()
    return {"success": True, "process": {"id": state.id, "status": state.status.value}}


class SuggestActionRequest(BaseModel):
    project: str
    description: str


@app.post("/actions/suggest")
async def suggest_action(req: SuggestActionRequest):
    """Ask LLM to suggest an action based on a natural language description."""
    from .process_discovery import read_project_files
    from ..llm import llm_generate

    project_repo = get_project_repo()
    project = project_repo.get(req.project)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{req.project}' not found")

    # Read project files for context
    files = read_project_files(project.path)
    files_text = ""
    for filename, content in files.items():
        files_text += f"\n--- {filename} ---\n{content}\n"

    # Get existing actions for dedup awareness
    existing = process_manager.list(project=req.project)
    existing_text = ", ".join(f"{p.name} ({p.command})" for p in existing) if existing else "none"

    prompt = f"""A user wants to add an action to their project. Based on the project files, suggest the right command.

User request: "{req.description}"

Project: {req.project}
Project path: {project.path}
Existing actions: {existing_text}

Project files:
{files_text}

Return ONLY valid JSON (no markdown, no explanation):
{{
  "name": "short-name (e.g. lint, test, build, migrate)",
  "command": "the shell command to run (e.g. npm run lint, pytest, make lint)",
  "kind": "service or command (use service only for long-running servers with ports, command for everything else)",
  "port": null,
  "cwd": "subdirectory to run from, or null for project root. IMPORTANT: if the command relates to a package.json in a subdirectory like frontend/, set cwd to that subdirectory"
}}

Rules:
- kind should be "command" for build, test, lint, migrate, format, typecheck, etc.
- kind should be "service" only for long-running dev servers, APIs, workers
- port should only be set for services that listen on a port
- Use the actual commands from package.json scripts, Makefile targets, pyproject.toml scripts, etc.
- If unsure about cwd, use null (project root)
"""

    result = llm_generate(prompt, format_json=True)
    if not result or not isinstance(result, dict):
        raise HTTPException(status_code=500, detail="LLM failed to suggest an action")

    # Ensure required fields
    return {
        "name": result.get("name", "action"),
        "command": result.get("command", ""),
        "kind": result.get("kind", "command"),
        "port": result.get("port"),
        "cwd": result.get("cwd"),
    }


@app.post("/actions/{process_id}/start")
async def start_process(process_id: str, force: bool = False):
    """Start a registered process.

    Args:
        process_id: ID of the process to start
        force: If True, kill any process using the port before starting
    """
    try:
        state = process_manager.start(process_id, force=force)
        event_repo.log(
            "process.started",
            project=state.project,
            message=f"Started {state.name}: {state.command}",
        )
        machine = get_state_machine()
        await machine._broadcast_state()
        return {
            "success": True,
            "process": {
                "id": state.id,
                "status": state.status.value,
                "pid": state.pid,
                "port": state.port,
            },
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/actions/{process_id}/stop")
async def stop_process(process_id: str, force: bool = False):
    """Stop a running process."""
    try:
        state = process_manager.stop(process_id, force=force)
        event_repo.log(
            "process.stopped",
            project=state.project,
            message=f"Stopped {state.name}",
        )
        machine = get_state_machine()
        await machine._broadcast_state()
        return {"success": True, "process": {"id": state.id, "status": state.status.value}}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/actions/{process_id}/restart")
async def restart_process(process_id: str):
    """Restart a process."""
    try:
        state = process_manager.restart(process_id)
        event_repo.log(
            "process.restarted",
            project=state.project,
            message=f"Restarted {state.name}",
        )
        machine = get_state_machine()
        await machine._broadcast_state()
        return {
            "success": True,
            "process": {
                "id": state.id,
                "status": state.status.value,
                "pid": state.pid,
            },
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/ports/{port}/info")
async def get_port_info(port: int):
    """Get information about process running on a port."""
    info = process_manager.get_port_process_info(port)
    if not info:
        return {"port": port, "in_use": False}
    return {"port": port, "in_use": True, **info}


@app.post("/ports/{port}/kill")
async def kill_port(port: int, force: bool = False):
    """Kill any process using the specified port.
    
    Args:
        port: Port number to free up
        force: If True, use SIGKILL instead of SIGTERM
    """
    result = process_manager.kill_port(port, force=force)
    event_repo.log(
        "port.killed",
        message=f"Killed processes on port {port}: {result['killed_pids']}",
    )
    return result


@app.post("/actions/{process_id}/attach")
async def attach_to_process(process_id: str, port: int | None = None):
    """Attach to an existing process.

    If port is provided, finds the process listening on that port (original behavior).
    If port is omitted, searches for a running process matching the action's
    command and cwd (for services without a port like 'uv run main.py').
    """
    if port is not None:
        state, verified = process_manager.attach_to_port(process_id, port)
        if not state or not verified:
            raise HTTPException(
                status_code=400,
                detail=f"No running process found on port {port} — nothing to attach to",
            )
        event_repo.log(
            "process.attached",
            message=f"Attached {process_id} to PID {state.pid} on port {port}",
        )
    else:
        state, verified = process_manager.attach_to_command(process_id)
        if not state or not verified:
            raise HTTPException(
                status_code=400,
                detail="No running process found matching this action's command — nothing to attach to",
            )
        event_repo.log(
            "process.attached",
            message=f"Attached {process_id} to PID {state.pid} by command match",
        )

    await get_state_machine()._broadcast_state()
    return {
        "id": state.id,
        "pid": state.pid,
        "port": state.port,
        "status": state.status.value,
    }


@app.post("/system/kill/{pid}")
async def kill_pid(pid: int, force: bool = False):
    """Kill a process by PID."""
    import signal
    
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.kill(pid, sig)
        event_repo.log(
            "system.kill",
            message=f"Killed PID {pid} with signal {sig.name}",
        )
        return {"success": True, "pid": pid, "signal": sig.name}
    except ProcessLookupError:
        raise HTTPException(status_code=404, detail=f"Process not found: {pid}")
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied to kill PID: {pid}")


@app.get("/actions/{process_id}/logs")
async def get_process_logs(process_id: str, lines: int = 100):
    """Get logs for a process."""
    state = process_manager.get(process_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Process not found: {process_id}")
    
    logs = process_manager.get_logs(process_id, lines=lines)
    return {"process_id": process_id, "logs": logs}


@app.post("/actions/{process_id}/create-fix-task")
async def create_fix_task_from_process(process_id: str):
    """Create a task to fix a failed process error."""
    from .db.models import TaskPriority as DBTaskPriority
    
    state = process_manager.get(process_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Process not found: {process_id}")
    
    if state.status.value != "failed":
        raise HTTPException(status_code=400, detail="Process is not in failed state")
    
    # Get error details
    error_msg = state.error or "Unknown error"
    logs = process_manager.get_logs(process_id, lines=50)
    
    # Create task description
    description = f"""Fix the {state.name} process error for {state.project}.

Command that failed: {state.command}

Error:
{error_msg}

Recent logs:
{logs[-2000:] if len(logs) > 2000 else logs}
"""
    
    fix_proj_id = resolve_project_id(state.project) or ""
    task = task_repo.create(
        project_id=fix_proj_id,
        description=description,
        priority=DBTaskPriority.HIGH,
        metadata={"source": "process_error", "process_id": process_id},
    )
    
    event_repo.log(
        "task.created_from_error",
        project=state.project,
        task_id=task.id,
        message=f"Created fix task from failed process: {state.name}",
    )
    
    return {
        "success": True,
        "task": {
            "id": task.id,
            "project": task.project,
            "description": task.description[:100] + "...",
            "priority": task.priority.value,
        },
    }


@app.post("/projects/{project}/detect-actions")
async def detect_project_processes(project: str, use_llm: bool = True, force_rediscover: bool = False):
    """Auto-detect and register dev processes for a project.
    
    Uses cached discovery if available, or LLM (Ollama) for fresh discovery.
    Set force_rediscover=True to bypass cache and re-run LLM.
    """
    from .process_discovery import discover_processes, DiscoveredProcess
    from .ports import get_port_manager
    from .db.repositories import get_process_config_repo, get_project_repo
    from .db.models import ProcessConfig

    # Look up project in DB
    db_proj = get_project_repo().get(project)
    if not db_proj:
        raise HTTPException(status_code=404, detail=f"Project not found: {project}")
    project_path = db_proj.path
    port_manager = get_port_manager()
    process_config_repo = get_process_config_repo()

    # Resolve project name to UUID for DB operations
    proj_uuid = resolve_project_id(project) or _ensure_project_in_db(project)

    # If forcing rediscovery, clear old cached configs for this project
    if force_rediscover:
        process_config_repo.delete_by_project(proj_uuid)

        # Also remove in-memory entries for this project
        for proc in list(process_manager.list(project=project)):
            process_manager.remove(proc.id)

    # Priority 1: Always check rdc.yaml/adt.yaml first (user-defined, takes precedence over cache)
    from .process_discovery import load_adt_config as load_project_rdc_config
    discovered = load_project_rdc_config(project_path) or []
    from_cache = False

    if discovered:
        # rdc.yaml found — update DB cache and remove stale entries
        rdc_ids = {f"{project}-{proc.name}".lower().replace(" ", "-") for proc in discovered}

        # Remove DB configs, in-memory processes, and port assignments not in rdc.yaml
        existing_configs = process_config_repo.list(proj_uuid)
        for cfg in existing_configs:
            if cfg.id not in rdc_ids:
                process_config_repo.delete(cfg.id)
                process_manager.remove(cfg.id)
                # Clean up stale port assignment
                port_manager.release_port(project, cfg.name)

        for proc in discovered:
            process_config_repo.upsert(ProcessConfig(
                id=f"{project}-{proc.name}",
                project_id=proj_uuid,
                name=proc.name,
                command=proc.command,
                cwd=proc.cwd,
                port=proc.default_port,
                description=proc.description,
                discovered_by="rdc.yaml",
            ))
    else:
        # Priority 2: Check DB cache (unless forcing rediscovery)
        if not force_rediscover:
            configs = process_config_repo.list(proj_uuid)
            if configs:
                discovered = [
                    DiscoveredProcess(
                        name=cfg.name,
                        command=cfg.command,
                        description=cfg.description or "",
                        default_port=cfg.port,
                        cwd=cfg.cwd,
                    )
                    for cfg in configs
                ]
                from_cache = True

        # Priority 3: LLM / heuristic discovery
        if not discovered:
            discovered = discover_processes(project, project_path, use_llm=use_llm)

            # Save to DB cache
            if discovered:
                for proc in discovered:
                    process_config_repo.upsert(ProcessConfig(
                        id=f"{project}-{proc.name}",
                        project_id=proj_uuid,
                        name=proc.name,
                        command=proc.command,
                        cwd=proc.cwd,
                        port=proc.default_port,
                        description=proc.description,
                        discovered_by="llm" if use_llm else "heuristics",
                    ))
    
    registered = []
    for proc in discovered:
        # Determine the working directory
        if proc.cwd:
            cwd = str(Path(project_path) / proc.cwd)
        else:
            cwd = project_path
        
        # Assign port if this is a server
        port = None
        if proc.default_port:
            port = port_manager.assign_port(project, proc.name, preferred=proc.default_port)
            # Adjust command with assigned port
            cmd = process_manager._adjust_command_port(proc.command, port)
        else:
            cmd = proc.command
        
        # Register the process
        state = process_manager.register(
            project=project,
            name=proc.name,
            command=cmd,
            cwd=cwd,
            port=port,
            force_update=True,
        )
        registered.append({
            "id": state.id,
            "name": proc.name,
            "command": cmd,
            "port": port,
            "description": proc.description,
        })
    
    return {
        "success": True,
        "detected": registered,
        "method": "cache" if from_cache else ("llm" if use_llm else "heuristics"),
    }


@app.post("/projects/{project}/detect-stack")
async def detect_project_stack(project: str):
    """Auto-detect project stack, test framework, and directories.

    Returns detected info without saving — the UI lets the user review first.
    """
    from .process_discovery import detect_stack

    from .db.repositories import get_project_repo
    db_proj = get_project_repo().get(project)
    if not db_proj:
        raise HTTPException(status_code=404, detail=f"Project not found: {project}")

    result = detect_stack(db_proj.path)
    return result


# =============================================================================
# Browser Sessions (Browserless + CDP)
# =============================================================================

@app.websocket("/browser/cdp-proxy")
async def browser_cdp_proxy(ws: WebSocket):
    """Proxy WebSocket between the screencast viewer and the browserless CDP endpoint."""
    import websockets as ws_lib
    from .browser import get_browser_manager

    target = ws.query_params.get("target", "")
    if not target:
        await ws.close(code=1008, reason="Missing target parameter")
        return

    # Extract target_id from ws URL (e.g. "localhost:9500/devtools/page/ABC123")
    target_id = target.rsplit("/", 1)[-1] if "/devtools/page/" in target else ""

    cdp_url = f"ws://{target}"
    await ws.accept()

    try:
        bm = get_browser_manager()

        # Try connecting to the target; if stale, discover a fresh page target
        try:
            cdp_ws_ctx = ws_lib.connect(
                cdp_url, max_size=50 * 1024 * 1024,
                ping_interval=20, ping_timeout=10,
            )
            cdp_ws = await cdp_ws_ctx.__aenter__()
        except Exception:
            # Target is stale — extract port and discover current page targets
            port_match = target.split("/")[0]  # e.g. "localhost:9500"
            browser_url = f"ws://{port_match}"
            try:
                async with ws_lib.connect(
                    browser_url, max_size=5 * 1024 * 1024,
                    open_timeout=5, close_timeout=2,
                ) as browser_ws:
                    await browser_ws.send(json.dumps({"id": 1, "method": "Target.getTargets", "params": {}}))
                    resp = json.loads(await asyncio.wait_for(browser_ws.recv(), timeout=5))
                    targets = resp.get("result", {}).get("targetInfos", [])
                    page_targets = [t for t in targets if t.get("type") == "page"]
                    if not page_targets:
                        await ws.close(code=1000, reason="Browser session ended")
                        return
                    new_target_id = page_targets[0]["targetId"]
                    new_cdp_url = f"ws://{port_match}/devtools/page/{new_target_id}"
                    cdp_ws_ctx = ws_lib.connect(
                        new_cdp_url, max_size=50 * 1024 * 1024,
                        ping_interval=20, ping_timeout=10,
                    )
                    cdp_ws = await cdp_ws_ctx.__aenter__()
                    logger.info(f"CDP proxy recovered to target {new_target_id}")
            except Exception as e:
                logger.error(f"CDP proxy target recovery failed: {e}")
                await ws.close(code=1000, reason="Browser session ended")
                return

        try:
            async def forward_to_cdp():
                try:
                    while True:
                        data = await ws.receive_text()
                        if target_id:
                            try:
                                msg = json.loads(data)
                                method = msg.get("method", "")
                                if method == "Emulation.setDeviceMetricsOverride":
                                    await bm.set_device_override(target_id, msg.get("params", {}))
                                elif method == "Emulation.setUserAgentOverride":
                                    ua = msg.get("params", {}).get("userAgent", "")
                                    await bm.set_ua_override(target_id, ua)
                                elif method == "Emulation.clearDeviceMetricsOverride":
                                    await bm.clear_device_override(target_id)
                            except (json.JSONDecodeError, Exception):
                                pass
                        await cdp_ws.send(data)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass

            async def forward_from_cdp():
                try:
                    async for msg in cdp_ws:
                        await ws.send_text(msg if isinstance(msg, str) else msg.decode())
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass

            await asyncio.gather(forward_to_cdp(), forward_from_cdp())
        finally:
            try:
                await cdp_ws.close()
            except Exception:
                pass
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"CDP proxy error: {e}")
    finally:
        try:
            await ws.close()
        except Exception:
            pass


@app.get("/browser/resolve-target")
async def resolve_browser_target(port: int):
    """Resolve the current DevTools page target for a given container port.

    The viewer calls this before each reconnect attempt to get a fresh target URL
    (the target_id may change after browser recovery).
    """
    import websockets as ws_lib

    try:
        async with ws_lib.connect(
            f"ws://localhost:{port}", max_size=5 * 1024 * 1024,
            open_timeout=5, close_timeout=2,
        ) as browser_ws:
            await browser_ws.send(json.dumps({"id": 1, "method": "Target.getTargets", "params": {}}))
            resp = json.loads(await asyncio.wait_for(browser_ws.recv(), timeout=5))
            targets = resp.get("result", {}).get("targetInfos", [])
            page_targets = [t for t in targets if t.get("type") == "page"]
            if page_targets:
                tid = page_targets[0]["targetId"]
                return {"target": f"localhost:{port}/devtools/page/{tid}"}
            return {"target": None}
    except Exception as e:
        logger.warning(f"resolve-target failed for port {port}: {e}")
        return {"target": None}


@app.get("/browser/viewer")
async def browser_viewer(ws: str = ""):
    """Serve a standalone CDP screencast viewer page.

    The viewer connects to /browser/cdp-proxy?target=... and uses
    Page.startScreencast to render the remote browser on a canvas.
    Mouse/keyboard events are forwarded back through CDP.
    """
    return HTMLResponse(_BROWSER_VIEWER_HTML)


_BROWSER_VIEWER_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>Browser Viewer</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: 100%; height: 100%; overflow: hidden; background: #111; }
  canvas {
    display: block; width: 100%; height: 100%;
    object-fit: contain; cursor: default;
    touch-action: none;
  }
  #status {
    position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%);
    color: #888; font-family: system-ui, sans-serif; font-size: 14px;
    text-align: center; z-index: 10; pointer-events: none;
  }
  #status.hidden { display: none; }
  #stall-overlay {
    position: fixed; inset: 0; background: rgba(0,0,0,0.35);
    display: none; align-items: center; justify-content: center; z-index: 5;
    pointer-events: none;
  }
  #stall-overlay.visible { display: flex; }
  #stall-overlay .pill {
    background: rgba(0,0,0,0.7); color: #ccc; padding: 6px 16px;
    border-radius: 20px; font-family: system-ui, sans-serif; font-size: 13px;
  }
  @keyframes pulse-dot { 0%,80% { opacity: 0.3; } 40% { opacity: 1; } }
  #stall-overlay .dots span {
    animation: pulse-dot 1.4s infinite; margin-left: 2px;
  }
  #stall-overlay .dots span:nth-child(2) { animation-delay: 0.2s; }
  #stall-overlay .dots span:nth-child(3) { animation-delay: 0.4s; }
  /* Transparent typing overlay — invisible but focusable */
  #type-overlay {
    position: fixed; z-index: 15; opacity: 0;
    width: 1px; height: 1px; padding: 0; border: none;
    font-size: 16px; /* prevents iOS zoom on focus */
    caret-color: transparent;
    background: transparent; color: transparent;
    pointer-events: none;
  }
  #type-overlay.active { pointer-events: auto; width: 200px; height: 40px; }
</style>
</head>
<body>
<div id="status">Connecting...</div>
<div id="stall-overlay"><div class="pill">Updating<span class="dots"><span>.</span><span>.</span><span>.</span></span></div></div>
<canvas id="screen"></canvas>
<textarea id="type-overlay" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false"></textarea>
<script>
(function() {
  var canvas = document.getElementById('screen');
  var ctx = canvas.getContext('2d');
  var statusEl = document.getElementById('status');
  var stallOverlay = document.getElementById('stall-overlay');
  var typeOverlay = document.getElementById('type-overlay');

  var params = new URLSearchParams(location.search);
  var target = params.get('ws');
  if (!target) { statusEl.textContent = 'Missing ws parameter'; return; }

  var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  var ws, cmdId = 1, screenW = 0, screenH = 0, scaleFactor = 1;
  var retryCount = 0, MAX_RETRIES = 5, currentTarget = target;
  var lastFrameTime = Date.now(), watchdogTimer = null, hasReceivedFrame = false;
  var lastInteractionTime = 0; // track user/agent activity
  var isTouchDevice = 'ontouchstart' in window || navigator.maxTouchPoints > 0;

  var portMatch = target.match(/localhost:(\\d+)/);
  var containerPort = portMatch ? parseInt(portMatch[1]) : 0;

  function buildWsUrl(t) {
    return proto + '//' + location.host + '/browser/cdp-proxy?target=' + encodeURIComponent(t);
  }

  async function resolveTarget() {
    if (!containerPort) return null;
    try {
      var resp = await fetch('/browser/resolve-target?port=' + containerPort);
      var data = await resp.json();
      return data.target || null;
    } catch(e) { return null; }
  }

  async function connectWithRetry() {
    if (retryCount >= MAX_RETRIES) {
      statusEl.textContent = 'Session ended.';
      statusEl.classList.remove('hidden');
      return;
    }
    if (retryCount > 0) {
      statusEl.textContent = 'Reconnecting (' + retryCount + '/' + MAX_RETRIES + ')...';
      var freshTarget = await resolveTarget();
      if (freshTarget) { currentTarget = freshTarget; }
      else if (retryCount >= 2) {
        statusEl.textContent = 'Session ended.';
        statusEl.classList.remove('hidden');
        return;
      }
    }
    connect();
  }

  function connect() {
    ws = new WebSocket(buildWsUrl(currentTarget));
    ws.onopen = function() {
      retryCount = 0;
      lastFrameTime = Date.now();
      statusEl.textContent = 'Starting screencast...';

      // On touch devices, set mobile viewport so Chrome renders responsive content
      // Use actual device pixel dimensions for crisp screencast
      var dpr = window.devicePixelRatio || 1;
      var vw = window.innerWidth || 390;
      var vh = window.innerHeight || 844;
      var pxW = Math.round(vw * dpr);
      var pxH = Math.round(vh * dpr);

      // Match rendering to the viewing device for responsive fidelity.
      // Use the device's full physical pixel resolution for sharp screencast.
      if (isTouchDevice) {
        send('Emulation.setDeviceMetricsOverride', {
          width: vw, height: vh, deviceScaleFactor: dpr, mobile: true
        });
        send('Emulation.setUserAgentOverride', {
          userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
        });
      }

      // Request screencast at full physical pixel resolution
      send('Page.startScreencast', {
        format: 'jpeg', quality: 100, maxWidth: pxW, maxHeight: pxH,
        everyNthFrame: 1
      });

      // Watchdog: only restart screencast if frames stopped AFTER recent
      // user interaction (click, type, scroll). On a static page Chrome
      // naturally stops sending frames — that's fine, not a stall.
      if (watchdogTimer) clearInterval(watchdogTimer);
      watchdogTimer = setInterval(function() {
        var now = Date.now();
        var frameIdle = now - lastFrameTime;
        var sinceTap = now - lastInteractionTime;
        // Only intervene if: frames stopped for 3s AND there was a recent interaction (within 8s)
        if (ws && ws.readyState === 1 && frameIdle > 3000 && hasReceivedFrame && sinceTap < 8000) {
          stallOverlay.classList.add('visible');
          send('Page.startScreencast', {
            format: 'jpeg', quality: 100, maxWidth: pxW, maxHeight: pxH,
            everyNthFrame: 1
          });
        }
      }, 2000);
    };

    ws.onmessage = function(ev) {
      var msg;
      try { msg = JSON.parse(ev.data); } catch(e) { return; }
      if (msg.method === 'Page.screencastFrame') {
        var p = msg.params;
        lastFrameTime = Date.now();
        hasReceivedFrame = true;
        stallOverlay.classList.remove('visible');
        var img = new Image();
        img.onload = function() {
          if (canvas.width !== img.width || canvas.height !== img.height) {
            canvas.width = img.width;
            canvas.height = img.height;
          }
          ctx.drawImage(img, 0, 0);
          statusEl.classList.add('hidden');
          screenW = p.metadata.deviceWidth || img.width;
          screenH = p.metadata.deviceHeight || img.height;
          scaleFactor = p.metadata.deviceScaleFactor || 1;
        };
        img.src = 'data:image/jpeg;base64,' + p.data;
        send('Page.screencastFrameAck', { sessionId: p.sessionId });
      }
    };

    ws.onclose = function() {
      if (watchdogTimer) { clearInterval(watchdogTimer); watchdogTimer = null; }
      statusEl.classList.remove('hidden');
      retryCount++;
      setTimeout(connectWithRetry, 2000);
    };
    ws.onerror = function() {};
  }

  function send(method, params) {
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({ id: cmdId++, method: method, params: params || {} }));
    }
  }

  // ── Coordinate mapping ──
  function mapCoords(clientX, clientY) {
    // Map from viewer CSS coords to Chrome's CSS coords (not pixel buffer coords).
    // canvas.width = pixel buffer (e.g. 2560 for 1280@2x)
    // rect.width = CSS display size (e.g. 640px in sidebar)
    // screenW = Chrome's CSS viewport width (e.g. 1280)
    // We need: (click position in canvas) / (canvas CSS size) * (Chrome CSS viewport)
    var rect = canvas.getBoundingClientRect();
    var x = (clientX - rect.left) / rect.width * screenW;
    var y = (clientY - rect.top) / rect.height * screenH;
    return { x: Math.round(x), y: Math.round(y) };
  }

  function markInteraction() { lastInteractionTime = Date.now(); }

  // ── Desktop mouse events ──
  canvas.addEventListener('mousedown', function(e) {
    markInteraction();
    var c = mapCoords(e.clientX, e.clientY);
    send('Input.dispatchMouseEvent', { type: 'mousePressed', x: c.x, y: c.y, button: 'left', clickCount: 1 });
  });
  canvas.addEventListener('mouseup', function(e) {
    var c = mapCoords(e.clientX, e.clientY);
    send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: c.x, y: c.y, button: 'left', clickCount: 1 });
  });
  canvas.addEventListener('mousemove', function(e) {
    var c = mapCoords(e.clientX, e.clientY);
    send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: c.x, y: c.y });
  });

  // ── Touch events: tap = click, drag = scroll ──
  var touchStartPos = null, touchStartTime = 0, touchScrolling = false;
  var TAP_THRESHOLD = 10, TAP_TIME = 300;

  canvas.addEventListener('touchstart', function(e) {
    e.preventDefault();
    markInteraction();
    var t = e.touches[0];
    touchStartPos = { x: t.clientX, y: t.clientY };
    touchStartTime = Date.now();
    touchScrolling = false;
  }, { passive: false });

  canvas.addEventListener('touchmove', function(e) {
    e.preventDefault();
    if (!touchStartPos || e.touches.length < 1) return;
    var t = e.touches[0];
    var dx = t.clientX - touchStartPos.x;
    var dy = t.clientY - touchStartPos.y;
    if (!touchScrolling && Math.abs(dy) > TAP_THRESHOLD) {
      touchScrolling = true;
      // Dismiss keyboard when scrolling starts
      if (typeOverlay.classList.contains('active')) typeOverlay.blur();
    }
    if (touchScrolling) {
      var c = mapCoords(t.clientX, t.clientY);
      // Invert: dragging up = scroll down
      send('Input.dispatchMouseEvent', {
        type: 'mouseWheel', x: c.x, y: c.y, deltaX: 0, deltaY: -dy * 2
      });
      touchStartPos = { x: t.clientX, y: t.clientY };
    }
  }, { passive: false });

  canvas.addEventListener('touchend', function(e) {
    e.preventDefault();
    if (!touchStartPos) return;
    var elapsed = Date.now() - touchStartTime;
    if (!touchScrolling && elapsed < TAP_TIME) {
      // It was a tap — send click to the remote page
      var c = mapCoords(touchStartPos.x, touchStartPos.y);
      send('Input.dispatchMouseEvent', { type: 'mousePressed', x: c.x, y: c.y, button: 'left', clickCount: 1 });
      send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: c.x, y: c.y, button: 'left', clickCount: 1 });
      // Toggle keyboard: if overlay is already focused, dismiss it.
      // Otherwise show it at tap location for typing.
      if (typeOverlay.classList.contains('active')) {
        typeOverlay.blur();
      } else {
        typeOverlay.style.left = touchStartPos.x + 'px';
        typeOverlay.style.top = touchStartPos.y + 'px';
        typeOverlay.classList.add('active');
        typeOverlay.value = '';
        typeOverlay.focus();
      }
    }
    touchStartPos = null;
  }, { passive: false });

  // ── Typing overlay: forward input to CDP ──
  var VK = {
    Backspace: 8, Tab: 9, Enter: 13, Escape: 27, Delete: 46,
    ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40,
    Home: 36, End: 35, PageUp: 33, PageDown: 34,
  };

  typeOverlay.addEventListener('input', function(e) {
    markInteraction();
    var data = e.data;
    if (data) {
      // Use Input.insertText — the most reliable way to type any character
      // via CDP. Works for all chars including dots, symbols, emoji, etc.
      send('Input.insertText', { text: data });
    }
    typeOverlay.value = '';
  });

  typeOverlay.addEventListener('keydown', function(e) {
    var vk = VK[e.key];
    if (vk) {
      e.preventDefault();
      send('Input.dispatchKeyEvent', {
        type: 'keyDown', key: e.key, code: e.code,
        windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk
      });
      send('Input.dispatchKeyEvent', {
        type: 'keyUp', key: e.key, code: e.code,
        windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk
      });
    }
  });

  typeOverlay.addEventListener('blur', function() {
    typeOverlay.classList.remove('active');
  });

  // ── Desktop keyboard events (physical keyboard) ──
  document.addEventListener('keydown', function(e) {
    if (e.target === typeOverlay) return; // handled above
    e.preventDefault();
    markInteraction();
    var modifiers = (e.altKey ? 1 : 0) | (e.ctrlKey ? 2 : 0) | (e.metaKey ? 4 : 0) | (e.shiftKey ? 8 : 0);
    var p = { type: 'keyDown', key: e.key, code: e.code, modifiers: modifiers };
    if (e.key.length === 1) {
      p.type = 'rawKeyDown';
      p.windowsVirtualKeyCode = e.keyCode;
      p.nativeVirtualKeyCode = e.keyCode;
      send('Input.dispatchKeyEvent', p);
      send('Input.dispatchKeyEvent', { type: 'char', text: e.key, key: e.key, code: e.code, modifiers: modifiers });
    } else {
      var vk = VK[e.key] || e.keyCode || 0;
      if (vk) { p.windowsVirtualKeyCode = vk; p.nativeVirtualKeyCode = vk; }
      send('Input.dispatchKeyEvent', p);
    }
  });
  document.addEventListener('keyup', function(e) {
    if (e.target === typeOverlay) return;
    e.preventDefault();
    var vk = VK[e.key] || e.keyCode || 0;
    var p = {
      type: 'keyUp', key: e.key, code: e.code,
      modifiers: (e.altKey ? 1 : 0) | (e.ctrlKey ? 2 : 0) | (e.metaKey ? 4 : 0) | (e.shiftKey ? 8 : 0)
    };
    if (vk) { p.windowsVirtualKeyCode = vk; p.nativeVirtualKeyCode = vk; }
    send('Input.dispatchKeyEvent', p);
  });

  // Desktop scroll
  canvas.addEventListener('wheel', function(e) {
    e.preventDefault();
    markInteraction();
    var c = mapCoords(e.clientX, e.clientY);
    send('Input.dispatchMouseEvent', { type: 'mouseWheel', x: c.x, y: c.y, deltaX: e.deltaX, deltaY: e.deltaY });
  }, { passive: false });

  connectWithRetry();
})();
</script>
</body>
</html>
"""


@app.post("/browser/start/{process_id}")
async def start_browser_session(process_id: str, target_url: str | None = None):
    """Start a browser session for a process."""
    from .browser import get_browser_manager
    from .processes import get_process_manager

    bm = get_browser_manager()

    if not target_url:
        pm = get_process_manager()
        state = pm.get(process_id)
        if state and state.port:
            target_url = f"http://localhost:{state.port}"
        else:
            raise HTTPException(status_code=400, detail="No target_url and process has no port")

    session = await bm.create_session(process_id, target_url)

    if session.status.value == "failed":
        raise HTTPException(status_code=500, detail=session.error or "Failed to start browser")

    event_repo.log("browser.session_created", message=f"Browser session for {process_id}")

    # Check PinchTab attachment status
    from .pinchtab import check_health as _pt_health
    pinchtab_attached = _pt_health()

    return {
        "id": session.id,
        "process_id": session.process_id,
        "target_url": session.target_url,
        "container_port": session.container_port,
        "status": session.status.value,
        "viewer_url": bm.get_viewer_url(session.id),
        "pinchtab_attached": pinchtab_attached,
    }


@app.get("/browser/sessions")
async def list_browser_sessions():
    """List all browser sessions."""
    from .browser import get_browser_manager
    bm = get_browser_manager()
    await bm.ensure_connections()
    sessions = bm.list_sessions()
    return [
        {
            "id": s.id, "process_id": s.process_id,
            "project_id": s.project_id,
            "target_url": s.target_url,
            "container_port": s.container_port,
            "status": s.status.value,
            "viewer_url": bm.get_viewer_url(s.id),
            "error": s.error,
        }
        for s in sessions
    ]


@app.get("/browser/sessions/{session_id}")
async def get_browser_session(session_id: str):
    """Get a browser session."""
    from .browser import get_browser_manager
    bm = get_browser_manager()
    session = bm.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "id": session.id, "process_id": session.process_id,
        "target_url": session.target_url,
        "container_port": session.container_port,
        "status": session.status.value,
        "viewer_url": bm.get_viewer_url(session.id),
        "error": session.error,
    }


@app.post("/browser/sessions/{session_id}/stop")
async def stop_browser_session(session_id: str):
    """Stop a browser session."""
    from .browser import get_browser_manager
    bm = get_browser_manager()
    success = await bm.stop_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    event_repo.log("browser.session_stopped", message=f"Stopped browser session {session_id}")
    return {"success": True}


@app.post("/browser/start")
async def start_standalone_browser(target_url: str, project: str | None = None):
    """Start a standalone browser session (not tied to a process)."""
    from .browser import get_browser_manager

    bm = get_browser_manager()
    project_id = resolve_project_id(project) if project else ""

    session = await bm.create_standalone_session(target_url, project_id=project_id)

    if session.status.value == "failed":
        raise HTTPException(status_code=500, detail=session.error or "Failed to start browser")

    event_repo.log("browser.session_created", message=f"Standalone browser session -> {target_url}")

    return {
        "id": session.id,
        "process_id": session.process_id,
        "target_url": session.target_url,
        "container_port": session.container_port,
        "status": session.status.value,
        "viewer_url": bm.get_viewer_url(session.id),
    }


@app.post("/browser/sessions/{session_id}/navigate")
async def navigate_browser_session(session_id: str, url: str):
    """Navigate an existing browser session to a new URL."""
    from .browser import get_browser_manager

    bm = get_browser_manager()
    success = await bm.navigate_session(session_id, url)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found or not running")

    event_repo.log("browser.navigated", message=f"Navigated {session_id} -> {url}")
    return {"success": True, "url": url}


@app.post("/browser/sessions/{session_id}/reload")
async def reload_browser_session(session_id: str):
    """Reload the current page in a browser session."""
    from .browser import get_browser_manager

    bm = get_browser_manager()
    success = await bm.reload_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found or not running")
    return {"success": True}


@app.post("/browser/sessions/{session_id}/back")
async def go_back_browser_session(session_id: str):
    """Go back in browser history for a session."""
    from .browser import get_browser_manager

    bm = get_browser_manager()
    success = await bm.go_back_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found or not running")
    return {"success": True}


@app.post("/browser/sessions/{session_id}/record/start")
async def start_recording(session_id: str):
    """Start rrweb recording for a browser session."""
    from .browser import get_browser_manager

    bm = get_browser_manager()
    session = bm.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    recording = await bm.start_recording(session_id, project_id=session.project_id or "")
    if not recording:
        raise HTTPException(status_code=500, detail="Failed to start recording")

    event_repo.log("recording.started", message=f"Recording started for {session_id}")
    return {
        "id": recording.id,
        "session_id": recording.session_id,
        "status": recording.status.value,
        "started_at": recording.started_at.isoformat(),
    }


@app.post("/browser/sessions/{session_id}/record/stop")
async def stop_recording(session_id: str):
    """Stop rrweb recording for a browser session."""
    from .browser import get_browser_manager

    bm = get_browser_manager()
    recording = await bm.stop_recording(session_id)
    if not recording:
        raise HTTPException(status_code=404, detail="No active recording found")

    event_repo.log("recording.stopped", message=f"Recording stopped: {recording.id}")
    return {
        "id": recording.id,
        "status": recording.status.value,
        "event_count": recording.event_count,
        "chunk_count": recording.chunk_count,
    }


@app.get("/recordings")
async def list_recordings(session_id: str | None = None):
    """List recordings."""
    from .recording import get_recording_manager

    rm = get_recording_manager()
    recordings = rm.list_recordings(session_id=session_id or "")
    return [
        {
            "id": r.id,
            "session_id": r.session_id,
            "project_id": r.project_id,
            "status": r.status.value,
            "started_at": r.started_at.isoformat(),
            "stopped_at": r.stopped_at.isoformat() if r.stopped_at else None,
            "event_count": r.event_count,
            "chunk_count": r.chunk_count,
        }
        for r in recordings
    ]


@app.get("/recordings/{recording_id}")
async def get_recording(recording_id: str):
    """Get recording metadata."""
    from .recording import get_recording_manager

    rm = get_recording_manager()
    recording = rm.get_recording(recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    return {
        "id": recording.id,
        "session_id": recording.session_id,
        "project_id": recording.project_id,
        "status": recording.status.value,
        "started_at": recording.started_at.isoformat(),
        "stopped_at": recording.stopped_at.isoformat() if recording.stopped_at else None,
        "event_count": recording.event_count,
        "chunk_count": recording.chunk_count,
    }


@app.get("/recordings/{recording_id}/events")
async def get_recording_events(recording_id: str, chunk: int = 0):
    """Get events for a recording chunk."""
    from .recording import get_recording_manager

    rm = get_recording_manager()
    recording = rm.get_recording(recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")

    events = rm.get_events(recording_id, chunk)
    return {
        "recording_id": recording_id,
        "chunk": chunk,
        "total_chunks": recording.chunk_count,
        "events": events,
    }


@app.delete("/recordings/{recording_id}")
async def delete_recording(recording_id: str):
    """Delete a recording and its chunk files."""
    from .recording import get_recording_manager
    import shutil

    rm = get_recording_manager()
    recording = rm.get_recording(recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")

    # Stop if still active
    if recording.status.value == "recording":
        rm.stop_recording(recording_id)

    # Delete chunk files
    rec_dir = rm.RECORDINGS_DIR / recording_id if hasattr(rm, 'RECORDINGS_DIR') else None
    from .config import get_rdc_home
    rec_dir = get_rdc_home() / "recordings" / recording_id
    if rec_dir.exists():
        shutil.rmtree(rec_dir)

    # Delete DB record
    from .db.connection import get_db
    db = get_db("rdc")
    db.execute("DELETE FROM recordings WHERE id = ?", (recording_id,))
    db.commit()

    return {"ok": True}


# =============================================================================
# Context Capture
# =============================================================================

@app.post("/context/capture")
async def capture_context(
    session_id: str | None = None,
    process_id: str | None = None,
    project: str | None = None,
    description: str = "",
):
    """Capture browser context (screenshot + accessibility tree)."""
    from .browser import get_browser_manager
    bm = get_browser_manager()

    if not session_id and process_id:
        s = bm.get_by_process(process_id)
        if s:
            session_id = s.id

    if not session_id:
        sessions = [s for s in bm.list_sessions() if s.status.value == "running"]
        if sessions:
            session_id = sessions[0].id

    if not session_id:
        raise HTTPException(status_code=404, detail="No active browser session. Start a Preview first.")

    if not project and process_id and "-" in process_id:
        project = process_id.rsplit("-", 1)[0]

    proj_id = resolve_project_id(project) if project else None
    snapshot = await bm.capture_context(
        session_id, project_id=proj_id, description=description,
    )
    if not snapshot:
        raise HTTPException(
            status_code=500,
            detail="Failed to capture context. The browser connection may have dropped — try again.",
        )

    return {
        "id": snapshot.id,
        "project": snapshot.project,
        "url": snapshot.url,
        "title": snapshot.title,
        "timestamp": snapshot.timestamp.isoformat(),
        "screenshot_path": snapshot.screenshot_path,
        "a11y_path": snapshot.a11y_path,
        "meta_path": snapshot.meta_path,
        "description": snapshot.description,
    }




@app.get("/context")
async def list_contexts(project: str | None = None, limit: int = 50):
    """List captured contexts."""
    from .browser import get_browser_manager
    bm = get_browser_manager()
    proj_id = ""
    if project:
        resolved = resolve_project_id(project)
        proj_id = resolved if resolved else ""
    contexts = bm.list_contexts(project_id=proj_id, limit=limit)
    return [
        {
            "id": c.id, "project": c.project, "url": c.url, "title": c.title,
            "timestamp": c.timestamp.isoformat(), "description": c.description,
            "screenshot_path": c.screenshot_path,
        }
        for c in contexts
    ]


@app.get("/context/{context_id}")
async def get_context(context_id: str):
    """Get context metadata and accessibility tree."""
    from .browser import get_browser_manager
    bm = get_browser_manager()
    ctx = bm.get_context(context_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="Context not found")

    a11y = None
    if ctx.a11y_path and Path(ctx.a11y_path).exists():
        a11y = json.loads(Path(ctx.a11y_path).read_text())

    meta = None
    if ctx.meta_path and Path(ctx.meta_path).exists():
        meta = json.loads(Path(ctx.meta_path).read_text())

    return {
        "id": ctx.id, "project": ctx.project, "url": ctx.url, "title": ctx.title,
        "timestamp": ctx.timestamp.isoformat(), "description": ctx.description,
        "a11y": a11y, "meta": meta,
        "screenshot_path": ctx.screenshot_path,
    }


@app.get("/context/{context_id}/screenshot")
async def get_context_screenshot(context_id: str):
    """Get context screenshot image."""
    from fastapi.responses import FileResponse
    from .browser import get_browser_manager
    bm = get_browser_manager()
    ctx = bm.get_context(context_id)
    if not ctx or not ctx.screenshot_path:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    filepath = Path(ctx.screenshot_path)
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Screenshot file missing")
    return FileResponse(filepath, media_type="image/png")


@app.delete("/context/{context_id}")
async def delete_context(context_id: str):
    """Delete a context snapshot."""
    from .browser import get_browser_manager
    bm = get_browser_manager()
    if not bm.delete_context(context_id):
        raise HTTPException(status_code=404, detail="Context not found")
    return {"success": True}


@app.post("/context/upload")
async def upload_context(
    file: UploadFile = File(...),
    project: str = Form("upload"),
    description: str = Form(""),
):
    """Upload a file (image or any other type) as a context snapshot."""
    from .browser import CONTEXTS_DIR, get_browser_manager
    from .db.models import ContextSnapshot
    from .db.repositories import resolve_project_id

    bm = get_browser_manager()
    ctx_id = secrets.token_hex(4)
    ext = Path(file.filename or "upload.bin").suffix or ".bin"
    file_path = CONTEXTS_DIR / f"{ctx_id}{ext}"
    file_path.write_bytes(await file.read())

    project_id = resolve_project_id(project) or ""

    snapshot = ContextSnapshot(
        id=ctx_id, project=project, project_id=project_id,
        screenshot_path=str(file_path),
        description=description or file.filename or "Uploaded file",
    )
    bm._save_context(snapshot)

    return {
        "id": snapshot.id, "project": snapshot.project,
        "timestamp": snapshot.timestamp.isoformat(),
        "path": snapshot.screenshot_path,
        "description": snapshot.description,
    }


# =============================================================================
# Visual Streaming (VNC) — Legacy, being replaced by Browser Sessions
# =============================================================================

@app.get("/vnc/sessions")
async def list_vnc_sessions():
    """List all VNC sessions."""
    from .vnc import get_vnc_manager
    
    vnc_manager = get_vnc_manager()
    sessions = vnc_manager.list_sessions()
    
    return [
        {
            "id": s.id,
            "process_id": s.process_id,
            "target_url": s.target_url,
            "vnc_port": s.vnc_port,
            "web_port": s.web_port,
            "container_id": s.container_id,
            "status": s.status.value,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "error": s.error,
        }
        for s in sessions
    ]


@app.get("/vnc/sessions/{session_id}")
async def get_vnc_session(session_id: str):
    """Get a specific VNC session."""
    from .vnc import get_vnc_manager
    
    vnc_manager = get_vnc_manager()
    session = vnc_manager.get_session(session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    
    return {
        "id": session.id,
        "process_id": session.process_id,
        "target_url": session.target_url,
        "vnc_port": session.vnc_port,
        "web_port": session.web_port,
        "viewer_url": f"http://localhost:{session.vnc_port}",
        "container_id": session.container_id,
        "status": session.status.value,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "error": session.error,
    }


@app.post("/vnc/sessions")
async def create_vnc_session(
    process_id: str,
    target_url: str | None = None,
    vnc_port: int | None = None,
):
    """Create a new VNC session for a process.
    
    Query params:
    - process_id: ID of the process to visualize
    - target_url: URL to open (optional, will auto-detect from process port)
    - vnc_port: Preferred VNC viewer port (optional)
    """
    from .vnc import get_vnc_manager
    
    vnc_manager = get_vnc_manager()
    
    # Auto-detect target URL from process if not provided
    if not target_url:
        state = process_manager.get(process_id)
        if not state:
            raise HTTPException(status_code=404, detail=f"Process not found: {process_id}")
        
        if not state.port:
            raise HTTPException(
                status_code=400,
                detail=f"Process {process_id} has no port configured. Provide target_url manually."
            )
        
        target_url = f"http://localhost:{state.port}"
    
    try:
        session = vnc_manager.create_session(
            process_id=process_id,
            target_url=target_url,
            preferred_vnc_port=vnc_port,
        )
        
        event_repo.log(
            "vnc.session_created",
            message=f"Created VNC session for {process_id}",
        )
        
        return {
            "success": True,
            "session": {
                "id": session.id,
                "process_id": session.process_id,
                "target_url": session.target_url,
                "vnc_port": session.vnc_port,
                "viewer_url": f"http://localhost:{session.vnc_port}",
                "status": session.status.value,
                "error": session.error,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/vnc/start/{process_id}")
async def start_vnc_for_process(process_id: str, target_url: str | None = None):
    """Convenience endpoint to start VNC session for a process."""
    from .vnc import get_vnc_manager
    
    vnc_manager = get_vnc_manager()
    
    if not target_url:
        state = process_manager.get(process_id)
        if not state:
            raise HTTPException(status_code=404, detail=f"Process not found: {process_id}")
        
        if not state.port:
            raise HTTPException(
                status_code=400,
                detail=f"Process {process_id} has no port configured."
            )
        
        target_url = f"http://localhost:{state.port}"
    
    try:
        session = vnc_manager.create_session(
            process_id=process_id,
            target_url=target_url,
        )
        
        event_repo.log("vnc.session_created", message=f"Created VNC session for {process_id}")
        
        return {
            "id": session.id,
            "process_id": session.process_id,
            "vnc_port": session.vnc_port,
            "viewer_url": f"http://localhost:{session.vnc_port}",
            "status": session.status.value,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/vnc/sessions/{session_id}/stop")
async def stop_vnc_session(session_id: str):
    """Stop a VNC session."""
    from .vnc import get_vnc_manager
    
    vnc_manager = get_vnc_manager()
    success = vnc_manager.stop_session(session_id)
    
    if not success:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    
    event_repo.log(
        "vnc.session_stopped",
        message=f"Stopped VNC session {session_id}",
    )
    
    return {"success": True}


@app.post("/vnc/sessions/{session_id}/restart")
async def restart_vnc_session(session_id: str):
    """Restart a VNC session."""
    from .vnc import get_vnc_manager
    
    vnc_manager = get_vnc_manager()
    
    try:
        session = vnc_manager.restart_session(session_id)
        return {
            "success": True,
            "session": {
                "id": session.id,
                "status": session.status.value,
                "viewer_url": f"http://localhost:{session.vnc_port}",
            },
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/vnc/sessions/{session_id}")
async def delete_vnc_session(session_id: str):
    """Delete a VNC session."""
    from .vnc import get_vnc_manager
    
    vnc_manager = get_vnc_manager()
    success = vnc_manager.delete_session(session_id)
    
    if not success:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    
    return {"success": True}


@app.get("/vnc/sessions/{session_id}/screenshot")
async def vnc_screenshot(session_id: str, full_page: bool = False):
    """Capture a screenshot from a VNC session.
    
    Args:
        session_id: VNC session ID.
        full_page: If true, capture the full scrollable page. Default is viewport only.
    """
    from .vnc import get_vnc_manager
    from fastapi.responses import Response
    
    vnc_mgr = get_vnc_manager()
    screenshot = vnc_mgr.capture_screenshot(session_id, full_page=full_page)
    
    if not screenshot:
        raise HTTPException(status_code=404, detail="Could not capture screenshot")
    
    return Response(content=screenshot, media_type="image/png")


# Voice Agent endpoints
_voice_manager = None

@app.post("/voice/start")
async def start_voice_server():
    """Start the LiveKit server and voice agent."""
    global _voice_manager
    
    try:
        from .voice_agent import VoiceAgentManager
        from .vnc import get_vnc_manager
        
        if _voice_manager is None:
            _voice_manager = VoiceAgentManager(vnc_manager=get_vnc_manager())
        
        success = await _voice_manager.start_livekit_server()
        if not success:
            raise HTTPException(status_code=500, detail="Failed to start LiveKit server")
        
        return {"success": True, "message": "LiveKit server started"}
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"LiveKit not installed: {e}")


@app.get("/voice/connection")
async def get_voice_connection(room: str = "rdc-voice"):
    """Get connection info for joining the voice room."""
    global _voice_manager
    
    try:
        from .voice_agent import VoiceAgentManager
        from .vnc import get_vnc_manager
        
        if _voice_manager is None:
            _voice_manager = VoiceAgentManager(vnc_manager=get_vnc_manager())
        
        return _voice_manager.get_connection_info(room)
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"LiveKit not installed: {e}")


@app.post("/voice/stop")
async def stop_voice_server():
    """Stop the LiveKit server."""
    global _voice_manager
    
    if _voice_manager:
        _voice_manager.stop_livekit_server()
    
    return {"success": True}


# Phone calling endpoints (Twilio)

def _try_init_phone_channel():
    """Try to lazily initialize the phone channel from secrets + DB settings."""
    from .channels.phone import get_phone_channel, set_phone_channel, PhoneChannel

    phone = get_phone_channel()
    if phone is not None:
        return phone

    try:
        from .tts import get_tts_service
        tts = get_tts_service()
        settings = tts.get_all_settings()

        account_sid = get_secret("TWILIO_ACCOUNT_SID")
        auth_token = get_secret("TWILIO_AUTH_TOKEN")
        twilio_number = get_secret("TWILIO_PHONE_NUMBER")
        user_phone = settings.get("phone_user_number")
        webhook_url = settings.get("phone_webhook_url")

        if not all([account_sid, auth_token, twilio_number, user_phone, webhook_url]):
            return None

        phone = PhoneChannel(
            account_sid=account_sid,
            auth_token=auth_token,
            twilio_number=twilio_number,
            user_phone=user_phone,
            webhook_base_url=webhook_url,
        )
        phone.start()
        set_phone_channel(phone)
        return phone
    except Exception:
        return None


def _is_phone_configured() -> bool:
    """Check if Twilio phone calling has all required credentials."""
    from .channels.phone import get_phone_channel
    if get_phone_channel() is not None:
        return True
    try:
        from .tts import get_tts_service
        tts = get_tts_service()
        settings = tts.get_all_settings()
        return bool(
            get_secret("TWILIO_ACCOUNT_SID")
            and get_secret("TWILIO_AUTH_TOKEN")
            and get_secret("TWILIO_PHONE_NUMBER")
            and settings.get("phone_user_number")
            and settings.get("phone_webhook_url")
        )
    except Exception:
        return False


@app.post("/voice/call")
async def initiate_phone_call(request: Request):
    """Initiate an outbound phone call to the user."""
    phone = _try_init_phone_channel()
    if not phone:
        raise HTTPException(status_code=400, detail="Phone calling not configured. Set up Twilio credentials in Admin Settings.")

    data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    project = data.get("project")
    client_id = data.get("client_id")

    # If no explicit client_id, try to auto-pair with a connected client.
    # Prefer the caller's own client_id if provided, otherwise pick the
    # first connected client (desktop preferred over mobile).
    if not client_id:
        try:
            from .state_machine import get_state_machine
            clients = get_state_machine().get_connected_clients()
            desktop_clients = [c for c in clients if c["client_id"].startswith("desktop-")]
            mobile_clients = [c for c in clients if c["client_id"].startswith("mobile-")]
            if desktop_clients:
                client_id = desktop_clients[0]["client_id"]
            elif mobile_clients:
                client_id = mobile_clients[0]["client_id"]
        except Exception:
            pass

    result = await phone.initiate_call(project=project, client_id=client_id)
    return result


@app.post("/voice/hangup")
async def hangup_phone_call(request: Request):
    """Hang up the active phone call."""
    from .channels.phone import get_phone_channel
    phone = get_phone_channel()
    if not phone:
        raise HTTPException(status_code=400, detail="Phone calling not configured")

    call = phone.get_active_call()
    if not call:
        return {"status": "no_active_call"}

    data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    call_sid = data.get("call_sid") or call.call_sid
    result = await phone.hangup(call_sid)
    return result


@app.get("/voice/call/status")
async def get_phone_call_status():
    """Get current phone call status."""
    from .channels.phone import get_phone_channel
    phone = get_phone_channel()
    if phone:
        info = phone.get_call_info()
        info["configured"] = True
        return info
    return {"active": False, "configured": _is_phone_configured()}


@app.post("/voice/pair")
async def pair_phone_with_client(request: Request):
    """Pair the active phone call with a dashboard client."""
    from .channels.phone import get_phone_channel

    phone = get_phone_channel()
    if not phone:
        raise HTTPException(status_code=400, detail="Phone calling not configured")

    call = phone.get_active_call()
    if not call:
        raise HTTPException(status_code=400, detail="No active call")

    data = await request.json()
    client_id = data.get("client_id")
    if not client_id:
        raise HTTPException(status_code=400, detail="client_id required")

    machine = get_state_machine()
    # Verify client is connected
    connected = {c["client_id"] for c in machine.get_connected_clients()}
    if client_id not in connected:
        raise HTTPException(status_code=400, detail=f"Client {client_id} not connected")

    phone.pair(call.call_sid, client_id)

    # Notify the paired client
    await machine.send_to_client(client_id, {
        "type": "phone_paired",
        "call_sid": call.call_sid,
        "client_id": client_id,
    })

    return {"paired": True, "call_sid": call.call_sid, "client_id": client_id}


@app.post("/voice/unpair")
async def unpair_phone(request: Request):
    """Remove pairing from the active phone call."""
    from .channels.phone import get_phone_channel

    phone = get_phone_channel()
    if not phone:
        raise HTTPException(status_code=400, detail="Phone calling not configured")

    call = phone.get_active_call()
    if not call:
        raise HTTPException(status_code=400, detail="No active call")

    old_client = call.paired_client_id
    phone.unpair(call.call_sid)

    # Notify the previously paired client
    if old_client:
        machine = get_state_machine()
        await machine.send_to_client(old_client, {
            "type": "phone_unpaired",
            "call_sid": call.call_sid,
        })

    return {"unpaired": True, "call_sid": call.call_sid}


@app.get("/voice/clients")
async def get_voice_clients():
    """List connected dashboard clients available for pairing."""
    machine = get_state_machine()
    return {"clients": machine.get_connected_clients()}


@app.post("/voice/type-mode")
async def set_type_mode(request: Request):
    """Toggle type mode for the active phone call."""
    from .channels.phone import get_phone_channel

    data = await request.json()
    enabled = data.get("enabled", False)
    target = data.get("target", "terminal")

    phone = get_phone_channel()
    if not phone:
        raise HTTPException(status_code=400, detail="Phone calling not configured")

    call = phone.get_active_call()
    if not call:
        raise HTTPException(status_code=400, detail="No active call")

    call.type_mode = enabled
    call.type_mode_target = target if enabled else None

    if call.paired_client_id:
        machine = get_state_machine()
        await machine.send_to_client(call.paired_client_id, {
            "type": "phone_type_mode",
            "enabled": enabled,
            "target": target,
        })

    return {"type_mode": enabled, "target": target}


# Twilio webhooks (public, validated by signature)

@app.post("/voice/twilio/incoming")
async def twilio_incoming(request: Request):
    """Twilio webhook: call connected (inbound or outbound), return greeting TwiML."""
    from fastapi.responses import Response

    phone = _try_init_phone_channel()
    if not phone:
        raise HTTPException(status_code=500, detail="Phone not configured")

    form = await request.form()
    params = dict(form)
    call_sid = params.get("CallSid", "")

    # Validate Twilio signature using canonical public URL (not local proxy URL)
    signature = request.headers.get("X-Twilio-Signature", "")
    canonical_url = f"{phone.webhook_base_url}/voice/twilio/incoming"
    if not phone.validate_request(canonical_url, params, signature):
        logger.warning("Invalid Twilio signature for incoming webhook (url=%s)", canonical_url)
        # Don't block — signature may mismatch due to HTTP/HTTPS or proxy differences
        # raise HTTPException(status_code=403, detail="Invalid signature")

    twiml = await phone.handle_incoming(call_sid)
    return Response(content=twiml, media_type="application/xml")


@app.post("/voice/twilio/gather")
async def twilio_gather(request: Request):
    """Twilio webhook: speech received, process and return response TwiML."""
    from fastapi.responses import Response

    phone = _try_init_phone_channel()
    if not phone:
        raise HTTPException(status_code=500, detail="Phone not configured")

    form = await request.form()
    params = dict(form)
    call_sid = params.get("CallSid", "")
    speech_result = params.get("SpeechResult", "")

    # Validate Twilio signature using canonical public URL
    signature = request.headers.get("X-Twilio-Signature", "")
    canonical_url = f"{phone.webhook_base_url}/voice/twilio/gather"
    if not phone.validate_request(canonical_url, params, signature):
        logger.warning("Invalid Twilio signature for gather webhook (url=%s)", canonical_url)
        # Don't block — signature may mismatch due to HTTP/HTTPS or proxy differences

    if not speech_result:
        # No speech detected, re-prompt
        from twilio.twiml.voice_response import VoiceResponse, Gather
        resp = VoiceResponse()
        resp.say("I didn't catch that. Please try again.", voice="Polly.Joanna")
        gather = Gather(
            input="speech",
            action=f"{phone.webhook_base_url}/voice/twilio/gather",
            method="POST",
            speech_timeout="auto",
            language="en-US",
        )
        resp.append(gather)
        return Response(content=str(resp), media_type="application/xml")

    twiml = await phone.handle_gather(call_sid, speech_result)
    return Response(content=twiml, media_type="application/xml")


@app.post("/voice/twilio/result/{call_sid}")
async def twilio_result(call_sid: str, request: Request):
    """Twilio webhook: poll for async LLM result."""
    from fastapi.responses import Response

    phone = _try_init_phone_channel()
    if not phone:
        raise HTTPException(status_code=500, detail="Phone not configured")

    twiml = phone.get_pending_result(call_sid)
    return Response(content=twiml, media_type="application/xml")


@app.post("/voice/twilio/status")
async def twilio_status(request: Request):
    """Twilio webhook: call status updates."""
    from .channels.phone import get_phone_channel

    phone = get_phone_channel()
    if not phone:
        return {"ok": True}

    form = await request.form()
    params = dict(form)
    call_sid = params.get("CallSid", "")
    call_status = params.get("CallStatus", "")

    await phone.handle_status(call_sid, call_status)

    # Broadcast state so all clients see the call ended
    if call_status in ("completed", "failed", "busy", "no-answer", "canceled"):
        machine = get_state_machine()
        await machine._broadcast_state()

    return {"ok": True}


@app.get("/voice/twilio/audio/{filename}")
async def serve_twilio_audio(filename: str):
    """Serve TTS audio files for Twilio to play."""
    from .channels.phone import get_phone_channel
    from fastapi.responses import FileResponse

    phone = get_phone_channel()
    if not phone:
        raise HTTPException(status_code=404, detail="Not found")

    filepath = phone.get_audio_file(filename)
    if not filepath:
        raise HTTPException(status_code=404, detail="Audio file not found")

    media_type = "audio/wav" if filepath.suffix == ".wav" else "audio/mpeg"
    return FileResponse(filepath, media_type=media_type)


@app.post("/orchestrator")
async def orchestrator_endpoint(
    request: Request,
    message: str | None = None,
    project: str | None = None,
    session_id: str | None = None,
    channel: str = "desktop",  # "desktop", "mobile", "voice"
):
    """Unified intent processing for all user input (voice, mobile, desktop)."""
    from .intent import (
        get_intent_engine, get_action_executor, build_orchestrator_context,
        load_nanobot_config, log_nanobot_interaction, extract_knowledge,
    )
    from .conversation import get_conversation_manager

    # Accept params from query string OR JSON body
    conversation_history: list[dict] | None = None
    client_id: str | None = None
    channel_id: str | None = None
    try:
        body = await request.json()
        if isinstance(body, dict):
            message = message or body.get("message")
            project = project or body.get("project")
            channel = body.get("channel", channel)
            client_id = body.get("client_id")
            channel_id = body.get("channel_id")
            conversation_history = body.get("conversation_history")
    except Exception:
        pass

    if not message:
        return {"response": "No message provided.", "actions": [], "usage": {}}

    # Check for async mode
    mode = "sync"
    try:
        body_check = await request.json() if hasattr(request, "json") else {}
        if isinstance(body_check, dict):
            mode = body_check.get("mode", "sync")
    except Exception:
        pass

    # Emit structured event for the user message
    from .channel_manager import emit
    emit("orchestrator.message_received", channel_id=channel_id, data={
        "message": message[:200], "project": project, "channel": channel,
    })

    async def _process_orchestrator():
        """Core orchestrator logic — runs sync or as background task."""
        engine = get_intent_engine()
        executor = get_action_executor()

        # Build context from current state
        ctx = build_orchestrator_context(project, session_id, channel, client_id=client_id)
        # Override active workstream if frontend sent the channel_id
        if channel_id:
            ctx.active_workstream_id = channel_id
            if not ctx.active_workstream:
                for ws in ctx.workstreams:
                    if ws.get("id") == channel_id:
                        ctx.active_workstream = ws.get("name")
                        break

        # Process intent via LLM, passing conversation history for multi-turn context
        result = await engine.process(message, ctx, conversation_history=conversation_history)

        # Execute actions (skip tools already executed in the LLM tool loop)
        from .intent import TOOLS_WITH_OUTPUT as _ALREADY_EXECUTED
        executed = []
        for action in result.actions:
            if action.name in _ALREADY_EXECUTED:
                executed.append({"action": action.name, "success": True, "type": "server", **action.params})
            else:
                outcome = await executor.execute(action.name, action.params, ctx)
                executed.append(outcome)

        # Save turns to server-side conversation thread
        try:
            conv_mgr = get_conversation_manager()
            thread_id = conv_mgr.get_or_create_thread(project)
            conv_mgr.append_turn(thread_id, "user", message, channel=channel, client_id=client_id)
            conv_mgr.append_turn(thread_id, "assistant", result.response, channel=channel, actions=executed)
        except Exception:
            logger.debug("Failed to save conversation turn", exc_info=True)

        # Layer 1: Log raw interaction (audit)
        try:
            log_nanobot_interaction(
                channel=channel,
                project=project,
                message=message,
                response=result.response,
                actions=executed,
                model=result.usage.get("model", "unknown"),
                prompt_tokens=result.usage.get("prompt_tokens", 0),
                completion_tokens=result.usage.get("completion_tokens", 0),
                duration_ms=result.usage.get("duration_ms", 0),
            )
        except Exception:
            pass

        # Layer 2: Extract knowledge async (fire-and-forget)
        cfg = load_nanobot_config()
        if project and cfg.get("llm_provider") != "ollama":
            asyncio.create_task(extract_knowledge(message, result.response, project, executed))

        resp = {
            "response": result.response,
            "executed": executed,
            "actions": [{"name": a.name, "params": a.params} for a in result.actions],
            "options": result.options or None,
            "usage": result.usage,
        }
        # Include A2UI components if present
        if result.ui_components:
            resp["ui_components"] = result.ui_components
        return resp

    # Async mode: return immediately, post result to channel when done
    if mode == "async" and channel_id:
        async def _background():
            try:
                result = await _process_orchestrator()
                # Post the response as a channel message
                from .channel_manager import get_channel_manager
                cm = get_channel_manager()
                metadata = {}
                if result.get("ui_components"):
                    metadata["type"] = "a2ui"
                    metadata["components"] = result["ui_components"]
                elif result.get("executed"):
                    metadata["type"] = "action_results"
                    metadata["actions"] = result["executed"]
                    metadata["response"] = result.get("response", "")
                if result.get("usage"):
                    metadata["usage"] = result["usage"]
                content = result.get("response") or ""
                # Don't post empty messages unless they have UI components
                if content or metadata:
                    cm.post_message(
                        channel_id,
                        role="orchestrator",
                        content=content,
                        metadata=metadata if metadata else None,
                    )
            except Exception:
                logger.exception("Background orchestrator task failed")
                try:
                    from .channel_manager import get_channel_manager
                    cm = get_channel_manager()
                    cm.post_message(channel_id, role="system", content="Orchestrator error — please try again.")
                except Exception:
                    pass

        asyncio.create_task(_background())
        return {"response": "Working on it...", "async": True, "executed": [], "actions": [], "usage": {}}

    # Sync mode: wait for result
    return await _process_orchestrator()


@app.post("/conversation/clear")
async def clear_conversation(request: Request):
    """Clear conversation thread for a project."""
    from .conversation import get_conversation_manager
    project = None
    try:
        body = await request.json()
        if isinstance(body, dict):
            project = body.get("project")
    except Exception:
        pass
    conv_mgr = get_conversation_manager()
    cleared = conv_mgr.clear_thread(project)
    return {"cleared": cleared, "project": project}


@app.get("/config/nanobot")
async def get_nanobot_config():
    """Get Nanobot (orchestrator) configuration."""
    from .intent import load_nanobot_config, AVAILABLE_MODELS
    return {
        "config": load_nanobot_config(),
        "available_models": AVAILABLE_MODELS,
    }


@app.patch("/config/nanobot")
async def update_nanobot_config(request: Request):
    """Update Nanobot (orchestrator) configuration."""
    from .intent import load_nanobot_config, save_nanobot_config, _get_model_router
    body = await request.json()
    existing = load_nanobot_config()
    existing.update(body)
    saved = save_nanobot_config(existing)
    # Invalidate model router cache so new routing_mode / overrides take effect
    _get_model_router().invalidate()
    return {"success": True, "config": saved}


# ---------------------------------------------------------------------------
# PinchTab browser automation config + endpoints
# ---------------------------------------------------------------------------

@app.get("/config/pinchtab")
async def get_pinchtab_config():
    """Get PinchTab configuration and status."""
    from .pinchtab import load_pinchtab_config, check_health, get_pinchtab_client
    cfg = load_pinchtab_config()
    available = check_health(cfg.get("port", 9867)) if cfg.get("enabled", True) else False
    tabs = []
    if available:
        try:
            client = get_pinchtab_client()
            if client:
                tabs_data = await client.tabs()
                tabs = tabs_data.get("tabs", []) if isinstance(tabs_data, dict) else []
        except Exception:
            pass
    return {
        "config": cfg,
        "status": {"available": available, "tabs": tabs},
    }


@app.patch("/config/pinchtab")
async def update_pinchtab_config(request: Request):
    """Update PinchTab configuration."""
    from .pinchtab import load_pinchtab_config, save_pinchtab_config, invalidate_health_cache
    body = await request.json()
    existing = load_pinchtab_config()
    existing.update(body)
    saved = save_pinchtab_config(existing)
    invalidate_health_cache()
    return {"success": True, "config": saved}


@app.get("/pinchtab/status")
async def pinchtab_status():
    """Get PinchTab health and open tabs."""
    from .pinchtab import load_pinchtab_config, get_pinchtab_client
    cfg = load_pinchtab_config()
    if not cfg.get("enabled", True):
        return {"available": False, "reason": "disabled", "tabs": []}
    client = get_pinchtab_client()
    if not client:
        return {"available": False, "reason": "no client", "tabs": []}
    healthy = await client.health()
    tabs = []
    if healthy:
        try:
            tabs_data = await client.tabs()
            tabs = tabs_data.get("tabs", []) if isinstance(tabs_data, dict) else []
        except Exception:
            pass
    return {"available": healthy, "tabs": tabs}


@app.post("/pinchtab/navigate")
async def pinchtab_navigate(request: Request):
    """Navigate browser to URL."""
    from .pinchtab import get_pinchtab_client
    client = get_pinchtab_client()
    if not client:
        raise HTTPException(status_code=503, detail="PinchTab not available or disabled")
    if not await client.ensure_running():
        raise HTTPException(status_code=503, detail="PinchTab failed to start")
    body = await request.json()
    url = body.get("url", "")
    tab_id = body.get("tab_id")
    result = await client.navigate(url, tab_id=tab_id)
    return result


@app.get("/pinchtab/snapshot")
async def pinchtab_snapshot(tab_id: str | None = None):
    """Get interactive element tree."""
    from .pinchtab import get_pinchtab_client
    client = get_pinchtab_client()
    if not client:
        raise HTTPException(status_code=503, detail="PinchTab not available or disabled")
    return await client.snapshot(tab_id=tab_id)


@app.post("/pinchtab/action")
async def pinchtab_action(request: Request):
    """Execute browser action (click, fill, type)."""
    from .pinchtab import get_pinchtab_client
    client = get_pinchtab_client()
    if not client:
        raise HTTPException(status_code=503, detail="PinchTab not available or disabled")
    body = await request.json()
    action_type = body.get("type", body.get("kind", "click"))
    ref = body.get("ref", "e0")
    value = body.get("value")
    tab_id = body.get("tab_id")
    result = await client.action(action_type, ref, value=value, tab_id=tab_id)
    return result


@app.get("/pinchtab/text")
async def pinchtab_text(tab_id: str | None = None):
    """Extract readable page text."""
    from .pinchtab import get_pinchtab_client
    client = get_pinchtab_client()
    if not client:
        raise HTTPException(status_code=503, detail="PinchTab not available or disabled")
    return await client.text(tab_id=tab_id)


@app.get("/pinchtab/screenshot")
async def pinchtab_screenshot(tab_id: str | None = None):
    """Get screenshot as base64 PNG."""
    from .pinchtab import get_pinchtab_client
    client = get_pinchtab_client()
    if not client:
        raise HTTPException(status_code=503, detail="PinchTab not available or disabled")
    return await client.screenshot(tab_id=tab_id)


@app.post("/pinchtab/evaluate")
async def pinchtab_evaluate(request: Request):
    """Evaluate JavaScript expression in browser."""
    from .pinchtab import get_pinchtab_client
    client = get_pinchtab_client()
    if not client:
        raise HTTPException(status_code=503, detail="PinchTab not available or disabled")
    body = await request.json()
    expression = body.get("expression", "")
    tab_id = body.get("tab_id")
    return await client.evaluate(expression, tab_id=tab_id)


@app.post("/pinchtab/find")
async def pinchtab_find(request: Request):
    """Find elements on page by natural language description."""
    from .pinchtab import get_pinchtab_client
    client = get_pinchtab_client()
    if not client:
        raise HTTPException(status_code=503, detail="PinchTab not available or disabled")
    body = await request.json()
    description = body.get("description", "")
    tab_id = body.get("tab_id")
    return await client.find(description, tab_id=tab_id)


@app.post("/pinchtab/tabs/{tab_id}/close")
async def pinchtab_close_tab(tab_id: str):
    """Close a PinchTab browser tab."""
    from .pinchtab import get_pinchtab_client
    client = get_pinchtab_client()
    if not client:
        raise HTTPException(status_code=503, detail="PinchTab not available or disabled")
    return await client.close_tab(tab_id)


@app.get("/pinchtab/pdf")
async def pinchtab_pdf(tab_id: str | None = None):
    """Get page as PDF."""
    from .pinchtab import get_pinchtab_client
    from starlette.responses import Response
    client = get_pinchtab_client()
    if not client:
        raise HTTPException(status_code=503, detail="PinchTab not available or disabled")
    pdf_bytes = await client.pdf(tab_id=tab_id)
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": "attachment; filename=page.pdf"})


@app.post("/pinchtab/tabs/{tab_id}/close")
async def pinchtab_close_tab(tab_id: str):
    """Close a PinchTab browser tab."""
    from .pinchtab import get_pinchtab_client
    client = get_pinchtab_client()
    if not client:
        raise HTTPException(status_code=503, detail="PinchTab not available or disabled")
    return await client.close_tab(tab_id)


def _build_action_result_spec(actions: list, results: list) -> dict | None:
    """Build a json-render spec from agent action results."""
    if not actions and not results:
        return None

    elements: dict = {
        "stack": {"type": "Stack", "props": {"direction": "vertical"}, "children": []},
    }
    children = elements["stack"]["children"]

    count = max(len(actions), len(results))
    for i in range(count):
        key = f"r{i}"
        action = actions[i] if i < len(actions) else {}
        result_text = results[i] if i < len(results) else ""
        action_name = action.get("name", "")
        is_error = "failed" in result_text.lower() or "error" in result_text.lower() if result_text else False

        elements[key] = {
            "type": "ActionResult",
            "props": {
                "action": action_name.replace("browser_", "").replace("_", " ") if action_name else (result_text.split(":")[0] if result_text else "action"),
                "status": "error" if is_error else "success",
                "detail": result_text or None,
            },
        }
        children.append(key)

    if not children:
        return None

    return {"root": "stack", "elements": elements}


_BROWSER_AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "browser_fill",
            "description": "Type text into an input element and optionally press Enter to submit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string", "description": "Element ref (e.g. 'e27')"},
                    "value": {"type": "string", "description": "Text to type"},
                    "submit": {"type": "boolean", "description": "Press Enter after (default: true)"},
                },
                "required": ["ref", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "Click an element by ref.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string", "description": "Element ref (e.g. 'e27')"},
                },
                "required": ["ref"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_navigate",
            "description": "Navigate the browser to a different URL. ONLY use when the user EXPLICITLY says 'go to', 'open', or 'navigate to' a specific URL. Do NOT use this to leave the current site.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to navigate to"},
                },
                "required": ["url"],
            },
        },
    },
]

_BROWSER_AGENT_SYSTEM_PROMPT = (
    "You are a browser automation agent. You control a web browser that is viewing a "
    "specific web application. Your job is to interact with THE CURRENT SITE — filling "
    "inputs, clicking buttons/links, and navigating within the site.\n\n"
    "CRITICAL: Stay within the current site. The user's instructions are in the CONTEXT "
    "of the loaded website. For example, if the site is an SEO tool at localhost:3000 "
    "and the user says 'analyze cnn.com', they want you to type 'cnn.com' into the "
    "tool's input field — NOT navigate the browser away to cnn.com.\n\n"
    "DECISION LOGIC:\n"
    "1. ALWAYS prefer interacting with the current page's elements (fill, click).\n"
    "2. When the user mentions a URL/domain, look for an input field to type it into.\n"
    "3. ONLY use browser_navigate when the user EXPLICITLY says 'go to', 'open', or "
    "'navigate to' a URL. Never navigate away from the current site on your own.\n"
    "4. If there are no interactive elements on the page, tell the user the page appears "
    "empty and ask them to wait for it to load or refresh.\n\n"
    "Examples:\n"
    "- SEO tool loaded + 'analyze cnn.com' → find the URL/search input, fill 'cnn.com', click analyze\n"
    "- Dashboard loaded + 'show critical issues' → click the Critical filter/link\n"
    "- Any site + 'search for cats' → fill 'cats' into the search box and submit\n"
    "- User says 'go to https://example.com' → browser_navigate (explicit request)\n"
    "- Page has no elements + 'analyze cnn.com' → respond that the page seems empty, suggest refreshing\n\n"
    "Rules:\n"
    "- ALWAYS call tools to take action. Never just describe what you would do — DO it.\n"
    "- NEVER use browser_navigate unless the user explicitly asks to go to a different site.\n"
    "- Use browser_fill to type into inputs. Set submit=false when you plan to click a button after.\n"
    "- Use browser_click to click buttons and links.\n"
    "- Do NOT call browser_click on a submit button if you used browser_fill with submit=true.\n"
    "- For multi-field forms: browser_fill(submit=false) for each field, then browser_click on submit.\n"
    "- Refs are strings like 'e0', 'e1' from the element list.\n"
)


async def _browser_agent_llm(instruction: str, elements: str, page_info: str) -> dict:
    """Shared LLM call for browser agent — returns {response, actions, model}."""
    import asyncio
    import json as _json

    messages = [
        {"role": "system", "content": _BROWSER_AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": f"{page_info}\n\nInteractive elements:\n{elements}\n\nInstruction: {instruction}"},
    ]

    try:
        from .intent import load_nanobot_config
        from .vault import get_secret
        import os

        cfg = load_nanobot_config()
        model = cfg.get("model_fast", "anthropic/claude-3.5-haiku")

        if cfg.get("llm_provider") == "ollama":
            from openai import OpenAI
            client = OpenAI(api_key="ollama", base_url="http://localhost:11434/v1")
            model = cfg.get("ollama_model", "qwen3.5")
        else:
            from openai import OpenAI
            api_key = (
                get_secret("OPENROUTER_API_KEY")
                or get_secret("OPENAI_API_KEY")
                or os.getenv("OPENROUTER_API_KEY")
                or os.getenv("OPENAI_API_KEY")
            )
            if not api_key:
                return {"response": "No API key configured", "actions": []}
            client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")

        def _call():
            return client.chat.completions.create(
                model=model,
                messages=messages,
                tools=_BROWSER_AGENT_TOOLS,
                max_tokens=400,
            )

        response = await asyncio.to_thread(_call)
        choice = response.choices[0]
        msg = choice.message

        actions = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    params = _json.loads(tc.function.arguments) if tc.function.arguments else {}
                except (ValueError, TypeError):
                    params = {}
                actions.append({"name": tc.function.name, "params": params})

        return {
            "response": msg.content or "",
            "actions": actions,
            "model": model,
        }
    except Exception as e:
        logger.exception("Browser agent LLM error")
        return {"response": f"Error: {e}", "actions": []}


@app.post("/pinchtab/agent")
async def pinchtab_agent(request: Request):
    """Lightweight browser agent — translates user instruction to PinchTab actions.

    Bypasses the full orchestrator for speed. Takes page context + instruction,
    returns tool calls (browser_fill, browser_click, browser_navigate).
    """
    body = await request.json()
    instruction = body.get("instruction", "")
    elements = body.get("elements", "")
    page_info = body.get("page_info", "")

    if not instruction:
        return {"response": "No instruction provided.", "actions": []}

    return await _browser_agent_llm(instruction, elements, page_info)


@app.post("/browser/sessions/{session_id}/agent")
async def browser_session_agent(session_id: str, request: Request):
    """CDP-based browser agent — executes actions in the browser session.

    Self-contained: gets a11y tree via CDP, asks LLM for actions, executes them
    directly on the same browser the user sees in the iframe.
    """
    from .browser import get_browser_manager

    body = await request.json()
    instruction = body.get("instruction", "")
    if not instruction:
        return {"response": "No instruction provided.", "actions_taken": [], "results": []}

    bm = get_browser_manager()
    conn = await bm._ensure_connection(session_id)
    if not conn:
        return {"response": "Browser session not connected.", "actions_taken": [], "results": []}

    # 1. Get interactive elements + page info via CDP
    # NOTE: Do NOT call get_page_info() — it triggers capture_all() which
    # calls Page.stopScreencast and causes visible iframe flicker.
    # Instead, read URL/title directly via lightweight Runtime.evaluate.
    try:
        # Wait for page to be ready (document.readyState === 'complete')
        sid = conn._session_id
        for _ in range(10):
            rs = await conn._cdp("Runtime.evaluate", {
                "expression": "document.readyState",
            }, sid)
            if rs.get("result", {}).get("value") == "complete":
                break
            await asyncio.sleep(0.3)

        elements = await conn.get_interactive_elements()

        # If the a11y tree is empty (page still rendering), wait and retry once
        if not elements:
            await asyncio.sleep(1.0)
            elements = await conn.get_interactive_elements()

        url_r = await conn._cdp("Runtime.evaluate", {"expression": "document.location.href"}, sid)
        title_r = await conn._cdp("Runtime.evaluate", {"expression": "document.title"}, sid)
        page_info_dict = {
            "url": url_r.get("result", {}).get("value", ""),
            "title": title_r.get("result", {}).get("value", ""),
        }
    except Exception as e:
        return {"response": f"Failed to read page: {e}", "actions_taken": [], "results": []}

    url = page_info_dict.get("url", "")
    # Normalize Docker internal URLs back to localhost for display (legacy compat)
    if "host.docker.internal" in url:
        url = url.replace("host.docker.internal", "localhost")
    title = page_info_dict.get("title", "")
    page_info = f"Page: {title} ({url})" if title else f"Page: {url}"

    element_list = "\n".join(
        f"  {el['ref']} [{el['role']}] \"{el.get('name', '')}\""
        + (f" value=\"{el['value']}\"" if el.get("value") else "")
        for el in elements
    )

    # 2. Ask LLM for actions
    llm_result = await _browser_agent_llm(instruction, element_list, page_info)
    actions = llm_result.get("actions", [])
    response_text = llm_result.get("response", "")

    # 3. Execute actions via CDP (reliable, framework-aware)
    results: list[str] = []
    actions_taken: list[dict] = []

    async def _exec_action(action: dict, retry: bool = True) -> None:
        """Execute a single action, retrying once on stale refs."""
        name = action.get("name", "")
        params = action.get("params", {})
        if name == "browser_click":
            r = await conn.click_element(params.get("ref", ""))
            if r.get("error") and "Unknown ref" in r["error"] and retry:
                await asyncio.sleep(0.3)
                await conn.get_interactive_elements()
                return await _exec_action(action, retry=False)
            if r.get("error"):
                results.append(f"Failed click {params.get('ref')}: {r['error']}")
            else:
                results.append(f"Clicked {params.get('ref')}")
                actions_taken.append(action)
        elif name == "browser_fill":
            submit = params.get("submit", True)
            r = await conn.fill_element(params.get("ref", ""), params.get("value", ""), submit=submit)
            if r.get("error") and "Unknown ref" in r["error"] and retry:
                await asyncio.sleep(0.3)
                await conn.get_interactive_elements()
                return await _exec_action(action, retry=False)
            if r.get("error"):
                results.append(f"Failed fill {params.get('ref')}: {r['error']}")
            else:
                results.append(f"Typed \"{params.get('value')}\" into {params.get('ref')}")
                actions_taken.append(action)
        elif name == "browser_navigate":
            nav_url = params.get("url", "")
            if nav_url and not nav_url.startswith(("http://", "https://", "about:", "data:")):
                nav_url = "https://" + nav_url
            r = await conn.navigate(bm._rewrite_url(nav_url))
            if r.get("error"):
                results.append(f"Failed to navigate to {nav_url}: {r['error']}")
            else:
                results.append(f"Navigated to {nav_url}")
                actions_taken.append(action)

    for action in actions:
        try:
            await _exec_action(action)
            # Small delay between actions to let the page settle
            await asyncio.sleep(0.15)
        except Exception as e:
            results.append(f"Error executing {action.get('name', '')}: {e}")

    # NOTE: The viewer runs its own screencast on a separate page-level WebSocket.
    # Restarting screencast on the server's browser-level session does not affect
    # the viewer.  The viewer has a client-side watchdog (2s idle → stop/start
    # cycle) that handles recovery from stalled frame delivery.

    # Build json-render spec from action results
    spec = _build_action_result_spec(actions_taken, results)

    return {
        "response": response_text or ("; ".join(results) if results else "No actions taken."),
        "actions_taken": actions_taken,
        "results": results,
        "model": llm_result.get("model", ""),
        "spec": spec,
    }


@app.post("/browser/sessions/{session_id}/agent/loop")
async def browser_session_agent_loop(session_id: str, request: Request):
    """Multi-step observe->act browser agent loop.

    Repeatedly observes the page and asks the LLM for actions until
    the task is complete or max_steps is reached.

    Returns intermediate steps for transparency.
    """
    from .browser import get_browser_manager
    from .browser_use import get_or_create_session

    body = await request.json()
    instruction = body.get("instruction", "")
    max_steps = min(body.get("max_steps", 10), 20)

    if not instruction:
        return {"response": "No instruction provided.", "steps": [], "done": False}

    bm = get_browser_manager()
    conn = await bm._ensure_connection(session_id)
    if not conn:
        return {"response": "Browser session not connected.", "steps": [], "done": False}

    bus = await get_or_create_session(session_id, conn.container_port, live_conn=conn)

    steps: list[dict] = []
    all_results: list[str] = []
    all_actions: list[dict] = []

    for step_num in range(max_steps):
        # 1. Observe
        state = await bus.observe()
        if state.get("error"):
            steps.append({"step": step_num + 1, "type": "error", "detail": state["error"]})
            break

        url = state.get("url", "")
        title = state.get("title", "")
        elements = state.get("elements", [])

        page_info = f"Page: {title} ({url})" if title else f"Page: {url}"
        element_list = "\n".join(
            f"  {el['ref']} [{el['role']}] \"{el.get('name', '')}\""
            + (f" value=\"{el['value']}\"" if el.get("value") else "")
            for el in elements
        )

        # Include history of what we've done so far
        history_context = ""
        if all_results:
            history_context = "\n\nActions taken so far:\n" + "\n".join(f"- {r}" for r in all_results)

        # 2. Ask LLM for next action(s)
        llm_result = await _browser_agent_llm(
            instruction + history_context,
            element_list,
            page_info,
        )
        actions = llm_result.get("actions", [])
        response_text = llm_result.get("response", "")

        step_data = {
            "step": step_num + 1,
            "type": "act",
            "page": {"url": url, "title": title},
            "actions": actions,
            "response": response_text,
            "results": [],
        }

        # 3. Check if LLM says we're done (no actions returned)
        if not actions:
            step_data["type"] = "done"
            steps.append(step_data)
            return {
                "response": response_text or "Task completed.",
                "steps": steps,
                "actions_taken": all_actions,
                "results": all_results,
                "done": True,
                "model": llm_result.get("model", ""),
                "spec": _build_action_result_spec(all_actions, all_results),
            }

        # 4. Execute actions
        for action in actions:
            action_name = action.get("name", "")
            params = action.get("params", {})
            try:
                if action_name == "browser_click":
                    r = await bus.act("click", ref=params.get("ref", ""))
                elif action_name in ("browser_fill", "browser_type"):
                    r = await bus.act("type", ref=params.get("ref", ""), value=params.get("value", ""), submit=params.get("submit", True))
                elif action_name == "browser_navigate":
                    nav_url = params.get("url", "")
                    if nav_url and not nav_url.startswith(("http://", "https://", "about:", "data:")):
                        nav_url = "https://" + nav_url
                    r = await bus.act("navigate", url=bm._rewrite_url(nav_url))
                else:
                    r = {"error": f"Unknown action: {action_name}"}

                if r.get("error"):
                    result_text = f"Failed {action_name}: {r['error']}"
                else:
                    result_text = f"{action_name}: OK"
                    all_actions.append(action)
            except Exception as e:
                result_text = f"Error {action_name}: {e}"

            all_results.append(result_text)
            step_data["results"].append(result_text)

        steps.append(step_data)

        # Brief pause between steps
        await asyncio.sleep(0.3)

    # Max steps reached
    return {
        "response": f"Reached max steps ({max_steps}). " + ("; ".join(all_results[-3:]) if all_results else ""),
        "steps": steps,
        "actions_taken": all_actions,
        "results": all_results,
        "done": False,
        "spec": _build_action_result_spec(all_actions, all_results),
    }


@app.post("/chat/message")
async def chat_with_screen(
    message: str,
    session_id: str | None = None,
    include_screenshot: bool = False,
    project: str | None = None,
    task_id: str | None = None,
    mode: str = "dashboard",  # "dashboard" or "preview"
    execute_actions: bool = True,  # Whether to execute suggested actions
):
    """Context-aware chat with AI that can execute actions.
    
    Dashboard mode: Focus on RDC operations (tasks, agents, processes)
    Preview mode: Focus on UI/visual feedback with auto-screenshots
    """
    import base64
    import os
    import json
    from pathlib import Path
    
    try:
        from openai import OpenAI
    except ImportError:
        raise HTTPException(status_code=500, detail="OpenAI not installed")
    
    # Get API key from vault first, then environment
    from .vault import get_secret
    
    api_key = (
        get_secret("OPENROUTER_API_KEY") or 
        get_secret("OPENAI_API_KEY") or
        os.getenv("OPENROUTER_API_KEY") or 
        os.getenv("OPENAI_API_KEY")
    )
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY or OPENAI_API_KEY not set. Use 'rdc config set-secret OPENROUTER_API_KEY <key>'")
    
    # Determine if using OpenRouter based on which key was found
    use_openrouter = bool(get_secret("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY"))
    
    if use_openrouter:
        client = OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1"
        )
    else:
        client = OpenAI(api_key=api_key)
    
    # Build context based on mode
    context_parts = []
    rdc_state = {}
    
    # Gather RDC state for dashboard mode
    if mode == "dashboard":
        try:
            from .db.repositories import get_task_repo, get_project_repo
            
            # Get tasks summary
            task_repo = get_task_repo()
            all_tasks = task_repo.list()
            pending_tasks = [t for t in all_tasks if t.get("status") == "pending"]
            in_progress = [t for t in all_tasks if t.get("status") == "in_progress"]
            
            rdc_state["total_tasks"] = len(all_tasks)
            rdc_state["pending_tasks"] = len(pending_tasks)
            rdc_state["in_progress_tasks"] = len(in_progress)
            
            if pending_tasks:
                rdc_state["pending_task_titles"] = [t.get("title", "Untitled")[:50] for t in pending_tasks[:5]]
            if in_progress:
                rdc_state["in_progress_titles"] = [t.get("title", "Untitled")[:50] for t in in_progress[:3]]
            
            # Get projects
            project_repo = get_project_repo()
            projects = project_repo.list()
            rdc_state["projects"] = [p.get("name") for p in projects[:10]]
            
        except Exception as e:
            rdc_state["error"] = str(e)
    
    if project:
        context_parts.append(f"Selected project: {project}")
        try:
            from .db.repositories import get_project_repo
            project_repo = get_project_repo()
            proj = project_repo.get(project)
            if proj and proj.get("path"):
                context_parts.append(f"Project path: {proj['path']}")
        except Exception:
            pass
    
    if task_id:
        context_parts.append(f"Active task ID: {task_id}")
        try:
            from .db.repositories import get_task_repo
            task_repo = get_task_repo()
            task = task_repo.get(task_id)
            if task:
                context_parts.append(f"Task: {task.get('title', 'Unknown')}")
                context_parts.append(f"Task status: {task.get('status', 'Unknown')}")
                if task.get('description'):
                    context_parts.append(f"Task description: {task['description'][:300]}")
        except Exception:
            pass
    
    context_str = "\n".join(context_parts) if context_parts else ""
    
    # Build system prompt based on mode
    if mode == "preview":
        system_prompt = f"""You are an AI assistant viewing a live preview of a web application.

{context_str}

You can see screenshots of the application. Help the user with:
- Identifying UI/UX issues
- Spotting errors or broken layouts
- Suggesting improvements
- Debugging visible problems

If you need to see the current screen to answer, say "Let me take a look at the screen" and a screenshot will be captured.

Be concise and reference visual elements by position (top-left, center, etc.)."""
    else:
        # Dashboard mode - focus on RDC operations
        rdc_summary = json.dumps(rdc_state, indent=2) if rdc_state else "No data available"
        
        # Get available processes
        process_list = []
        try:
            from .processes import get_process_manager
            pm = get_process_manager()
            
            # Get all processes from all projects
            for pid, proc in pm._processes.items():
                # ProcessState is a Pydantic model, access as attributes
                status = proc.status.value if hasattr(proc.status, 'value') else str(proc.status)
                name = proc.name
                port = proc.port
                port_str = f" (port {port})" if port else ""
                process_list.append(f"- {pid}: {status}{port_str}")
        except Exception as e:
            process_list.append(f"Error loading processes: {str(e)}")
        
        process_info = "\n".join(process_list) if process_list else "No processes configured. Use Sync in Processes tab first."
        
        system_prompt = f"""You are an AI assistant for RDC (Remote Dev Ctrl) Command Center. You can execute actions directly.

Current RDC State:
{rdc_summary}

Available Processes:
{process_info}

{context_str}

You help the user manage their development workflow by EXECUTING actions, not just suggesting them.

When the user wants to do something, include an ACTION block in your response. The backend will execute these:

[ACTION:start_action:action_id]
[ACTION:stop_action:action_id]
[ACTION:create_task:title|description] or [ACTION:create_task:project:title|description]
[ACTION:start_preview:action_id]

For UI-only actions (switching views, navigation), use UI_ACTION which the frontend handles:

[UI_ACTION:show_tab:processes]
[UI_ACTION:show_tab:tasks]
[UI_ACTION:show_tab:workers]
[UI_ACTION:show_tab:system]
[UI_ACTION:select_project:project_name]
[UI_ACTION:open_task_modal]

Examples:
- "Start the frontend" -> [ACTION:start_action:mindshare-monitor-frontend] I'm starting the frontend now.
- "Show me the actions" -> [UI_ACTION:show_tab:processes] Here are your actions.
- "Switch to documaker project" -> [UI_ACTION:select_project:documaker] Switched to documaker.
- "Create a task to fix login" -> [ACTION:create_task:Fix login bug|Investigate and fix the login issue] Created a task for the login fix.
- "Add a task for documaker to add dark mode" -> [ACTION:create_task:documaker:Add dark mode|Implement dark mode toggle in settings] Created the task.
- "I want to add a new task" -> [UI_ACTION:open_task_modal] I've opened the task form for you.

IMPORTANT: Use the exact process IDs from the list above. Be concise. Execute first, then briefly confirm."""
    
    messages = [{"role": "system", "content": system_prompt}]
    
    # Build user message content
    content = []
    
    # Auto-capture screenshot in preview mode if message suggests visual context needed
    auto_capture = False
    if mode == "preview" and session_id:
        visual_keywords = ["see", "look", "show", "screen", "page", "button", "layout", "ui", "display", "error", "broken", "wrong", "issue"]
        if any(kw in message.lower() for kw in visual_keywords):
            auto_capture = True
    
    # Add screenshot if requested or auto-captured
    if (include_screenshot or auto_capture) and session_id:
        from .vnc import get_vnc_manager
        vnc_manager = get_vnc_manager()
        screenshot = vnc_manager.capture_screenshot(session_id)
        
        if screenshot:
            b64_image = base64.b64encode(screenshot).decode('utf-8')
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64_image}"}
            })
    
    content.append({"type": "text", "text": message})
    
    messages.append({"role": "user", "content": content})
    
    try:
        # Use a vision-capable model
        model = "anthropic/claude-3.5-sonnet" if use_openrouter else "gpt-4o-mini"
        
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=500,
        )
        
        ai_response = response.choices[0].message.content
        
        # Parse and execute actions from the response
        actions_executed = []
        if execute_actions:
            import re
            action_pattern = r'\[ACTION:(\w+):([^\]]+)\]'
            actions = re.findall(action_pattern, ai_response)
            
            for action_type, action_params in actions:
                action_result = await _execute_chat_action(action_type, action_params)
                if action_result:
                    actions_executed.append(action_result)
            
            # Remove action tags from displayed response
            clean_response = re.sub(action_pattern, '', ai_response).strip()
        else:
            clean_response = ai_response
        
        return {
            "response": clean_response,
            "actions": actions_executed,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/tts/speak")
async def text_to_speech(request: Request):
    """Convert text to speech using configured TTS provider with fallback."""
    from fastapi.responses import Response
    from .tts import get_tts_service
    
    data = await request.json()
    text = data.get("text", "")
    voice = data.get("voice")  # Optional, uses config default
    provider = data.get("provider")  # Optional, uses config default
    
    if not text:
        raise HTTPException(status_code=400, detail="No text provided")
    
    tts = get_tts_service()
    
    try:
        audio = await tts.speak(text, voice=voice, provider=provider)
        return Response(
            content=audio,
            media_type="audio/mpeg",
            headers={"Content-Disposition": "inline; filename=speech.mp3"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tts/config")
async def get_tts_config():
    """Get current TTS configuration."""
    from .tts import get_tts_service, ELEVENLABS_VOICES, DEEPGRAM_VOICES
    
    tts = get_tts_service()
    config = tts.get_config()
    
    return {
        "config": {
            "provider": config.provider.value,
            "voice": config.voice,
            "fallback_provider": config.fallback_provider.value,
            "fallback_voice": config.fallback_voice,
            "elevenlabs_model": config.elevenlabs_model,
            "elevenlabs_stability": config.elevenlabs_stability,
            "elevenlabs_similarity": config.elevenlabs_similarity,
        },
        "available_voices": {
            "elevenlabs": list(ELEVENLABS_VOICES.keys()),
            "deepgram": list(DEEPGRAM_VOICES.keys()),
            "openai": ["alloy", "echo", "fable", "onyx", "nova", "shimmer"],
        }
    }


@app.post("/tts/config")
async def set_tts_config(request: Request):
    """Update TTS configuration."""
    from .tts import get_tts_service
    
    data = await request.json()
    tts = get_tts_service()
    
    config = tts.set_config(**data)
    
    return {
        "success": True,
        "config": {
            "provider": config.provider.value,
            "voice": config.voice,
            "fallback_provider": config.fallback_provider.value,
            "fallback_voice": config.fallback_voice,
        }
    }


@app.post("/stt/transcribe")
async def speech_to_text(request: Request):
    """Transcribe audio using Deepgram."""
    from fastapi.responses import JSONResponse
    import httpx
    
    api_key = get_secret("DEEPGRAM_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="DEEPGRAM_API_KEY not configured")
    
    # Get audio data from request
    content_type = request.headers.get("content-type", "audio/webm")
    audio_data = await request.body()
    
    if not audio_data:
        raise HTTPException(status_code=400, detail="No audio data provided")
    
    # Call Deepgram API
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.deepgram.com/v1/listen",
            params={
                "model": "nova-2",
                "smart_format": "true",
                "punctuate": "true",
                "language": "en",
            },
            headers={
                "Authorization": f"Token {api_key}",
                "Content-Type": content_type,
            },
            content=audio_data,
            timeout=30.0,
        )
        
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=f"Deepgram error: {response.text}")
        
        result = response.json()
        
        # Extract transcript
        try:
            transcript = result["results"]["channels"][0]["alternatives"][0]["transcript"]
            confidence = result["results"]["channels"][0]["alternatives"][0]["confidence"]
        except (KeyError, IndexError):
            transcript = ""
            confidence = 0
        
        return {"transcript": transcript, "confidence": confidence}


@app.websocket("/stt/stream")
async def stt_stream(websocket: WebSocket):
    """Stream audio to Deepgram and return real-time transcripts."""
    import websockets
    
    await websocket.accept()
    
    api_key = get_secret("DEEPGRAM_API_KEY")
    if not api_key:
        await websocket.send_json({"error": "DEEPGRAM_API_KEY not configured"})
        await websocket.close()
        return
    
    deepgram_ws = None
    
    try:
        # Connect to Deepgram streaming API
        deepgram_url = (
            "wss://api.deepgram.com/v1/listen?"
            "model=nova-2&"
            "punctuate=true&"
            "smart_format=true&"
            "language=en&"
            "encoding=linear16&"
            "sample_rate=16000"
        )
        
        deepgram_ws = await websockets.connect(
            deepgram_url,
            additional_headers={"Authorization": f"Token {api_key}"},
        )
        
        logging.info("Connected to Deepgram streaming API")
        await websocket.send_json({"status": "connected"})
        
        async def receive_from_deepgram():
            """Receive transcripts from Deepgram and forward to client."""
            try:
                async for message in deepgram_ws:
                    data = json.loads(message)
                    
                    # Extract transcript from Deepgram response
                    if "channel" in data:
                        alt = data.get("channel", {}).get("alternatives", [{}])[0]
                        transcript = alt.get("transcript", "")
                        is_final = data.get("is_final", False)
                        confidence = alt.get("confidence", 0)
                        
                        if transcript:
                            await websocket.send_json({
                                "transcript": transcript,
                                "is_final": is_final,
                                "confidence": confidence,
                            })
            except websockets.exceptions.ConnectionClosed:
                logging.info("Deepgram connection closed")
            except Exception as e:
                logging.error(f"Deepgram receive error: {e}")
        
        async def receive_from_client():
            """Receive audio from client and forward to Deepgram."""
            try:
                while True:
                    data = await websocket.receive()
                    
                    if "bytes" in data:
                        # Forward audio bytes to Deepgram
                        await deepgram_ws.send(data["bytes"])
                    elif "text" in data:
                        msg = json.loads(data["text"])
                        if msg.get("type") == "stop":
                            # Send close message to Deepgram
                            await deepgram_ws.send(json.dumps({"type": "CloseStream"}))
                            break
            except WebSocketDisconnect:
                logging.info("Client disconnected from STT stream")
            except Exception as e:
                logging.error(f"Client receive error: {e}")
        
        # Run both tasks concurrently
        await asyncio.gather(
            receive_from_deepgram(),
            receive_from_client(),
        )
        
    except Exception as e:
        logging.error(f"STT stream error: {e}")
        try:
            await websocket.send_json({"error": str(e)})
        except:
            pass
    finally:
        if deepgram_ws:
            await deepgram_ws.close()
        try:
            await websocket.close()
        except:
            pass


@app.get("/admin/settings")
async def get_all_settings():
    """Get all admin settings."""
    from .tts import get_tts_service
    from .vault import list_secrets
    
    tts = get_tts_service()
    settings = tts.get_all_settings()
    
    # Get configured secrets (just names, not values)
    try:
        secrets = list_secrets()
    except Exception:
        secrets = []
    
    return {
        "settings": settings,
        "secrets": secrets,
    }


@app.post("/admin/settings")
async def set_admin_setting(request: Request):
    """Set an admin setting."""
    from .tts import get_tts_service
    
    data = await request.json()
    key = data.get("key")
    value = data.get("value")
    
    if not key:
        raise HTTPException(status_code=400, detail="key required")
    
    tts = get_tts_service()
    tts.set_setting(key, str(value))
    
    return {"success": True, "key": key}


async def _execute_chat_action(action_type: str, params: str) -> dict | None:
    """Execute an action from the chat AI."""
    try:
        if action_type == "start_action":
            from .processes import get_process_manager
            pm = get_process_manager()
            result = pm.start(params.strip())
            return {"action": "start_action", "process_id": params, "success": result.get("success", False)}

        elif action_type == "stop_action":
            from .processes import get_process_manager
            pm = get_process_manager()
            result = pm.stop(params.strip())
            return {"action": "stop_action", "process_id": params, "success": result.get("success", False)}
        
        elif action_type == "show_tab":
            # This is handled by the frontend
            return {"action": "show_tab", "tab": params.strip()}
        
        elif action_type == "create_task":
            # Format: title|description or project:title|description
            parts = params.split("|", 1)
            title_part = parts[0].strip()
            description = parts[1].strip() if len(parts) > 1 else ""
            
            # Check if project is specified (project:title format)
            if ":" in title_part:
                project, title = title_part.split(":", 1)
                project = project.strip()
                title = title.strip()
            else:
                project = None
                title = title_part
            
            from .db.repositories import get_task_repo, resolve_project_id as _resolve_pid
            task_repo = get_task_repo()
            _pid = _resolve_pid(project) if project else ""
            task_id = task_repo.create(
                project_id=_pid or "",
                description=description or title,
            )
            return {"action": "create_task", "task_id": task_id.id if hasattr(task_id, 'id') else task_id, "title": title, "project": project, "success": True}
        
        elif action_type == "start_preview":
            from .vnc import get_vnc_manager
            vnc = get_vnc_manager()
            session = vnc.create_session(params.strip(), f"http://localhost:4000")
            return {"action": "start_preview", "session_id": session.id, "success": session.status.value == "running"}
        
    except Exception as e:
        return {"action": action_type, "error": str(e), "success": False}
    
    return None


@app.api_route("/vnc/proxy/{session_id}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"])
async def vnc_proxy(session_id: str, path: str, request: Request):
    """Reverse proxy to VNC session — retained for backward compat.

    For native VNC, prefer using /desktop/vnc-proxy WebSocket endpoint with noVNC.
    """
    from .vnc import get_vnc_manager

    vnc_manager = get_vnc_manager()
    session = vnc_manager.get_session(session_id)

    if not session:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    if session.status.value != "running":
        raise HTTPException(status_code=400, detail=f"Session not running: {session.status.value}")

    raise HTTPException(
        status_code=410,
        detail="Direct HTTP proxy removed. Use /desktop/vnc-proxy WebSocket endpoint with noVNC viewer.",
    )


@app.websocket("/vnc/ws/{session_id}")
async def vnc_websocket_proxy(websocket: WebSocket, session_id: str):
    """WebSocket proxy to native VNC server for noVNC client.

    Bridges WebSocket (from noVNC in the browser) to raw TCP (VNC server).
    noVNC requires the 'binary' subprotocol to be accepted.
    """
    from .vnc import get_vnc_manager
    import socket as _socket

    vnc_manager = get_vnc_manager()
    session = vnc_manager.get_session(session_id)

    if not session or session.status.value != "running":
        await websocket.close(code=4004, reason="Session not found or not running")
        return

    # Accept with "binary" subprotocol if client requested it, plain accept otherwise
    requested = websocket.headers.get("sec-websocket-protocol", "")
    if "binary" in requested:
        await websocket.accept(subprotocol="binary")
    else:
        await websocket.accept()

    vnc_sock = None
    try:
        vnc_sock = _socket.create_connection(("localhost", session.vnc_port), timeout=5)
        vnc_sock.setblocking(False)
        loop = asyncio.get_running_loop()
        logger.info(f"VNC proxy: connected to localhost:{session.vnc_port} for session {session_id}")

        async def forward_vnc_to_ws():
            try:
                while True:
                    data = await loop.sock_recv(vnc_sock, 65536)
                    if not data:
                        logger.info("VNC proxy: VNC server closed connection")
                        break
                    await websocket.send_bytes(data)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"VNC proxy vnc→ws ended: {e}")

        async def forward_ws_to_vnc():
            try:
                while True:
                    msg = await websocket.receive()
                    if msg.get("type") == "websocket.disconnect":
                        break
                    data = msg.get("bytes")
                    if data:
                        await loop.sock_sendall(vnc_sock, data)
                    # noVNC can also send text frames (rare) — forward as bytes
                    text = msg.get("text")
                    if text:
                        await loop.sock_sendall(vnc_sock, text.encode())
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"VNC proxy ws→vnc ended: {e}")

        await asyncio.gather(forward_vnc_to_ws(), forward_ws_to_vnc())

    except Exception as e:
        logger.error(f"VNC WebSocket proxy error: {e}")
    finally:
        if vnc_sock:
            try:
                vnc_sock.close()
            except Exception:
                pass
        try:
            await websocket.close()
        except Exception:
            pass


@app.get("/actions/{process_id}/vnc")
async def get_process_vnc(process_id: str):
    """Get VNC session for a specific process."""
    from .vnc import get_vnc_manager
    
    vnc_manager = get_vnc_manager()
    session = vnc_manager.get_by_process(process_id)
    
    if not session:
        return {"session": None}
    
    return {
        "session": {
            "id": session.id,
            "status": session.status.value,
            "vnc_port": session.vnc_port,
            "viewer_url": f"http://localhost:{session.vnc_port}",
            "error": session.error,
        }
    }


# =============================================================================
# Port Management
# =============================================================================

@app.get("/ports")
async def list_ports(project: str | None = None):
    """List all port assignments."""
    from .ports import get_port_manager
    from .db.repositories import _resolve_project_names

    pm = get_port_manager()
    assignments = pm.list_assignments(project=project)

    # Resolve project_id UUIDs back to names
    project_ids = list({a["project_id"] for a in assignments if a.get("project_id")})
    name_map = _resolve_project_names(project_ids) if project_ids else {}

    return [
        {
            "project": name_map.get(a["project_id"], a["project_id"]),
            "service": a["service"],
            "port": a["port"],
            "in_use": a.get("in_use", False),
        }
        for a in assignments
    ]


class PortAssignRequest(BaseModel):
    project: str
    service: str
    port: int | None = None  # If None, auto-assign
    force_new: bool = False  # Force finding a new port (for conflict resolution)


@app.post("/ports/assign")
async def assign_port(req: PortAssignRequest):
    """Assign a port to a project service."""
    from .ports import get_port_manager
    
    pm = get_port_manager()
    
    try:
        port = pm.assign_port(req.project, req.service, preferred=req.port, force_new=req.force_new)
        
        # Also update the process command if exists
        process_id = f"{req.project}-{req.service}"
        proc_state = process_manager.get(process_id)
        if proc_state:
            old_port = proc_state.port
            if old_port != port:
                # Update command with new port
                proc_state.command = process_manager._update_command_port(proc_state.command, old_port, port)
                proc_state.port = port
                process_manager._repo.upsert(proc_state)
        
        return {"success": True, "project": req.project, "service": req.service, "port": port}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/ports/set")
async def set_port(req: PortAssignRequest, force: bool = False):
    """Explicitly set a port for a service.
    
    Args:
        force: If True, set the port even if it's currently in use (just update registry)
    """
    from .ports import get_port_manager
    
    if not req.port:
        raise HTTPException(status_code=400, detail="Port is required")
    
    pm = get_port_manager()
    
    if force:
        # Force mode: just update the DB without checking availability
        from .db.repositories import resolve_project_id
        project_id = resolve_project_id(req.project) or ""
        if project_id:
            pm._repo.upsert(project_id, req.service, req.port)
        return {"success": True, "project": req.project, "service": req.service, "port": req.port, "forced": True}
    
    success = pm.set_port(req.project, req.service, req.port)
    
    if not success:
        # Check if port is in use and provide more info
        info = process_manager.get_port_process_info(req.port)
        if info:
            raise HTTPException(
                status_code=400, 
                detail=f"Port {req.port} is in use by PID {info.get('pid')} ({info.get('command', 'unknown')}). Use force=true to set anyway."
            )
        raise HTTPException(status_code=400, detail=f"Port {req.port} is not available")
    
    return {"success": True, "project": req.project, "service": req.service, "port": req.port}


@app.delete("/ports/{project}/{service}")
async def release_port(project: str, service: str):
    """Release a port assignment."""
    from .ports import get_port_manager
    
    pm = get_port_manager()
    pm.release_port(project, service)
    
    return {"success": True, "released": f"{project}:{service}"}


# =============================================================================
# Screenshots
# =============================================================================

import uuid
from datetime import datetime

SCREENSHOTS_DIR = get_rdc_home() / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

# Simple in-memory storage (could be moved to DB later)
_screenshots: dict[str, dict] = {}

def _load_screenshots_from_disk():
    """Load existing screenshots from disk on startup."""
    import re
    for filepath in SCREENSHOTS_DIR.glob("*.png"):
        # Parse filename: project_id.png
        name = filepath.stem
        match = re.match(r"(.+)_([a-f0-9]+)$", name)
        if match:
            project = match.group(1)
            screenshot_id = match.group(2)
        else:
            project = "unknown"
            screenshot_id = name[:8]
        
        # Get file modification time as timestamp
        mtime = filepath.stat().st_mtime
        from datetime import datetime
        timestamp = datetime.fromtimestamp(mtime).isoformat()
        
        _screenshots[screenshot_id] = {
            "id": screenshot_id,
            "project": project,
            "filepath": str(filepath),
            "timestamp": timestamp,
        }

# Load on module import
_load_screenshots_from_disk()


@app.get("/screenshots")
async def list_screenshots(project: str | None = None):
    """List screenshots, optionally filtered by project."""
    screenshots = list(_screenshots.values())
    if project:
        screenshots = [s for s in screenshots if s.get("project") == project]
    # Sort by timestamp descending
    screenshots.sort(key=lambda s: s.get("timestamp", ""), reverse=True)
    return screenshots


@app.post("/screenshots")
async def capture_screenshot(
    project: Optional[str] = None,
    process_id: Optional[str] = None,
    full_page: bool = True,
):
    """Capture a screenshot from the active VNC session.
    
    Provide either project or process_id to find the VNC session.
    
    Args:
        project: Project name to find the VNC session for.
        process_id: Process ID to find the VNC session for.
        full_page: If true (default), capture the full scrollable page content
                   using headless Chrome. If false, capture only the visible viewport.
    """
    from .vnc import get_vnc_manager, VNCStatus
    
    if not project and not process_id:
        raise HTTPException(status_code=400, detail="Either project or process_id is required")
    
    vnc_mgr = get_vnc_manager()
    sessions = vnc_mgr.list_sessions()
    
    session = None

    # Match by process_id first (more specific)
    if process_id:
        for s in sessions:
            if s.status == VNCStatus.RUNNING:
                if s.process_id and (s.process_id == process_id or process_id in s.process_id):
                    session = s
                    break

    # Then try project match
    if not session and project:
        for s in sessions:
            if s.status == VNCStatus.RUNNING:
                if s.process_id and project in s.process_id:
                    session = s
                    break

    # Fallback: any running session
    if not session:
        for s in sessions:
            if s.status == VNCStatus.RUNNING:
                session = s
                break

    if not session:
        raise HTTPException(status_code=404, detail="No active VNC session. Start desktop sharing first.")

    # Derive project name if not provided
    if not project:
        project = (session.process_id or "unknown").split("-")[0]
    
    screenshot_id = str(uuid.uuid4())[:8]
    timestamp = datetime.now().isoformat()
    filename = f"{project}_{screenshot_id}.png"
    filepath = SCREENSHOTS_DIR / filename
    
    # Use VNC manager's capture method
    try:
        image_bytes = vnc_mgr.capture_screenshot(session.id, full_page=full_page)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Screenshot capture error: {e}")
    
    if not image_bytes:
        capture_type = "full page" if full_page else "viewport"
        raise HTTPException(
            status_code=500,
            detail=f"Failed to capture {capture_type} screenshot. Ensure screencapture (macOS) or scrot (Linux) is available."
        )
    
    filepath.write_bytes(image_bytes)
    
    capture_type = "full page" if full_page else "viewport"
    screenshot_data = {
        "id": screenshot_id,
        "project": project,
        "timestamp": timestamp,
        "filepath": str(filepath),
        "filename": filename,
        "session_id": session.id,
        "full_page": full_page,
        "description": f"Screenshot ({capture_type}) from {session.target_url or project}"
    }
    _screenshots[screenshot_id] = screenshot_data
    
    return screenshot_data


@app.get("/screenshots/{screenshot_id}/image")
async def get_screenshot_image(screenshot_id: str):
    """Get screenshot image file."""
    from fastapi.responses import FileResponse
    
    screenshot = _screenshots.get(screenshot_id)
    if not screenshot:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    
    filepath = Path(screenshot["filepath"])
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Screenshot file not found")
    
    return FileResponse(filepath, media_type="image/png")


@app.delete("/screenshots/{screenshot_id}")
async def delete_screenshot(screenshot_id: str):
    """Delete a screenshot."""
    screenshot = _screenshots.get(screenshot_id)
    if not screenshot:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    
    # Delete file
    filepath = Path(screenshot["filepath"])
    if filepath.exists():
        filepath.unlink()
    
    # Remove from storage
    del _screenshots[screenshot_id]
    
    return {"success": True}


@app.post("/screenshots/upload")
async def upload_screenshot(
    file: UploadFile = File(...),
    project: str = Form("upload"),
    description: str = Form(""),
):
    """Upload a screenshot or image file."""
    allowed = {"image/png", "image/jpeg", "image/gif", "image/webp"}
    if file.content_type not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {file.content_type}")

    ext = Path(file.filename or "image.png").suffix or ".png"
    screenshot_id = str(uuid.uuid4())[:8]
    timestamp = datetime.now().isoformat()
    filename = f"{project}_{screenshot_id}{ext}"
    filepath = SCREENSHOTS_DIR / filename

    data = await file.read()
    filepath.write_bytes(data)

    screenshot_data = {
        "id": screenshot_id,
        "project": project,
        "timestamp": timestamp,
        "filepath": str(filepath),
        "filename": filename,
        "description": description or file.filename or "Uploaded image",
    }
    _screenshots[screenshot_id] = screenshot_data
    return screenshot_data


# =============================================================================
# Terminal Sessions (Interactive PTY)
# =============================================================================

@app.get("/terminals")
async def list_terminals():
    """List all terminal sessions."""
    from .terminal import get_terminal_manager
    
    tm = get_terminal_manager()
    return [
        {
            "id": s.id,
            "project": s.project,
            "command": s.command,
            "cwd": s.cwd,
            "status": s.status.value,
            "pid": s.pid,
            "cols": s.cols,
            "rows": s.rows,
            "created_at": s.created_at.isoformat(),
        }
        for s in tm.list()
    ]


@app.post("/terminals")
async def create_terminal(
    project: str,
    command: str | None = None,
    cwd: str | None = None,
    cols: int = 120,
    rows: int = 30,
    mode: str | None = None,
    channel_id: str | None = None,
):
    """Create a new terminal session for a project.

    If channel_id is provided, the terminal is auto-linked to that channel.
    """
    from .terminal import get_terminal_manager

    tm = get_terminal_manager()
    session = tm.create(
        project=project,
        command=command,
        cwd=cwd,
        cols=cols,
        rows=rows,
        mode=mode,
    )

    # Auto-link terminal to channel if provided
    if channel_id:
        try:
            from .channel_manager import get_channel_manager
            get_channel_manager().link_terminal(session.id, channel_id)
        except Exception:
            pass  # Don't fail terminal creation if channel linking fails

    event_repo.log(
        "terminal.created",
        project=project,
        message=f"Terminal started for {project}",
    )

    # Emit structured event
    from .channel_manager import emit
    emit("terminal.created", channel_id=channel_id, data={
        "terminal_id": session.id, "project": project, "command": command or "",
    })

    # Broadcast state so all clients see the new terminal
    machine = get_state_machine()
    await machine._broadcast_state()

    return {
        "id": session.id,
        "project": session.project,
        "command": session.command,
        "status": session.status.value,
        "pid": session.pid,
        "ws_url": f"/terminals/{session.id}/ws",
        "channel_id": channel_id,
    }


@app.get("/terminals/{session_id}")
async def get_terminal(session_id: str):
    """Get terminal session info."""
    from .terminal import get_terminal_manager
    
    tm = get_terminal_manager()
    session = tm.get(session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail=f"Terminal not found: {session_id}")
    
    return {
        "id": session.id,
        "project": session.project,
        "command": session.command,
        "cwd": session.cwd,
        "status": session.status.value,
        "pid": session.pid,
        "cols": session.cols,
        "rows": session.rows,
    }


@app.delete("/terminals/{session_id}")
async def destroy_terminal(session_id: str):
    """Destroy a terminal session."""
    from .terminal import get_terminal_manager

    tm = get_terminal_manager()
    # Get project name before destroying
    session = tm.get(session_id)
    project_name = session.project if session else session_id.removeprefix("term-")
    success = tm.destroy(session_id)

    if not success:
        raise HTTPException(status_code=404, detail=f"Terminal not found: {session_id}")

    event_repo.log("terminal.destroyed", project=project_name, message=f"Terminal destroyed for {project_name}")

    # Broadcast state so all clients see the terminal is gone
    machine = get_state_machine()
    await machine._broadcast_state()

    return {"success": True}


@app.post("/terminals/{session_id}/resize")
async def resize_terminal(session_id: str, cols: int, rows: int):
    """Resize a terminal."""
    from .terminal import get_terminal_manager
    
    tm = get_terminal_manager()
    success = tm.resize(session_id, cols, rows)
    
    if not success:
        raise HTTPException(status_code=404, detail=f"Terminal not found: {session_id}")
    
    return {"success": True, "cols": cols, "rows": rows}


@app.post("/terminals/{session_id}/restart")
async def restart_terminal(session_id: str, mode: str | None = None):
    """Restart a dead or stuck terminal session."""
    from .terminal import get_terminal_manager

    tm = get_terminal_manager()
    session = tm.restart(session_id, mode=mode)

    if not session:
        raise HTTPException(status_code=404, detail=f"Terminal not found: {session_id}")

    event_repo.log("terminal.restarted", project=session.project, message=f"Terminal restarted for {session.project}")

    # Broadcast state so all clients see the restarted terminal
    machine = get_state_machine()
    await machine._broadcast_state()

    return {
        "id": session.id,
        "project": session.project,
        "command": session.command,
        "status": session.status.value,
        "pid": session.pid,
        "ws_url": f"/terminals/{session.id}/ws",
    }


@app.post("/terminals/{session_id}/input")
async def terminal_input(session_id: str, request: Request):
    """Write text to a terminal's PTY. Used by the dictation buffer to insert text."""
    from .terminal import get_terminal_manager

    data = await request.json()
    text = data.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="No text provided")

    tm = get_terminal_manager()
    success = tm.write(session_id, text.encode("utf-8"))

    if not success:
        raise HTTPException(status_code=404, detail=f"Terminal not found: {session_id}")

    return {"success": True, "bytes_sent": len(text)}


@app.websocket("/terminals/{session_id}/ws")
async def terminal_websocket(websocket: WebSocket, session_id: str):
    """WebSocket for terminal I/O with seamless reconnect support.

    On connect: replays buffered output so client sees what happened while
    disconnected, then streams live output. No restart required — just reconnect.
    """
    from .terminal import get_terminal_manager, TerminalStatus

    tm = get_terminal_manager()
    session = tm.get(session_id)

    if not session:
        await websocket.close(code=4004, reason="Terminal not found")
        return

    # Only treat as stopped if we already saw EOF/EIO (never assume dead from is_alive)
    is_stopped = session.status != TerminalStatus.RUNNING

    await websocket.accept()

    import json as _json
    import secrets as _secrets

    # Each WebSocket connection gets a unique client_id for multi-client resize tracking
    client_id = _secrets.token_hex(8)

    # Collect early handshake messages (skip_replay + resize) with a short timeout.
    # The client may send up to 2 messages before it expects replay data.
    skip_replay = False
    client_cols, client_rows = 0, 0

    for _ in range(2):
        try:
            msg = await asyncio.wait_for(websocket.receive(), timeout=1.0)
            if msg.get("type") == "websocket.disconnect":
                tm.unregister_client(session_id, client_id)
                return
            if "text" in msg:
                try:
                    cmd = _json.loads(msg["text"])
                    if isinstance(cmd, dict):
                        if cmd.get("type") == "skip_replay":
                            skip_replay = True
                        elif cmd.get("type") == "resize":
                            client_cols = cmd.get("cols", 80)
                            client_rows = cmd.get("rows", 24)
                            tm.resize(session_id, client_cols, client_rows, client_id=client_id)
                            break  # resize is always last in handshake
                except (ValueError, _json.JSONDecodeError):
                    pass
        except asyncio.TimeoutError:
            break

    # Replay: client snapshot > server snapshot > raw buffer
    # Register output callback BEFORE replay so no data is lost
    # between get_buffer() and callback registration.
    output_queue: asyncio.Queue[bytes] = asyncio.Queue()

    def on_output(data: bytes):
        try:
            output_queue.put_nowait(data)
        except asyncio.QueueFull:
            pass

    tm.on_output(session_id, on_output)

    # Start/re-attach the reader (no-op if already running)
    tm.start_reader(session_id)

    if not skip_replay:
        snapshot_data = None
        if client_cols > 0 and client_rows > 0:
            snapshot_data = tm.get_snapshot(session_id, client_cols, client_rows)

        if snapshot_data:
            await websocket.send_text(snapshot_data)
        else:
            buffered = tm.get_buffer(session_id)
            if buffered:
                await websocket.send_bytes(buffered)

    # If terminal is stopped, send a marker and close
    if is_stopped:
        tm.remove_callback(session_id, on_output)
        await websocket.close(code=4005, reason="Terminal not running")
        return

    try:
        # Sender task
        async def send_output():
            import logging
            logger = logging.getLogger(__name__)
            while not _shutdown_event.is_set():
                try:
                    data = await asyncio.wait_for(output_queue.get(), timeout=0.1)
                    logger.debug(f"Sending {len(data)} bytes to terminal WS")
                    await websocket.send_bytes(data)
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.error(f"Send error: {e}")
                    break

        sender_task = asyncio.create_task(send_output())

        # Receiver loop
        try:
            while not _shutdown_event.is_set():
                message = await websocket.receive()

                if message["type"] == "websocket.disconnect":
                    break

                if "bytes" in message:
                    # Binary data - send directly to terminal.
                    # Resize PTY to this client's dimensions since they're actively typing.
                    tm.resize_for_active_client(session_id, client_id)
                    tm.write(session_id, message["bytes"])
                elif "text" in message:
                    # Text - could be JSON command or plain text
                    text = message["text"]
                    try:
                        import json
                        cmd = json.loads(text)
                        # Only handle if it's a dict with a type field
                        if isinstance(cmd, dict) and "type" in cmd:
                            if cmd["type"] == "resize":
                                tm.resize(session_id, cmd.get("cols", 80), cmd.get("rows", 24), client_id=client_id)
                            elif cmd["type"] == "input":
                                tm.resize_for_active_client(session_id, client_id)
                                tm.write(session_id, cmd.get("data", "").encode())
                            elif cmd["type"] == "snapshot":
                                # Client sends serialized screen state periodically.
                                # Store it so other clients (or reconnects at different
                                # dimensions) can use it instead of raw buffer replay.
                                tm.store_snapshot(
                                    session_id,
                                    cols=cmd.get("cols", 0),
                                    rows=cmd.get("rows", 0),
                                    data=cmd.get("data", ""),
                                )
                            elif cmd["type"] == "skip_replay":
                                pass  # Already handled above
                        else:
                            # Not a command, treat as plain text
                            tm.resize_for_active_client(session_id, client_id)
                            tm.write(session_id, text.encode())
                    except json.JSONDecodeError:
                        # Plain text input
                        tm.resize_for_active_client(session_id, client_id)
                        tm.write(session_id, text.encode())
        finally:
            sender_task.cancel()

    finally:
        # Remove this client's callback and dimensions — reader keeps buffering for next reconnect
        tm.remove_callback(session_id, on_output)
        tm.unregister_client(session_id, client_id)


# =============================================================================
# Agent Session History
# =============================================================================

@app.get("/projects/{project}/agent-sessions")
async def list_agent_sessions(project: str, limit: int = 20):
    """List captured agent session IDs for a project (most recent first)."""
    from .db.repositories import get_agent_session_repo, resolve_project_id

    project_id = resolve_project_id(project)
    if not project_id:
        raise HTTPException(status_code=404, detail=f"Project not found: {project}")

    repo = get_agent_session_repo()
    sessions = repo.list_by_project(project_id, limit=limit)
    return [
        {
            "id": s.id,
            "agent_session_id": s.agent_session_id,
            "created_at": s.created_at.isoformat(),
            "label": s.label,
        }
        for s in sessions
    ]


# =============================================================================
# WebSocket for Log Streaming
# =============================================================================

@app.websocket("/ws/logs")
async def logs_websocket(websocket: WebSocket):
    """WebSocket endpoint for real-time server log streaming."""
    import aiofiles
    
    await websocket.accept()
    
    log_file = get_rdc_home() / "logs" / "server.log"

    noisy = ('/admin/logs', '/admin/status', 'telegram.org', '/ws/state', '/ws/logs')

    try:
        # Send last 100 lines first
        if log_file.exists():
            async with aiofiles.open(log_file, 'r') as f:
                content = await f.read()
                raw_lines = content.strip().split('\n')
                filtered = [l for l in raw_lines[-200:] if not any(n in l for n in noisy)]
                await websocket.send_json({
                    "type": "initial",
                    "lines": filtered[-100:],
                })
        else:
            await websocket.send_json({"type": "initial", "lines": []})

        # Now tail the file
        async with aiofiles.open(log_file, 'r') as f:
            # Seek to end
            await f.seek(0, 2)

            while not _shutdown_event.is_set():
                line = await f.readline()
                if line:
                    line = line.rstrip('\n')
                    if line and not any(n in line for n in noisy):
                        await websocket.send_json({
                            "type": "line",
                            "line": line,
                        })
                else:
                    await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logging.error(f"Log streaming error: {e}")


@app.websocket("/ws/action-logs/{process_id}")
async def process_logs_websocket(websocket: WebSocket, process_id: str):
    """WebSocket endpoint for streaming action logs in real-time."""
    import aiofiles

    # Validate token from query params
    token = websocket.query_params.get("token", "")
    if auth_manager:
        if not token or not auth_manager.validate_token(token):
            await websocket.close(code=4001, reason="Token required")
            return

    await websocket.accept()
    
    state = process_manager.get(process_id)
    if not state:
        await websocket.send_json({"error": f"Process not found: {process_id}"})
        await websocket.close(code=4004)
        return
    
    log_file = state.log_path()
    
    try:
        # Send last 100 lines first
        if log_file.exists():
            async with aiofiles.open(log_file, 'r') as f:
                content = await f.read()
                lines = content.strip().split('\n')
                # Send initial batch
                await websocket.send_json({
                    "type": "initial",
                    "process_id": process_id,
                    "lines": lines[-100:],
                })
        else:
            await websocket.send_json({
                "type": "initial",
                "process_id": process_id,
                "lines": [],
            })
        
        # Now tail the file
        if log_file.exists():
            async with aiofiles.open(log_file, 'r') as f:
                # Seek to end
                await f.seek(0, 2)
                
                while not _shutdown_event.is_set():
                    line = await f.readline()
                    if line:
                        line = line.rstrip('\n')
                        if line:
                            await websocket.send_json({
                                "type": "line",
                                "process_id": process_id,
                                "line": line,
                            })
                    else:
                        # No new content, wait a bit
                        await asyncio.sleep(0.1)
        else:
            # Log file doesn't exist yet, wait for it
            while not log_file.exists() and not _shutdown_event.is_set():
                await asyncio.sleep(0.5)
            # File appeared, restart streaming
            await process_logs_websocket(websocket, process_id)
            
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logging.error(f"Process log streaming error for {process_id}: {e}")
        try:
            await websocket.send_json({"type": "error", "error": str(e)})
        except:
            pass


@app.websocket("/ws/task-logs/{task_id}")
async def task_logs_websocket(websocket: WebSocket, task_id: str):
    """WebSocket endpoint for streaming task agent logs in real-time.

    Handles both subprocess tasks (tail log file) and in-process tasks
    (gwd/web providers — subscribe to step events via stream manager).
    """
    import aiofiles

    # Validate token from query params
    token = websocket.query_params.get("token", "")
    if auth_manager:
        if not token or not auth_manager.validate_token(token):
            await websocket.close(code=4001, reason="Token required")
            return

    await websocket.accept()

    task = task_repo.get(task_id)
    if not task:
        await websocket.send_json({"error": f"Task not found: {task_id}"})
        await websocket.close(code=4004)
        return

    # Determine if this is an in-process provider (gwd/web) or subprocess
    task_metadata = {}
    if task.metadata:
        task_metadata = json.loads(task.metadata) if isinstance(task.metadata, str) else task.metadata
    task_provider = task_metadata.get("provider", "")

    # If no log path and task uses in-process provider, stream step events
    if not task.agent_log_path and task_provider in ("gwd", "web", ""):
        await _stream_task_steps(websocket, task_id)
        return

    log_path = task.agent_log_path
    if not log_path:
        # No log path yet — wait for it to appear (task may be queued)
        # Also check if an in-process provider picks it up (no log file)
        for _ in range(120):  # wait up to 60s
            await asyncio.sleep(0.5)
            task = task_repo.get(task_id)
            if not task:
                await websocket.close(code=4004)
                return
            if task.agent_log_path:
                log_path = task.agent_log_path
                break
            # Task started running without a log file → in-process provider
            if task.status.value in ("in_progress", "running") and not task.agent_log_path:
                await _stream_task_steps(websocket, task_id)
                return
        if not log_path:
            # Last resort: try step streaming
            await _stream_task_steps(websocket, task_id)
            return

    log_file = Path(log_path)

    try:
        # Wait for log file to appear on disk
        while not log_file.exists() and not _shutdown_event.is_set():
            await asyncio.sleep(0.5)
            # Check if task already finished without a log file
            task = task_repo.get(task_id)
            if task and task.status.value in ("completed", "failed", "cancelled"):
                await websocket.send_json({"type": "initial", "task_id": task_id, "lines": ["(task finished, no log file)"]})
                await websocket.send_json({"type": "completed", "task_id": task_id, "status": task.status.value})
                await websocket.close()
                return

        # Send initial batch (last 200 lines)
        if log_file.exists():
            async with aiofiles.open(log_file, "r") as f:
                content = await f.read()
                lines = content.strip().split("\n") if content.strip() else []
                await websocket.send_json({
                    "type": "initial",
                    "task_id": task_id,
                    "lines": lines[-200:],
                })
        else:
            await websocket.send_json({"type": "initial", "task_id": task_id, "lines": []})

        # Tail the file + poll task status
        status_check_counter = 0
        async with aiofiles.open(log_file, "r") as f:
            await f.seek(0, 2)

            while not _shutdown_event.is_set():
                line = await f.readline()
                if line:
                    line = line.rstrip("\n")
                    if line:
                        await websocket.send_json({
                            "type": "line",
                            "task_id": task_id,
                            "line": line,
                        })
                else:
                    await asyncio.sleep(0.1)
                    status_check_counter += 1
                    # Check task status every ~2s (20 * 0.1s)
                    if status_check_counter >= 20:
                        status_check_counter = 0
                        task = task_repo.get(task_id)
                        if task and task.status.value in ("completed", "failed", "cancelled"):
                            # Drain remaining lines
                            remaining = await f.read()
                            if remaining:
                                for rem_line in remaining.strip().split("\n"):
                                    if rem_line:
                                        await websocket.send_json({
                                            "type": "line",
                                            "task_id": task_id,
                                            "line": rem_line,
                                        })
                            await websocket.send_json({
                                "type": "completed",
                                "task_id": task_id,
                                "status": task.status.value,
                            })
                            return

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logging.error(f"Task log streaming error for {task_id}: {e}")
        try:
            await websocket.send_json({"type": "error", "error": str(e)})
        except:
            pass


async def _stream_task_steps(websocket: WebSocket, task_id: str):
    """Stream in-process agent steps as log lines over WebSocket.

    Subscribes to the stream manager's step events and formats
    AgentStep dicts as human-readable log lines, matching the
    {type: "line", line: ...} protocol the client expects.
    """
    from .streaming import get_stream_manager

    stream_manager = get_stream_manager()
    line_queue: asyncio.Queue[str] = asyncio.Queue()

    def _format_step(step: dict) -> list[str]:
        """Format an AgentStep dict into readable log lines."""
        lines = []
        step_type = step.get("type", "")
        content = step.get("content", "")

        if step_type == "text":
            lines.append(content)
        elif step_type == "tool_call":
            tool_name = step.get("tool_name", "?")
            tool_args = step.get("tool_args", {})
            # Show tool name + brief args
            brief_args = {}
            for k, v in tool_args.items():
                if isinstance(v, str) and len(v) > 80:
                    brief_args[k] = v[:77] + "..."
                else:
                    brief_args[k] = v
            lines.append(f"[tool] {tool_name} {brief_args}")
        elif step_type == "tool_result":
            tool_name = step.get("tool_name", "")
            result = step.get("result", "")
            is_error = step.get("is_error", False)
            prefix = "[error]" if is_error else "[result]"
            # Truncate long results
            if len(result) > 500:
                result = result[:250] + "\n  ... (truncated) ...\n  " + result[-250:]
            if tool_name:
                lines.append(f"{prefix} {tool_name}:")
            for rline in result.split("\n")[:20]:
                lines.append(f"  {rline}")
            if result.count("\n") > 20:
                lines.append(f"  ... ({result.count(chr(10))} lines total)")
        elif step_type == "status":
            lines.append(f"--- {content} ---")
        elif step_type == "error":
            lines.append(f"[ERROR] {content}")
        elif step_type == "thinking":
            lines.append(f"[thinking] {content[:200]}")

        return lines

    async def on_step(step_data: dict):
        for line in _format_step(step_data):
            await line_queue.put(line)

    await stream_manager.subscribe_task(task_id, on_step)

    try:
        await websocket.send_json({"type": "initial", "task_id": task_id, "lines": ["Watching task..."]})

        while not _shutdown_event.is_set():
            try:
                line = await asyncio.wait_for(line_queue.get(), timeout=2.0)
                await websocket.send_json({
                    "type": "line",
                    "task_id": task_id,
                    "line": line,
                })
            except asyncio.TimeoutError:
                # Check if task finished
                task = task_repo.get(task_id)
                if task and task.status.value in ("completed", "failed", "cancelled"):
                    # Drain remaining queued lines
                    while not line_queue.empty():
                        line = line_queue.get_nowait()
                        await websocket.send_json({
                            "type": "line",
                            "task_id": task_id,
                            "line": line,
                        })
                    await websocket.send_json({
                        "type": "completed",
                        "task_id": task_id,
                        "status": task.status.value,
                    })
                    return

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logging.error(f"Task step streaming error for {task_id}: {e}")
        try:
            await websocket.send_json({"type": "error", "error": str(e)})
        except:
            pass
    finally:
        await stream_manager.unsubscribe_task(task_id, on_step)


# =============================================================================
# WebSocket for Real-time Updates
# =============================================================================

@app.websocket("/ws/state")
async def state_websocket(websocket: WebSocket):
    """WebSocket endpoint for state machine sync.
    
    This is the primary channel for all UX clients (web, mobile, etc.) to:
    1. Receive state snapshots
    2. Send events to the state machine
    """
    # Validate token from query params
    token = websocket.query_params.get("token", "")
    if token:
        token_info = auth_manager.validate_token(token) if auth_manager else None
        if not token_info:
            await websocket.close(code=4001, reason="Invalid token")
            return
    else:
        await websocket.close(code=4001, reason="Token required")
        return
    
    await websocket.accept()

    session_id = str(uuid.uuid4())[:8]
    machine = get_state_machine()
    registered_client_id = None  # Track for cleanup

    # Register session
    await machine.send(MachineEvent(
        type="session_connect",
        session_id=session_id,
        data={"user_agent": websocket.headers.get("user-agent", "")},
    ))

    # Subscribe to state changes
    async def on_state_change(snapshot):
        try:
            await websocket.send_json({
                "type": "state",
                "data": snapshot.model_dump(mode="json"),
            })
        except Exception:
            pass

    # Subscribe to event broadcasts (for debug/monitoring)
    async def on_event(event_msg):
        try:
            await websocket.send_json(event_msg)
        except Exception as e:
            print(f"[StateWS] Error sending event to {session_id}: {e}")

    unsubscribe = machine.subscribe(on_state_change)
    unsubscribe_events = machine.subscribe_events(on_event)

    try:
        # Send initial state
        snapshot = machine.get_snapshot()
        await websocket.send_json({
            "type": "state",
            "session_id": session_id,
            "data": snapshot.model_dump(mode="json"),
        })

        # Handle incoming events
        while not _shutdown_event.is_set():
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
                message = json.loads(data)

                if message.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue

                # Extract client identity
                client_id = message.get("client_id", session_id)
                client_name = message.get("client_name", client_id)

                # Register client WebSocket on first message with client_id
                if client_id and not registered_client_id:
                    registered_client_id = client_id
                    machine.register_client_ws(client_id, websocket, session_id=session_id, client_name=client_name)

                # Lightweight registration message — don't forward to state machine
                if message.get("type") == "register":
                    await websocket.send_json({"type": "registered", "client_id": client_id})
                    continue

                # Include client info in event data for tracking
                event_data = message.get("data", {})
                # Also capture top-level fields (sendEvent spreads data at top level)
                for k, v in message.items():
                    if k not in ("type", "data", "client_id", "client_name") and k not in event_data:
                        event_data[k] = v
                event_data["_client_id"] = client_id
                event_data["_client_name"] = client_name

                msg_type = message.get("type", "unknown")

                # Log client_action events to DuckDB event store
                if msg_type == "client_action":
                    from .event_store import log_event as log_duckdb_event
                    try:
                        log_duckdb_event(
                            event_type=event_data.get("action", "unknown"),
                            direction="received",
                            data={k: v for k, v in event_data.items() if not k.startswith("_")},
                            client_id=registered_client_id or client_id,
                            project=event_data.get("project"),
                            source="client",
                        )
                    except Exception:
                        pass

                # Forward event to state machine
                event = MachineEvent(
                    type=msg_type,
                    session_id=session_id,
                    data=event_data,
                )

                result = await machine.send(event)
                await websocket.send_json({
                    "type": "event_result",
                    "event": message.get("type"),
                    "result": result,
                    "client_id": client_id,
                })

            except asyncio.TimeoutError:
                # Send keepalive
                await websocket.send_json({"type": "ping"})
            except WebSocketDisconnect:
                break

    except Exception as e:
        print(f"State WebSocket error: {e}")
    finally:
        if registered_client_id:
            machine.unregister_client_ws(registered_client_id)
        unsubscribe()
        unsubscribe_events()
        try:
            await asyncio.wait_for(
                machine.send(MachineEvent(
                    type="session_disconnect",
                    session_id=session_id,
                )),
                timeout=3.0,
            )
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            pass


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates."""
    from .streaming import get_stream_manager
    
    await websocket.accept()
    connected_clients.add(websocket)
    
    # Track subscriptions for cleanup
    stream_subscriptions: list[tuple[str, callable]] = []
    
    async def on_agent_output(project: str, content: str):
        """Send agent output to this client."""
        try:
            await websocket.send_json({
                "type": "agent.output",
                "project": project,
                "content": content,
            })
        except Exception:
            pass
    
    try:
        # Send current state on connect
        await websocket.send_json({
            "type": "connected",
            "data": {
                "agents": len(agent_manager.list()) if agent_manager else 0,
                "tasks": task_repo.stats() if task_repo else {},
            },
        })
        
        stream_manager = get_stream_manager()
        
        # Keep connection alive and handle incoming messages
        while not _shutdown_event.is_set():
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
                message = json.loads(data)

                # Handle client commands via WebSocket
                cmd = message.get("command")
                
                if cmd == "ping":
                    await websocket.send_json({"type": "pong"})
                
                elif cmd == "status":
                    await websocket.send_json({
                        "type": "status",
                        "data": await status(),
                    })
                
                elif cmd == "spawn":
                    project = message.get("project")
                    task = message.get("task")
                    if project:
                        try:
                            state = agent_manager.spawn(project, task=task)
                            await websocket.send_json({
                                "type": "agent.spawned",
                                "data": {"project": project, "pid": state.pid},
                            })
                        except Exception as e:
                            await websocket.send_json({
                                "type": "error",
                                "data": {"message": str(e)},
                            })
                
                elif cmd == "subscribe":
                    # Subscribe to agent output stream
                    project = message.get("project")
                    if project:
                        await stream_manager.subscribe(project, on_agent_output)
                        stream_subscriptions.append((project, on_agent_output))
                        await websocket.send_json({
                            "type": "subscribed",
                            "project": project,
                        })
                
                elif cmd == "unsubscribe":
                    # Unsubscribe from agent output stream
                    project = message.get("project")
                    if project:
                        await stream_manager.unsubscribe(project, on_agent_output)
                        stream_subscriptions = [(p, c) for p, c in stream_subscriptions if p != project]
                        await websocket.send_json({
                            "type": "unsubscribed",
                            "project": project,
                        })
                
            except asyncio.TimeoutError:
                # Send ping to keep connection alive
                await websocket.send_json({"type": "ping"})
                
    except WebSocketDisconnect:
        pass
    finally:
        # Cleanup subscriptions
        stream_manager = get_stream_manager()
        for project, callback in stream_subscriptions:
            await stream_manager.unsubscribe(project, callback)
        connected_clients.discard(websocket)


@app.websocket("/ws/agent/{task_id}")
async def agent_ws_endpoint(websocket: WebSocket, task_id: str):
    """WebSocket endpoint for streaming web-native agent steps.

    Server → Client: AgentStep dicts (type, content, tool_name, etc.)
    Client → Server: approval_response, cancel, ping
    """
    from .streaming import get_stream_manager

    await websocket.accept()
    stream_manager = get_stream_manager()

    async def on_step(step_data: dict):
        try:
            await websocket.send_json(step_data)
        except Exception:
            pass

    await stream_manager.subscribe_task(task_id, on_step)

    try:
        while not _shutdown_event.is_set():
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
                message = json.loads(data)
                msg_type = message.get("type")

                if msg_type == "ping":
                    await websocket.send_json({"type": "pong"})

                elif msg_type == "approval_response":
                    # Phase 2: relay approval to web provider
                    approval_id = message.get("approval_id", "")
                    decision = message.get("decision", "reject")
                    feedback = message.get("feedback", "")
                    # Find the provider instance for this task
                    try:
                        from .worker import _active_worker
                        if _active_worker and task_id in _active_worker._web_providers:
                            _active_worker._web_providers[task_id].resolve_approval(
                                approval_id, decision, feedback
                            )
                    except Exception:
                        pass

                elif msg_type == "cancel":
                    try:
                        from .worker import _active_worker
                        if _active_worker and task_id in _active_worker._web_providers:
                            await _active_worker._web_providers[task_id].cancel()
                    except Exception:
                        pass

            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})

    except WebSocketDisconnect:
        pass
    finally:
        await stream_manager.unsubscribe_task(task_id, on_step)


# =============================================================================
# Projects (from rdc registry)
# =============================================================================

def _ensure_project_in_db(name: str) -> str:
    """Get the UUID for a project by name. Returns '' if not found."""
    db_proj = project_repo.get(name)
    return db_proj.id if db_proj else ""


@app.get("/projects")
async def list_projects():
    """List registered projects."""
    from .db.repositories import ProjectRepository, get_collection_repo

    repo = ProjectRepository()
    db_projects = repo.list()

    # Build collection name lookup
    collection_repo = get_collection_repo()
    collections = {c.id: c.name for c in collection_repo.list()}

    return [
        {
            "name": p.name,
            "path": p.path,
            "description": p.description,
            "tags": p.tags,
            "config": p.config or {},
            "collection_id": p.collection_id or "general",
            "collection": collections.get(p.collection_id, "General"),
        }
        for p in db_projects
    ]


@app.get("/browse")
async def browse_directory(path: str = "~"):
    """List directories at the given path for a file browser."""
    from pathlib import Path as _Path

    target = _Path(path).expanduser().resolve()
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {target}")

    entries = []
    try:
        for child in sorted(target.iterdir()):
            if child.name.startswith("."):
                continue
            if child.is_dir():
                # Hint whether this looks like a project (has .git or .ai)
                is_project = (child / ".git").exists() or (child / ".ai").exists()
                entries.append({"name": child.name, "path": str(child), "is_project": is_project})
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {target}")

    parent = str(target.parent) if target != target.parent else None
    return {"current": str(target), "parent": parent, "dirs": entries}


class CreateProjectRequest(BaseModel):
    path: str
    name: str | None = None
    description: str | None = None
    tags: list[str] = []
    collection_id: str | None = None


@app.post("/projects")
async def create_project(req: CreateProjectRequest):
    """Register a new project."""
    from pathlib import Path as _Path
    from .db.models import Project
    from .db.repositories import ProjectRepository

    project_path = _Path(req.path).expanduser().resolve()
    if not project_path.exists():
        raise HTTPException(status_code=400, detail=f"Path does not exist: {project_path}")

    project_name = req.name
    description = req.description
    tag_list = req.tags

    # Auto-infer details and generate rdc.yaml if not provided
    from ..llm import analyze_existing_project
    import yaml

    if not description or not project_name:
        inferred = analyze_existing_project(project_path)
        if not project_name:
            project_name = inferred.get("name", project_path.name)
        if not description:
            description = inferred.get("description", "")
        if not tag_list:
            tag_list = [inferred.get("type")] + inferred.get("features", [])
            tag_list = [t for t in tag_list if t]

    # Always try to generate rdc.yaml if it doesn't exist (check both extensions)
    rdc_yaml = project_path / "rdc.yaml"
    rdc_yml = project_path / "rdc.yml"
    if rdc_yml.exists() and not rdc_yaml.exists():
        rdc_yaml = rdc_yml  # Use existing .yml
    if not rdc_yaml.exists():
        try:
            # Re-infer if we didn't do it above
            if 'inferred' not in locals():
                inferred = analyze_existing_project(project_path)
            
            yaml_content = {
                "name": project_name,
                "description": description,
                "type": inferred.get("type", "backend"),
                "stack": inferred.get("stack", {}),
                "features": inferred.get("features", []),
            }
            rdc_yaml.write_text(yaml.dump(yaml_content, sort_keys=False))
        except Exception:
            pass

    repo = ProjectRepository()
    if repo.get(project_name):
        raise HTTPException(status_code=409, detail=f"Project already exists: {project_name}")

    db_proj = repo.create(Project(
        name=project_name,
        path=str(project_path),
        description=description,
        tags=tag_list,
        collection_id=req.collection_id or "general",
    ))

    event_repo.log("project.created", project=project_name, message=f"Project registered: {project_name}")

    # Auto-create default channel for this project
    try:
        from .channel_manager import get_channel_manager
        get_channel_manager().ensure_project_channel(db_proj.id, project_name)
    except Exception:
        pass  # Don't fail project creation if channel creation fails

    # Create and run setup task in-process (stack detection, process discovery, profile)
    try:
        setup_task = _create_setup_task(project_name)
        asyncio.create_task(_run_project_setup(setup_task.id, project_name, str(project_path)))
    except Exception:
        pass  # Don't fail project creation if setup task creation fails

    return {
        "id": db_proj.id,
        "name": project_name,
        "path": str(project_path),
        "description": description,
        "tags": tag_list,
    }


@app.post("/projects/{project}/setup")
async def trigger_project_setup(project: str):
    """Re-run project setup (stack detection, process discovery, profile).

    Creates a task record for UI visibility and runs the setup in-process.
    """
    db_proj = project_repo.get(project)
    if not db_proj:
        raise HTTPException(status_code=404, detail=f"Project not found: {project}")

    task = _create_setup_task(project)
    # Broadcast so the task appears in the UI immediately
    await get_state_machine()._broadcast_state()
    # Run in-process (no worker needed for builtin tasks)
    asyncio.create_task(_run_project_setup(task.id, project, db_proj.path))
    return {"status": "started", "project": project, "task_id": task.id}


def _create_setup_task(project_name: str):
    """Create a task record for project setup (visible in UI)."""
    proj_id = resolve_project_id(project_name) or _ensure_project_in_db(project_name)
    task = task_repo.create(
        project_id=proj_id,
        description=f"Project setup: detect stack, discover processes, build profile",
        metadata={
            "provider": "builtin",
            "builtin_id": "project_setup",
            "recipe_name": "Project Setup",
        },
    )
    return task


async def _run_project_setup(task_id: str, project_name: str, project_path: str):
    """Run project setup in-process, updating the task record with progress."""
    from pathlib import Path
    from .process_discovery import detect_stack, discover_processes
    from .db.repositories import get_process_config_repo, get_project_repo
    from .db.models import ProcessConfig
    from .streaming import get_stream_manager

    stream_manager = get_stream_manager()
    lines: list[str] = []

    async def emit(msg: str):
        lines.append(msg)
        await stream_manager.emit_task_step(task_id, {"type": "text", "content": msg})

    # Mark as in-progress
    task_repo.db.execute("UPDATE tasks SET status = 'in_progress' WHERE id = ?", (task_id,))
    task_repo.db.commit()
    await get_state_machine()._broadcast_state()

    try:
        # Step 1: Detect stack
        await emit("Detecting project stack...")
        try:
            stack_info = await asyncio.to_thread(detect_stack, project_path)
            stack = stack_info.get("stack", [])
            await emit(f"Stack: {', '.join(stack) if stack else 'none detected'}")
            if stack_info.get("test_command"):
                await emit(f"Test command: {stack_info['test_command']}")
        except Exception as e:
            await emit(f"Stack detection failed: {e}")
            stack_info = {}

        # Step 2: Save profile to DB
        await emit("Saving project profile...")
        try:
            repo = get_project_repo()
            db_proj = repo.get(project_name)
            if db_proj:
                existing_config = db_proj.config or {}
                existing_config["profile"] = {
                    "stack": stack_info.get("stack", []),
                    "test_command": stack_info.get("test_command"),
                    "source_dir": stack_info.get("source_dir"),
                    "test_dir": stack_info.get("test_dir"),
                }
                db_proj.config = existing_config
                repo.update(db_proj)
                await emit("Profile saved")
        except Exception as e:
            await emit(f"Profile save failed: {e}")

        # Step 3: Discover processes
        await emit("Discovering processes...")
        try:
            from .ports import get_port_manager
            proj_uuid = resolve_project_id(project_name) or ""
            discovered = await asyncio.to_thread(discover_processes, project_name, project_path, True)
            if discovered:
                port_manager = get_port_manager()
                process_config_repo = get_process_config_repo()
                for proc in discovered:
                    process_config_repo.upsert(ProcessConfig(
                        id=f"{project_name}-{proc.name}",
                        project_id=proj_uuid,
                        name=proc.name,
                        command=proc.command,
                        cwd=proc.cwd,
                        port=proc.default_port,
                        description=proc.description,
                        discovered_by="setup",
                    ))
                    cwd = str(Path(project_path) / proc.cwd) if proc.cwd else project_path
                    port = None
                    cmd = proc.command
                    if proc.default_port:
                        port = port_manager.assign_port(project_name, proc.name, preferred=proc.default_port)
                        cmd = process_manager._adjust_command_port(proc.command, port)
                    process_manager.register(
                        project=project_name, name=proc.name,
                        command=cmd, cwd=cwd, port=port, force_update=True,
                    )
                await emit(f"Discovered {len(discovered)} process(es)")
                for proc in discovered:
                    await emit(f"  - {proc.name}: {proc.command}")
            else:
                await emit("No processes discovered")
        except Exception as e:
            await emit(f"Process discovery failed: {e}")

        # Mark completed
        await emit("Setup complete")
        output = "\n".join(lines)
        now = datetime.now().isoformat()
        task_repo.db.execute("""
            UPDATE tasks SET status = 'completed', completed_at = ?, output = ?, result = ?
            WHERE id = ?
        """, (now, output, "Success", task_id))
        task_repo.db.commit()

    except Exception as e:
        logger.error(f"Project setup failed for {project_name}: {e}")
        now = datetime.now().isoformat()
        task_repo.db.execute("""
            UPDATE tasks SET status = 'failed', completed_at = ?, error = ?
            WHERE id = ?
        """, (now, str(e), task_id))
        task_repo.db.commit()

    # Broadcast final state
    await get_state_machine()._broadcast_state()


class ScaffoldProjectRequest(BaseModel):
    name: str
    description: str
    path: str | None = None
    type: str | None = None
    backend: str | None = None
    frontend: str | None = None
    database: str | None = None
    deployment: str | None = None
    collection_id: str | None = None


@app.post("/projects/scaffold")
async def scaffold_new_project(req: ScaffoldProjectRequest):
    """Scaffold a new project from scratch."""
    from pathlib import Path as _Path
    from ..llm import analyze_project_description
    from ..scaffold import create_project as scaffold_project
    from .db.repositories import get_project_repo

    if get_project_repo().get(req.name):
        raise HTTPException(status_code=409, detail=f"Project already exists: {req.name}")

    if req.path:
        project_path = _Path(req.path).expanduser().resolve()
    else:
        # Use projects_dir from config, env, or default to ~/projects
        from .config import get_rdc_home, Config
        config = Config.load()
        projects_dir = getattr(config, 'projects_dir', None) or os.environ.get("RDC_PROJECTS_DIR") or str(_Path.home() / "projects")
        project_path = (_Path(projects_dir) / req.name).resolve()
        project_path.parent.mkdir(parents=True, exist_ok=True)
    
    if project_path.exists() and any(project_path.iterdir()):
        raise HTTPException(status_code=400, detail=f"Directory is not empty: {project_path}")

    # Infer config
    inferred = analyze_project_description(req.description)
    inferred["description"] = req.description
    
    if req.type: inferred["type"] = req.type
    if req.backend: inferred.setdefault("stack", {})["backend"] = req.backend
    if req.frontend: inferred.setdefault("stack", {})["frontend"] = req.frontend
    if req.database: inferred["database"] = req.database
    if req.deployment: inferred["deployment"] = req.deployment

    # Scaffold
    result = scaffold_project(
        path=project_path,
        name=req.name,
        config=inferred,
        register=False  # We'll register it below with collection_id
    )

    # Register
    create_req = CreateProjectRequest(
        name=req.name,
        path=str(project_path),
        description=req.description,
        tags=[inferred.get("type", "backend")] + inferred.get("features", []),
        collection_id=req.collection_id
    )
    return await create_project(create_req)


@app.delete("/projects/{name}")
async def delete_project(name: str):
    """Remove a project from DB without deleting files on disk."""
    from .db.repositories import ProjectRepository

    repo = ProjectRepository()
    db_proj = repo.get(name)
    if not db_proj:
        raise HTTPException(status_code=404, detail=f"Project not found: {name}")

    repo.delete(db_proj.id)
        
    # Stop processes and clear terminals
    try:
        from .processes import get_process_manager
        get_process_manager().remove_by_project(name)
    except Exception:
        pass
        
    try:
        from .terminal import get_terminal_manager
        get_terminal_manager().destroy_by_project(name)
    except Exception:
        pass
        
    event_repo.log("project.deleted", project=name, message=f"Project disconnected: {name}")


    return {"success": True, "message": f"Project {name} removed from tracking."}


@app.get("/projects/{name}")
async def get_project(name: str):
    db_proj = project_repo.get(name)
    if not db_proj:
        raise HTTPException(status_code=404, detail=f"Project not found: {name}")

    return {
        "name": db_proj.name,
        "path": db_proj.path,
        "description": db_proj.description,
        "tags": db_proj.tags,
        "config": db_proj.config or {},
        "collection_id": db_proj.collection_id or "general",
    }


@app.patch("/projects/{name}/config")
async def update_project_config(name: str, config: dict):
    """Update project configuration."""
    db_proj = project_repo.get(name)
    if not db_proj:
        raise HTTPException(status_code=404, detail=f"Project not found: {name}")

    existing_config = db_proj.config or {}
    existing_config.update(config)
    db_proj.config = existing_config
    project_repo.update(db_proj)

    event_repo.log("project.config_updated", project=name, message=f"Config updated: {list(config.keys())}")

    return {"success": True, "config": existing_config}


# =============================================================================
# Terminal presets (global, editable in frontend)
# =============================================================================

_DEFAULT_TERMINAL_PRESETS = [
    {"id": "cursor", "label": "Cursor", "command": "cursor-agent", "icon": "C", "description": "Cursor AI agent"},
    {"id": "gemini", "label": "Gemini", "command": "gemini", "icon": "G", "description": "Google Gemini CLI"},
    {"id": "claude", "label": "Claude", "command": "claude --continue", "icon": "A", "description": "Anthropic Claude Code"},
    {"id": "shell", "label": "Shell", "command": "", "icon": "$", "description": "Plain login shell"},
]


def _terminal_presets_path() -> Path:
    return get_rdc_home() / "terminal_presets.json"


@app.get("/settings/terminal-presets")
async def get_terminal_presets():
    """Return terminal starter presets (launcher list). Stored in ~/.rdc/terminal_presets.json."""
    path = _terminal_presets_path()
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return _DEFAULT_TERMINAL_PRESETS


@app.patch("/settings/terminal-presets")
async def update_terminal_presets(presets: list[dict] = Body(...)):
    """Update terminal starter presets. Each item: id, label, command, icon, description."""
    path = _terminal_presets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = []
    for p in presets:
        if not isinstance(p, dict):
            continue
        normalized.append({
            "id": str(p.get("id", "")),
            "label": str(p.get("label", "")),
            "command": str(p.get("command", "")),
            "icon": str(p.get("icon", "")) or "•",
            "description": str(p.get("description", "")),
        })
    path.write_text(json.dumps(normalized, indent=2))
    return normalized


# =============================================================================
# Collections
# =============================================================================

@app.get("/collections")
async def list_collections():
    """List all collections with project counts."""
    from .db.repositories import get_collection_repo
    collection_repo = get_collection_repo()
    collections = collection_repo.list()
    counts = collection_repo.project_counts()
    return [
        {
            "id": c.id,
            "name": c.name,
            "description": c.description,
            "sort_order": c.sort_order,
            "project_count": counts.get(c.id, 0),
        }
        for c in collections
    ]


class CreateCollectionRequest(BaseModel):
    name: str
    description: str | None = None


@app.post("/collections")
async def create_collection(req: CreateCollectionRequest):
    """Create a new collection."""
    from .db.repositories import get_collection_repo
    from .db.models import Collection
    collection_repo = get_collection_repo()
    if collection_repo.get(req.name):
        raise HTTPException(status_code=409, detail=f"Collection already exists: {req.name}")
    coll = collection_repo.create(Collection(name=req.name, description=req.description))
    return {"id": coll.id, "name": coll.name, "description": coll.description, "sort_order": coll.sort_order}


class UpdateCollectionRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    sort_order: int | None = None


@app.patch("/collections/{collection_id}")
async def update_collection(collection_id: str, req: UpdateCollectionRequest):
    """Update a collection."""
    from .db.repositories import get_collection_repo
    collection_repo = get_collection_repo()
    coll = collection_repo.get(collection_id)
    if not coll:
        raise HTTPException(status_code=404, detail="Collection not found")
    if req.name is not None:
        coll.name = req.name
    if req.description is not None:
        coll.description = req.description
    if req.sort_order is not None:
        coll.sort_order = req.sort_order
    coll = collection_repo.update(coll)
    return {"id": coll.id, "name": coll.name, "description": coll.description, "sort_order": coll.sort_order}


@app.delete("/collections/{collection_id}")
async def delete_collection(collection_id: str):
    """Delete a collection. Cannot delete 'general'. Moves orphan projects to 'general'."""
    from .db.repositories import get_collection_repo
    collection_repo = get_collection_repo()
    if collection_id == "general":
        raise HTTPException(status_code=400, detail="Cannot delete the General collection")
    if not collection_repo.get(collection_id):
        raise HTTPException(status_code=404, detail="Collection not found")
    collection_repo.delete(collection_id)
    return {"deleted": True}


class MoveProjectRequest(BaseModel):
    collection_id: str


@app.post("/projects/{name}/move")
async def move_project(name: str, req: MoveProjectRequest):
    """Move a project to a different collection."""
    from .db.repositories import get_collection_repo
    db_proj = project_repo.get(name)
    if not db_proj:
        raise HTTPException(status_code=404, detail=f"Project not found: {name}")
    collection_repo = get_collection_repo()
    if not collection_repo.get(req.collection_id):
        raise HTTPException(status_code=404, detail=f"Collection not found: {req.collection_id}")
    project_repo.move_to_collection(db_proj.id, req.collection_id)
    return {"success": True, "project": name, "collection_id": req.collection_id}


class UpdateProjectRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    path: str | None = None


@app.patch("/projects/{name}")
async def update_project_metadata(name: str, req: UpdateProjectRequest):
    """Update project metadata (name, description, path)."""
    db_proj = project_repo.get(name)
    if not db_proj:
        raise HTTPException(status_code=404, detail=f"Project not found: {name}")

    if req.name is not None and req.name != name:
        if project_repo.get(req.name):
            raise HTTPException(status_code=409, detail=f"Project name already in use: {req.name}")
        db_proj.name = req.name
    if req.description is not None:
        db_proj.description = req.description
    if req.path is not None:
        db_proj.path = str(Path(req.path).expanduser().resolve())
    project_repo.update(db_proj)

    event_repo.log("project.updated", project=db_proj.name, message=f"Project metadata updated")


    return {
        "name": db_proj.name,
        "path": db_proj.path,
        "description": db_proj.description,
    }


@app.get("/projects/{name}/actions")
async def get_project_processes(name: str):
    """Get process configs for a project."""
    from .db.repositories import get_process_config_repo

    proj_uuid = resolve_project_id(name) or _ensure_project_in_db(name)
    if not proj_uuid:
        raise HTTPException(status_code=404, detail=f"Project not found: {name}")

    configs = get_process_config_repo().list(proj_uuid)
    return [
        {
            "name": cfg.name,
            "command": cfg.command,
            "cwd": cfg.cwd,
            "port": cfg.port,
            "description": cfg.description,
            "discovered_by": cfg.discovered_by,
            "kind": cfg.kind.value if hasattr(cfg.kind, 'value') else cfg.kind,
        }
        for cfg in configs
    ]


class ProcessConfigItem(BaseModel):
    name: str
    command: str
    cwd: str | None = None
    port: int | None = None
    description: str | None = None
    discovered_by: str | None = "manual"
    kind: str = "service"


@app.put("/projects/{name}/actions")
async def save_project_processes(name: str, processes: list[ProcessConfigItem]):
    """Save manual process overrides for a project."""
    from .db.repositories import get_process_config_repo
    from .db.models import ProcessConfig
    from .processes import get_process_manager

    proj_uuid = resolve_project_id(name) or _ensure_project_in_db(name)
    if not proj_uuid:
        raise HTTPException(status_code=404, detail=f"Project not found: {name}")

    process_config_repo = get_process_config_repo()

    # Replace all process configs for this project with the submitted list
    process_config_repo.delete_by_project(proj_uuid)

    from .db.models import ActionKind as AK
    for proc in processes:
        proc_kind = AK.COMMAND if proc.kind == "command" else AK.SERVICE
        process_config_repo.upsert(ProcessConfig(
            id=f"{name}-{proc.name}",
            project_id=proj_uuid,
            name=proc.name,
            command=proc.command,
            cwd=proc.cwd,
            port=proc.port,
            description=proc.description,
            discovered_by=proc.discovered_by or "manual",
            kind=proc_kind,
        ))

    # Sync ProcessManager: remove old entries for this project, then re-register kept ones
    pm = get_process_manager()
    from .ports import get_port_manager
    port_manager = get_port_manager()

    kept_names = {proc.name for proc in processes}
    for existing in list(pm.list(project=name)):
        if existing.name not in kept_names:
            # Stop if running
            if existing.status.value == "running":
                try:
                    pm.stop(existing.id)
                except Exception:
                    pass
            # Remove from in-memory cache
            pm._processes.pop(existing.id, None)

    db_proj = project_repo.get(name)
    project_path = db_proj.path if db_proj else ""

    for proc in processes:
        cwd = str(Path(project_path) / proc.cwd) if proc.cwd else project_path
        port = None
        if proc.port:
            port = port_manager.assign_port(name, proc.name, preferred=proc.port)
        from .db.models import ActionKind as AK
        proc_kind = AK.COMMAND if proc.kind == "command" else AK.SERVICE
        state = pm.register(
            project=name,
            name=proc.name,
            command=proc.command,
            cwd=cwd,
            port=port,
            kind=proc_kind,
            force_update=True,
        )
        state.description = proc.description
        process_manager._repo.upsert(state)

    event_repo.log("project.processes_updated", project=name, message=f"Saved {len(processes)} manual processes")

    return {"success": True, "count": len(processes)}


# =============================================================================
# Activity Log (SQLite event_repo — persistent across restarts)
# =============================================================================

@app.get("/activity")
async def get_activity(limit: int = 30, project: str | None = None, before: str | None = None):
    """Get recent activity from the persistent event log.

    Use `before` (ISO timestamp) for pagination — returns events older than that timestamp.
    """
    from .db.repositories import resolve_project_id

    project_id = resolve_project_id(project) if project else None
    before_dt = datetime.fromisoformat(before) if before else None
    events = event_repo.query(project_id=project_id, before=before_dt, limit=limit)

    return [
        {
            "type": e.type,
            "timestamp": e.timestamp.isoformat(),
            "project": e.project or "",
            "message": e.message or e.type,
            "level": e.level.value if hasattr(e.level, 'value') else str(e.level),
        }
        for e in events
    ]


# =============================================================================
# Events History
# =============================================================================

@app.get("/events")
async def list_events(limit: int = 50, event_type: str | None = None):
    """Get recent events."""
    type_filter = EventType(event_type) if event_type else None
    events = event_bus.get_history(limit=limit, event_type=type_filter)
    
    return [
        {
            "type": e.type.value,
            "timestamp": e.timestamp.isoformat(),
            "project": e.project,
            "data": e.data,
        }
        for e in events
    ]


# Token management endpoints

class CreateTokenRequest(BaseModel):
    name: str
    role: str = "operator"
    expires_in_days: int | None = None


@app.get("/tokens")
async def list_tokens(request: Request):
    """List all API tokens (admin only)."""
    tokens = auth_manager.list_tokens()
    return [
        {
            "id": t.id,
            "name": t.name,
            "role": t.role.value,
            "created_at": t.created_at.isoformat(),
            "expires_at": t.expires_at.isoformat() if t.expires_at else None,
            "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
            "revoked": t.revoked,
        }
        for t in tokens
    ]


@app.post("/tokens")
async def create_token(req: CreateTokenRequest, request: Request):
    """Create a new API token (admin only)."""
    token_info = getattr(request.state, "token_info", None)
    
    try:
        role = Role(req.role)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid role: {req.role}")
    
    plain_token, info = auth_manager.create_token(
        name=req.name,
        role=role,
        expires_in_days=req.expires_in_days,
        created_by=token_info.id if token_info else None,
    )
    
    audit(
        AuditAction.AUTH_TOKEN_CREATED,
        actor_type="user" if token_info else "system",
        actor_id=token_info.id if token_info else None,
        resource_type="token",
        resource_id=info.id,
        channel="api",
        metadata={"name": info.name, "role": info.role.value},
    )
    
    return {
        "token": plain_token,
        "id": info.id,
        "name": info.name,
        "role": info.role.value,
        "expires_at": info.expires_at.isoformat() if info.expires_at else None,
    }


@app.delete("/tokens/{token_id}")
async def revoke_token(token_id: str, request: Request):
    """Revoke an API token (admin only)."""
    token_info = getattr(request.state, "token_info", None)
    
    success = auth_manager.revoke_token(token_id)
    if not success:
        raise HTTPException(status_code=404, detail="Token not found")
    
    audit(
        AuditAction.AUTH_TOKEN_REVOKED,
        actor_type="user" if token_info else "system",
        actor_id=token_info.id if token_info else None,
        resource_type="token",
        resource_id=token_id,
        channel="api",
    )
    
    return {"success": True}


# Orchestrator endpoints

@app.get("/orchestrator/status")
async def orchestrator_status():
    """Get orchestrator status and stats."""
    if not orchestrator:
        return {"running": False, "error": "Orchestrator not initialized"}
    return orchestrator.get_stats()


@app.post("/orchestrator/start")
async def orchestrator_start():
    """Start the orchestrator."""
    if not orchestrator:
        raise HTTPException(status_code=500, detail="Orchestrator not initialized")
    
    await orchestrator.start()
    return {"success": True, "message": "Orchestrator started"}


@app.post("/orchestrator/stop")
async def orchestrator_stop():
    """Stop the orchestrator."""
    if not orchestrator:
        raise HTTPException(status_code=500, detail="Orchestrator not initialized")
    
    await orchestrator.stop()
    return {"success": True, "message": "Orchestrator stopped"}


# Audit log endpoint

@app.get("/audit")
async def get_audit_logs(
    request: Request,
    action: str | None = None,
    since: str | None = None,
    limit: int = 100,
):
    """Get audit logs (admin only)."""
    from datetime import datetime
    
    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format")
    
    logger = get_audit_logger()
    entries = logger.query(action=action, since=since_dt, limit=limit)
    
    return [
        {
            "id": e.id,
            "timestamp": e.timestamp.isoformat(),
            "actor_type": e.actor_type,
            "actor_id": e.actor_id,
            "action": e.action,
            "resource_type": e.resource_type,
            "resource_id": e.resource_id,
            "status": e.status,
            "error": e.error,
            "metadata": e.metadata,
        }
        for e in entries
    ]


# =============================================================================
# Admin / System Endpoints
# =============================================================================

@app.get("/admin/logs")
async def get_server_logs(lines: int = 200):
    """Get RDC server logs."""
    import subprocess
    from pathlib import Path
    
    log_file = get_rdc_home() / "logs" / "server.log"

    if not log_file.exists():
        return {"logs": "No log file found"}
    
    try:
        # Use tail for efficiency
        result = subprocess.run(
            ["tail", "-n", str(lines), str(log_file)],
            capture_output=True,
            text=True,
            timeout=5
        )
        return {"logs": result.stdout}
    except Exception as e:
        return {"logs": f"Error reading logs: {e}"}


@app.get("/admin/status")
async def get_server_status():
    """Get RDC server status and stats."""
    import os
    import time
    import psutil
    
    process = psutil.Process(os.getpid())
    
    return {
        "pid": os.getpid(),
        "uptime_seconds": int(time.time() - process.create_time()),
        "memory_mb": round(process.memory_info().rss / 1024 / 1024, 1),
        "cpu_percent": process.cpu_percent(),
        "agents_active": len([a for a in agent_manager.list() if a.status == "running"]),
        "processes_running": len([p for p in process_manager.list() if p.status.value == "running"]),
        "tasks_pending": len([t for t in task_repo.list() if t.status.value == "pending"]),
        "tasks_in_progress": len([t for t in task_repo.list() if t.status.value == "in_progress"]),
    }


@app.post("/admin/restart")
async def restart_server():
    """Trigger a graceful server reload.

    Rebuilds the React frontend (if sources exist), sets the shutdown event
    so WebSocket handlers exit their loops promptly, then touches a file in
    ~/.rdc/reload-trigger/ which uvicorn's file-watcher picks up to restart
    the worker.
    """
    event_repo.log("server.restart", message="Server reload triggered via dashboard")

    # Rebuild frontend if the source directory exists
    frontend_dir = Path(__file__).resolve().parent.parent.parent.parent / "frontend"
    if (frontend_dir / "package.json").exists():
        import subprocess
        try:
            subprocess.run(
                ["npm", "run", "build"],
                cwd=str(frontend_dir),
                capture_output=True,
                timeout=60,
            )
        except Exception:
            pass  # non-fatal — server still restarts with stale assets

    # Signal all WS handlers to exit their loops
    _shutdown_event.set()

    trigger_dir = get_rdc_home() / "reload-trigger"
    trigger_dir.mkdir(parents=True, exist_ok=True)
    # File must end in .py — uvicorn's watchfiles filter only watches *.py
    (trigger_dir / "restart.py").write_text(f"# {datetime.now().isoformat()}")

    return {"success": True, "message": "Server reloading..."}


# =============================================================================
# Caddy proxy management
# =============================================================================

@app.get("/caddy/status")
async def caddy_status():
    """Get Caddy proxy status and active routes."""
    from .caddy import get_caddy_manager
    cm = get_caddy_manager()
    if cm is None:
        return {"enabled": False, "running": False, "routes": []}
    return {
        "enabled": True,
        "running": cm._process is not None and cm._process.poll() is None,
        "available": cm.available,
        "base_domain": cm._config.base_domain,
        "listen_port": cm._config.listen_port,
        "routes": cm.list_routes(),
    }


@app.get("/config/caddy")
async def get_caddy_config():
    """Get Caddy configuration from config.yml."""
    from .config import Config, CaddyConfig
    cfg = Config.load()
    return {"config": cfg.caddy.model_dump()}


@app.patch("/config/caddy")
async def update_caddy_config(request: Request):
    """Update Caddy configuration in config.yml."""
    from .config import Config, CaddyConfig
    body = await request.json()
    cfg = Config.load()
    updated = cfg.caddy.model_copy(update=body)
    cfg.caddy = updated
    cfg.save()
    return {"success": True, "config": updated.model_dump()}


@app.post("/caddy/restart")
async def caddy_restart():
    """Restart Caddy (or start it for the first time) and re-add all process routes."""
    global caddy_manager
    from .config import Config
    from .caddy import CaddyManager, get_caddy_manager, set_caddy_manager, sanitize_subdomain

    cm = get_caddy_manager()

    # If Caddy wasn't initialized at startup, create it now from current config
    if cm is None:
        cfg = Config.load()
        if not cfg.caddy.enabled:
            raise HTTPException(400, "Caddy not enabled in config")
        cm = CaddyManager(cfg.caddy)
        set_caddy_manager(cm)
        caddy_manager = cm

    await cm.stop()
    if not await cm.start():
        raise HTTPException(500, "Caddy failed to start")

    # Re-add routes for running processes
    from .processes import get_process_manager
    pm = get_process_manager()
    for p in pm.list():
        if p.status.value == "running" and p.port:
            sub = sanitize_subdomain(p.project, p.name)
            await cm.add_route(p.id, sub, p.port)

    return {"success": True, "routes": cm.list_routes()}


# =============================================================================
# Channels (v2)
# =============================================================================

@app.get("/channels")
async def list_channels(include_archived: bool = False):
    """List all channels."""
    from .channel_manager import get_channel_manager
    from .db.repositories import ProjectRepository
    cm = get_channel_manager()
    channels = cm.list_channels(include_archived=include_archived)

    # Resolve project IDs to names + collection_ids for frontend filtering
    repo = ProjectRepository()
    project_cache: dict = {}
    def _resolve(pid: str):
        if pid not in project_cache:
            p = repo.get_by_id(pid) if hasattr(repo, 'get_by_id') else repo.get(pid)
            project_cache[pid] = p
        return project_cache[pid]

    result = []
    for ch in channels:
        project_names = []
        collection_ids = set()
        for pid in ch.project_ids:
            p = _resolve(pid)
            if p:
                project_names.append(p.name)
                if p.collection_id:
                    collection_ids.add(p.collection_id)
        result.append({
            "id": ch.id,
            "name": ch.name,
            "type": ch.type.value,
            "parent_channel_id": ch.parent_channel_id,
            "collection_id": ch.collection_id,
            "project_ids": ch.project_ids,
            "project_names": project_names,
            "collection_ids": list(collection_ids | {ch.collection_id}),
            "auto_mode": ch.auto_mode,
            "token_spent": ch.token_spent,
            "token_budget": ch.token_budget,
            "created_at": ch.created_at.isoformat(),
            "archived_at": ch.archived_at.isoformat() if ch.archived_at else None,
        })
    return result


@app.post("/channels")
async def create_channel(request: Request):
    """Create a new channel."""
    from .channel_manager import get_channel_manager
    from .db.models import ChannelType

    body = await request.json()
    name = body.get("name", "")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if not name.startswith("#"):
        name = f"#{name}"

    # Resolve project names to IDs
    from .db.repositories import ProjectRepository
    raw_ids = body.get("project_ids", [])
    project_ids = []
    repo = ProjectRepository()
    for name_or_id in raw_ids:
        proj = repo.get(name_or_id)  # accepts name or UUID
        if proj:
            project_ids.append(proj.id)

    cm = get_channel_manager()
    ch = cm.create_channel(
        name=name,
        type=ChannelType(body.get("type", "ephemeral")),
        project_ids=project_ids,
        parent_channel_id=body.get("parent_channel_id"),
        collection_id=body.get("collection_id", "general"),
    )

    # Resolve project names and collection IDs for the response
    project_names = []
    collection_ids = set()
    for pid in project_ids:
        proj = repo.get(pid)
        if proj:
            project_names.append(proj.name)
            if proj.collection_id:
                collection_ids.add(proj.collection_id)

    return {
        "id": ch.id,
        "name": ch.name,
        "type": ch.type.value,
        "parent_channel_id": ch.parent_channel_id,
        "project_ids": ch.project_ids,
        "project_names": project_names,
        "collection_ids": list(collection_ids),
        "auto_mode": ch.auto_mode,
        "token_spent": 0,
        "token_budget": None,
        "created_at": ch.created_at.isoformat(),
        "archived_at": None,
    }


@app.get("/channels/{channel_id}")
async def get_channel(channel_id: str):
    """Get a channel by ID."""
    from .channel_manager import get_channel_manager
    cm = get_channel_manager()
    ch = cm.get_channel(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Channel not found")
    return {
        "id": ch.id,
        "name": ch.name,
        "type": ch.type.value,
        "parent_channel_id": ch.parent_channel_id,
        "project_ids": ch.project_ids,
        "auto_mode": ch.auto_mode,
        "token_spent": ch.token_spent,
        "token_budget": ch.token_budget,
        "created_at": ch.created_at.isoformat(),
        "archived_at": ch.archived_at.isoformat() if ch.archived_at else None,
    }


@app.get("/channels/{channel_id}/context")
async def get_channel_context(channel_id: str, project: str | None = None):
    """Get assembled workstream context for a channel. Useful for debugging and UI display."""
    from .workstream_context import assemble_workstream_context, ContextBudget
    ctx = assemble_workstream_context(channel_id=channel_id, project=project, budget=ContextBudget())
    return {
        "channel_id": ctx.channel_id,
        "channel_name": ctx.channel_name,
        "project": ctx.project,
        "total_tokens": ctx.total_tokens,
        "truncated": ctx.truncated,
        "layers": [
            {
                "name": l.name,
                "priority": l.priority,
                "token_budget": l.token_budget,
                "estimated_tokens": l.estimated_tokens,
                "content": l.content,
            }
            for l in ctx.layers
        ],
        "prompt": ctx.to_prompt(),
    }


@app.get("/channels/{channel_id}/sessions")
async def list_channel_sessions(channel_id: str):
    """List sessions for a channel."""
    from .session_manager import get_session_manager
    sm = get_session_manager()
    sessions = sm.list_for_channel(channel_id)
    return [
        {
            "id": s.id, "channel_id": s.channel_id, "project": s.project,
            "terminal_ids": s.terminal_ids, "task_id": s.task_id,
            "description": s.description, "status": s.status,
            "agent_provider": s.agent_provider,
            "created_at": s.created_at.isoformat(),
            "updated_at": s.updated_at.isoformat(),
            "completed_at": s.completed_at.isoformat() if s.completed_at else None,
            "output_summary": s.output_summary,
        }
        for s in sessions
    ]


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Get a session by ID."""
    from .session_manager import get_session_manager
    sm = get_session_manager()
    s = sm.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "id": s.id, "channel_id": s.channel_id, "project": s.project,
        "terminal_ids": s.terminal_ids, "task_id": s.task_id,
        "description": s.description, "status": s.status,
        "agent_provider": s.agent_provider,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
        "completed_at": s.completed_at.isoformat() if s.completed_at else None,
        "output_summary": s.output_summary,
    }


@app.get("/sessions/{session_id}/log")
async def get_session_log(session_id: str, tail: int = 200):
    """Get the session's terminal output log."""
    from .config import get_rdc_home
    log_file = get_rdc_home() / "sessions" / f"{session_id}.log"
    if not log_file.exists():
        return {"log": None, "message": "No log file found for this session."}
    try:
        content = log_file.read_text(errors="replace")
        # Strip ANSI escape codes + control chars for readability
        from .utils import strip_ansi
        content = strip_ansi(content)
        # Also strip remaining control characters (cursor movement, etc.)
        import re
        content = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", content)
        # Collapse excessive blank lines
        content = re.sub(r"\n{3,}", "\n\n", content)
        # Return tail lines
        lines = content.split("\n")
        if len(lines) > tail:
            lines = lines[-tail:]
        return {"log": "\n".join(lines), "lines": len(lines), "total_lines": len(content.split("\n"))}
    except Exception as e:
        return {"log": None, "error": str(e)}


@app.get("/sessions/{session_id}/events")
async def get_session_events(session_id: str, limit: int = 100):
    """Get the unified event timeline for a session."""
    from .session_manager import get_session_manager
    sm = get_session_manager()
    events = sm.get_events(session_id, limit=limit)
    return events


@app.post("/sessions/{session_id}/complete")
async def complete_session(session_id: str):
    """Manually mark a session as done — kills terminals and generates summary."""
    from .session_manager import get_session_manager, SessionStatus
    from .terminal import get_terminal_manager
    sm = get_session_manager()
    tm = get_terminal_manager()
    s = sm.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    # Save terminal log before killing
    sm._save_terminal_log(s)
    # Kill associated terminals
    for tid in s.terminal_ids:
        try:
            tm.kill(tid)
        except Exception:
            pass
    summary = await sm._generate_summary(s)
    sm.update_status(session_id, SessionStatus.DONE, output_summary=summary)
    sm.stop_monitor(session_id)
    # Post completion to channel
    try:
        from .utils import get_channel_manager
        cm = get_channel_manager()
        components = [{"type": "task_card", "title": s.description[:80], "status": "done", "project": s.project}]
        if summary:
            components.append({"type": "text", "content": summary})
        cm.post_message(s.channel_id, role="system", content="Session completed.",
            metadata={"type": "a2ui", "components": components})
    except Exception:
        pass
    return {"success": True, "session_id": session_id}


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session and its events."""
    from .session_manager import get_session_manager
    sm = get_session_manager()
    s = sm.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    sm.stop_monitor(session_id)
    db = sm._db()
    db.execute("DELETE FROM session_terminals WHERE session_id = ?", (session_id,))
    db.execute("DELETE FROM events WHERE mission_id = ?", (session_id,))
    db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    db.commit()
    sm._sessions.pop(session_id, None)
    # Remove log file
    try:
        from .utils import get_rdc_home
        log_file = get_rdc_home() / "sessions" / f"{session_id}.log"
        if log_file.exists():
            log_file.unlink()
    except Exception:
        pass
    return {"success": True}


@app.post("/sessions/{session_id}/retry")
async def retry_session(session_id: str):
    """Retry a failed/pending session — re-spawns the terminal."""
    from .session_manager import get_session_manager
    from .terminal import get_terminal_manager
    from .channel_manager import get_channel_manager

    sm = get_session_manager()
    s = sm.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    if s.status not in ("pending", "failed"):
        raise HTTPException(status_code=400, detail=f"Cannot retry session in status: {s.status}")

    tm = get_terminal_manager()
    provider = s.agent_provider or "claude"
    if provider == "shell":
        command = s.description
    else:
        command = f'claude --dangerously-skip-permissions "{s.description[:500]}"'

    try:
        term = tm.create(project=s.project, command=command)
        sm.link_terminal(s.id, term.id)

        cm = get_channel_manager()
        cm.post_message(
            s.channel_id,
            role="system",
            content=f"Session retried — agent restarted.",
            metadata={"type": "a2ui", "components": [
                {"type": "task_card", "title": s.description[:80], "status": "running", "project": s.project},
            ]},
        )
        return {"success": True, "session_id": s.id, "terminal_id": term.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/channels/{channel_id}")
async def update_channel(channel_id: str, request: Request):
    """Update a channel (rename, toggle auto-mode, set budget)."""
    from .channel_manager import get_channel_manager
    cm = get_channel_manager()
    ch = cm.get_channel(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Channel not found")

    body = await request.json()
    if "name" in body:
        cm.rename_channel(channel_id, body["name"])
    if "auto_mode" in body:
        cm.set_auto_mode(channel_id, bool(body["auto_mode"]))
    if "token_budget" in body:
        cm.db.execute(
            "UPDATE channels SET token_budget = ? WHERE id = ?",
            (body["token_budget"], channel_id),
        )
        cm.db.commit()

    return {"success": True}


@app.post("/channels/{channel_id}/archive")
async def archive_channel(channel_id: str):
    """Archive a channel."""
    from .channel_manager import get_channel_manager
    cm = get_channel_manager()
    cm.archive_channel(channel_id)
    return {"success": True}


@app.delete("/channels/{channel_id}")
async def delete_channel(channel_id: str):
    """Permanently delete a channel and its messages."""
    from .channel_manager import get_channel_manager
    cm = get_channel_manager()
    ch = cm.get_channel(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Channel not found")
    if ch.type.value == "system":
        raise HTTPException(status_code=403, detail="Cannot delete system channels")
    # Delete messages, terminal links, and the channel itself
    cm.db.execute("DELETE FROM channel_messages WHERE channel_id = ?", (channel_id,))
    cm.db.execute("DELETE FROM terminal_channels WHERE channel_id = ?", (channel_id,))
    cm.db.execute("DELETE FROM channel_projects WHERE channel_id = ?", (channel_id,))
    cm.db.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
    cm.db.commit()
    return {"success": True}


# ── Channel Projects ──

@app.post("/channels/{channel_id}/projects")
async def add_project_to_channel(channel_id: str, request: Request):
    """Link a project to a channel."""
    from .channel_manager import get_channel_manager
    from .db.repositories import ProjectRepository

    body = await request.json()
    project_name = body.get("project_name", "")
    if not project_name:
        raise HTTPException(status_code=400, detail="project_name is required")

    repo = ProjectRepository()
    proj = repo.get(project_name)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_name}")

    cm = get_channel_manager()
    cm.db.execute(
        "INSERT OR IGNORE INTO channel_projects (channel_id, project_id) VALUES (?, ?)",
        (channel_id, proj.id),
    )
    cm.db.commit()
    return {"success": True, "project_id": proj.id, "project_name": proj.name}


@app.delete("/channels/{channel_id}/projects/{project_name}")
async def remove_project_from_channel(channel_id: str, project_name: str):
    """Unlink a project from a channel."""
    from .channel_manager import get_channel_manager
    from .db.repositories import ProjectRepository

    repo = ProjectRepository()
    proj = repo.get(project_name)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_name}")

    cm = get_channel_manager()
    cm.db.execute(
        "DELETE FROM channel_projects WHERE channel_id = ? AND project_id = ?",
        (channel_id, proj.id),
    )
    cm.db.commit()
    return {"success": True}


# ── Channel Messages ──

@app.get("/channels/{channel_id}/messages")
async def list_channel_messages(channel_id: str, limit: int = 50, before: str | None = None):
    """List messages in a channel (chronological order)."""
    from .channel_manager import get_channel_manager
    cm = get_channel_manager()
    messages = cm.list_messages(channel_id, limit=limit, before=before)
    return [
        {
            "id": m.id,
            "channel_id": m.channel_id,
            "role": m.role.value,
            "content": m.content,
            "metadata": m.metadata,
            "created_at": m.created_at.isoformat(),
        }
        for m in messages
    ]


@app.post("/channels/{channel_id}/messages")
async def post_channel_message(channel_id: str, request: Request):
    """Post a message to a channel."""
    from .channel_manager import get_channel_manager
    from .db.models import ChannelMessageRole

    body = await request.json()
    content = body.get("content", "")
    if not content:
        raise HTTPException(status_code=400, detail="content is required")

    cm = get_channel_manager()
    msg = cm.post_message(
        channel_id=channel_id,
        role=ChannelMessageRole(body.get("role", "user")),
        content=content,
        metadata=body.get("metadata"),
    )
    return {
        "id": msg.id,
        "channel_id": msg.channel_id,
        "role": msg.role.value,
        "content": msg.content,
        "created_at": msg.created_at.isoformat(),
    }


# ── Channel Terminals ──

@app.post("/channels/{channel_id}/terminals")
async def link_terminal_to_channel(channel_id: str, request: Request):
    """Link a terminal to a channel."""
    from .channel_manager import get_channel_manager
    body = await request.json()
    terminal_id = body.get("terminal_id", "")
    if not terminal_id:
        raise HTTPException(status_code=400, detail="terminal_id is required")

    cm = get_channel_manager()
    cm.link_terminal(terminal_id, channel_id)
    return {"success": True}


@app.get("/channels/{channel_id}/terminals")
async def get_channel_terminals(channel_id: str):
    """Get terminal IDs linked to a channel."""
    from .channel_manager import get_channel_manager
    cm = get_channel_manager()
    return {"terminal_ids": cm.get_channel_terminals(channel_id)}


# ── Events ──

@app.get("/events/search")
async def search_events(
    q: str | None = None,
    type: str | None = None,
    channel_id: str | None = None,
    limit: int = 50,
):
    """Search the event store."""
    from .channel_manager import get_channel_manager
    cm = get_channel_manager()
    events = cm.search_events(query=q, type=type, channel_id=channel_id, limit=limit)
    return [
        {
            "id": e.id,
            "timestamp": e.timestamp.isoformat(),
            "type": e.type,
            "channel_id": e.channel_id,
            "project_id": e.project_id,
            "mission_id": e.mission_id,
            "data": e.data,
        }
        for e in events
    ]


# =============================================================================
# SPA catch-all — MUST be last so API routes take priority
# =============================================================================

@app.get("/{path:path}")
async def react_spa(path: str = ""):
    """Serve the React frontend SPA for all non-API routes."""
    # Skip API-like paths (they should have been handled above)
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return FileResponse(str(index), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return HTMLResponse("<h1>Frontend not built. Run: cd frontend && npm run build</h1>", status_code=404)


def create_app() -> FastAPI:
    """Create and return the FastAPI app."""
    return app


def run_server(host: str = "127.0.0.1", port: int = 8420):
    """Run the server."""
    import uvicorn
    uvicorn.run(app, host=host, port=port)
