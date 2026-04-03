"""Database models for RDC."""

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..config import get_rdc_home


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    AWAITING_REVIEW = "awaiting_review"  # Needs human approval

    def can_transition_to(self, target: "TaskStatus") -> bool:
        """Check if transitioning to the target status is valid."""
        return target in _VALID_TASK_TRANSITIONS.get(self, set())


# Valid state transitions for tasks
_VALID_TASK_TRANSITIONS: dict["TaskStatus", set["TaskStatus"]] = {
    TaskStatus.PENDING: {TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED, TaskStatus.AWAITING_REVIEW, TaskStatus.BLOCKED},
    TaskStatus.IN_PROGRESS: {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.BLOCKED},
    TaskStatus.BLOCKED: {TaskStatus.PENDING, TaskStatus.CANCELLED},
    TaskStatus.AWAITING_REVIEW: {TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED},
    TaskStatus.FAILED: {TaskStatus.PENDING},  # Allow retry
    TaskStatus.COMPLETED: set(),  # Terminal
    TaskStatus.CANCELLED: set(),  # Terminal
}


class TaskPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"
    
    def sort_key(self) -> int:
        return {"urgent": 0, "high": 1, "normal": 2, "low": 3}[self.value]


class Collection(BaseModel):
    """A named group of projects."""
    id: str = ""
    name: str
    description: Optional[str] = None
    sort_order: int = 0
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class Project(BaseModel):
    """A registered project."""
    id: str = ""  # UUID, primary key
    name: str
    path: str
    description: Optional[str] = None
    collection_id: str = "general"
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    config: Optional[dict[str, Any]] = None


class Task(BaseModel):
    """A task in the queue."""
    id: str
    project_id: str = ""  # UUID referencing projects.id
    project: str = ""  # Human-readable project name (from lookup, not stored in DB)
    description: str
    priority: TaskPriority = TaskPriority.NORMAL
    status: TaskStatus = TaskStatus.PENDING
    assigned_to: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[str] = None
    error: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    metadata: Optional[dict[str, Any]] = None
    
    # Task chaining
    depends_on: Optional[list[str]] = None  # Task IDs this depends on
    parent_task_id: Optional[str] = None  # ID of parent task (for follow-ups)
    output: Optional[str] = None  # Captured output from agent
    output_artifacts: Optional[list[str]] = None  # File paths created
    next_tasks: Optional[list[str]] = None  # Tasks to trigger on completion
    
    # Review mode
    requires_review: bool = False  # If true, task pauses for approval before running
    review_prompt: Optional[str] = None  # What to show the reviewer
    reviewed_by: Optional[str] = None  # Who approved/rejected
    reviewed_at: Optional[datetime] = None
    
    # Worker-related fields
    claimed_by: Optional[str] = None  # Worker ID that claimed this task
    claimed_at: Optional[datetime] = None  # When it was claimed
    agent_pid: Optional[int] = None  # PID of the agent process
    agent_log_path: Optional[str] = None  # Path to agent log file
    timeout_seconds: int = 3600  # Max runtime before worker kills the agent


class AgentRunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class AgentRun(BaseModel):
    """A single agent run (for history)."""
    id: Optional[int] = None
    project_id: str = ""  # UUID referencing projects.id
    project: str = ""  # Human-readable project name (from lookup)
    provider: Optional[str] = None
    task: Optional[str] = None
    task_id: Optional[str] = None
    pid: Optional[int] = None
    started_at: datetime = Field(default_factory=datetime.now)
    ended_at: Optional[datetime] = None
    exit_code: Optional[int] = None
    status: AgentRunStatus = AgentRunStatus.RUNNING
    error: Optional[str] = None
    log_file: Optional[str] = None


class WorkerStatus(str, Enum):
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    DEAD = "dead"


class Worker(BaseModel):
    """A task worker process."""
    id: str
    hostname: str
    pid: int
    started_at: datetime = Field(default_factory=datetime.now)
    last_heartbeat: datetime = Field(default_factory=datetime.now)
    status: WorkerStatus = WorkerStatus.RUNNING
    max_concurrent: int = 3
    current_load: int = 0


class BrowserStatus(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


class BrowserSession(BaseModel):
    """A browser session backed by a browserless container."""
    id: str
    process_id: Optional[str] = None
    project_id: Optional[str] = None  # UUID referencing projects.id
    target_url: str
    container_id: Optional[str] = None
    container_port: int = 0
    status: BrowserStatus = BrowserStatus.STARTING
    created_at: datetime = Field(default_factory=datetime.now)
    stopped_at: Optional[datetime] = None
    error: Optional[str] = None


class RecordingStatus(str, Enum):
    RECORDING = "recording"
    STOPPED = "stopped"


class Recording(BaseModel):
    """An rrweb DOM recording session."""
    id: str
    session_id: str
    project_id: Optional[str] = None
    status: RecordingStatus = RecordingStatus.RECORDING
    started_at: datetime = Field(default_factory=datetime.now)
    stopped_at: Optional[datetime] = None
    event_count: int = 0
    chunk_count: int = 0


class ContextSnapshot(BaseModel):
    """A captured browser context (screenshot + a11y tree + metadata)."""
    id: str
    project_id: str = ""  # UUID referencing projects.id
    project: str = ""  # Human-readable project name (from lookup)
    session_id: Optional[str] = None
    url: Optional[str] = None
    title: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)
    screenshot_path: Optional[str] = None
    a11y_path: Optional[str] = None
    meta_path: Optional[str] = None
    description: str = ""
    source: str = "manual"


# =============================================================================
# PROCESS STATE (stored in process_configs table)
# =============================================================================

class ProcessType(str, Enum):
    DEV_SERVER = "dev_server"
    DATABASE = "database"
    WORKER = "worker"
    CUSTOM = "custom"


class ActionKind(str, Enum):
    SERVICE = "service"
    COMMAND = "command"


class ProcessStatus(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"
    STARTING = "starting"
    IDLE = "idle"
    COMPLETED = "completed"


class ProcessConfig(BaseModel):
    """A managed process (config + runtime state). Stored in process_configs table."""
    id: str
    project_id: str = ""
    project: str = ""  # Resolved from project_id
    name: str
    kind: ActionKind = ActionKind.SERVICE
    process_type: ProcessType = ProcessType.DEV_SERVER
    command: str
    cwd: Optional[str] = ""
    port: Optional[int] = None
    description: Optional[str] = None
    status: ProcessStatus = ProcessStatus.IDLE
    pid: Optional[int] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    exit_code: Optional[int] = None
    error: Optional[str] = None
    discovered_at: datetime = Field(default_factory=datetime.now)
    discovered_by: str = "manual"

    def log_path(self) -> Path:
        return get_rdc_home() / "logs" / "processes" / f"{self.id}.log"


# =============================================================================
# AGENT STATE (stored in agent_registry table)
# =============================================================================

class AgentStatus(str, Enum):
    IDLE = "idle"
    SPAWNING = "spawning"
    WORKING = "working"
    WAITING = "waiting"
    TESTING = "testing"
    ERROR = "error"
    STOPPED = "stopped"


class AgentState(BaseModel):
    """Agent runtime state. Stored in agent_registry table."""
    project_id: str
    project: str = ""  # Resolved from project_id
    provider: str = "cursor"
    preferred_worktree: Optional[str] = None
    config: Optional[dict[str, Any]] = None
    status: AgentStatus = AgentStatus.IDLE
    pid: Optional[int] = None
    worktree: Optional[str] = None
    current_task: Optional[str] = None
    started_at: Optional[datetime] = None
    last_activity: Optional[datetime] = None
    error: Optional[str] = None
    retry_count: int = 0


# =============================================================================
# PORT ASSIGNMENTS (stored in port_assignments table)
# =============================================================================

class PortAssignment(BaseModel):
    """A port assigned to a project service."""
    id: Optional[int] = None
    project_id: str
    service: str
    port: int


# =============================================================================
# VNC SESSIONS (stored in vnc_sessions table)
# =============================================================================

class VNCStatus(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


class VNCSession(BaseModel):
    """A VNC/browser session for a process."""
    id: str
    process_id: str
    target_url: str
    vnc_port: int
    web_port: int
    container_id: Optional[str] = None
    status: VNCStatus = VNCStatus.STARTING
    started_at: Optional[datetime] = None
    error: Optional[str] = None


class AgentSession(BaseModel):
    """A captured cursor-agent session ID for resume support."""
    id: Optional[int] = None
    project_id: str
    agent_session_id: str
    created_at: datetime = Field(default_factory=datetime.now)
    label: Optional[str] = None


class RecipeModel(BaseModel):
    """A user-created recipe (stored in DB). Distinct from the built-in Recipe dataclass."""
    id: str = ""
    name: str
    description: Optional[str] = None
    prompt_template: str
    model: Optional[str] = None
    inputs: Optional[dict[str, str]] = None
    tags: Optional[list[str]] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class EventLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class Event(BaseModel):
    """A logged event."""
    id: Optional[int] = None
    timestamp: datetime = Field(default_factory=datetime.now)
    type: str
    project_id: Optional[str] = None  # UUID referencing projects.id
    project: Optional[str] = None  # Human-readable project name (from lookup)
    agent: Optional[str] = None
    task_id: Optional[str] = None
    level: EventLevel = EventLevel.INFO
    message: Optional[str] = None
    data: Optional[dict[str, Any]] = None


# =============================================================================
# Channels (v2)
# =============================================================================

class ChannelType(str, Enum):
    PROJECT = "project"
    MISSION = "mission"
    EPHEMERAL = "ephemeral"
    SYSTEM = "system"
    EVENT = "event"


class Channel(BaseModel):
    """A workspace: contains messages, terminals, and missions."""
    id: str
    name: str
    type: ChannelType = ChannelType.PROJECT
    parent_channel_id: Optional[str] = None
    project_ids: list[str] = Field(default_factory=list)  # from channel_projects
    auto_mode: bool = False
    token_spent: int = 0
    token_budget: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.now)
    archived_at: Optional[datetime] = None


class ChannelMessageRole(str, Enum):
    USER = "user"
    ORCHESTRATOR = "orchestrator"
    SYSTEM = "system"
    AGENT = "agent"


class ChannelMessage(BaseModel):
    """A message in a channel's queue."""
    id: str
    channel_id: str
    role: ChannelMessageRole
    content: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    synced: bool = True
    created_at: datetime = Field(default_factory=datetime.now)


class StructuredEvent(BaseModel):
    """A structured event in the event store."""
    id: str
    timestamp: datetime = Field(default_factory=datetime.now)
    type: str  # e.g. "terminal.error_occurred", "mission.step_completed"
    channel_id: Optional[str] = None
    project_id: Optional[str] = None
    mission_id: Optional[str] = None
    data: Optional[dict[str, Any]] = None
