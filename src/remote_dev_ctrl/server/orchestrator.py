"""Agent orchestration - automatically assigns tasks to agents."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from .agents import AgentManager, AgentStatus
from .db import TaskRepository, EventRepository, AgentRunRepository
from .db.models import TaskStatus, AgentRun, AgentRunStatus
from .config import Config

logger = logging.getLogger(__name__)


class Orchestrator:
    """Orchestrates agents and task assignment."""
    
    def __init__(
        self,
        config: Config,
        agent_manager: AgentManager,
        task_repo: TaskRepository,
        event_repo: EventRepository,
    ):
        self.config = config
        self.agent_manager = agent_manager
        self.task_repo = task_repo
        self.event_repo = event_repo
        self.run_repo = AgentRunRepository()
        
        self._running = False
        self._task: Optional[asyncio.Task] = None
        
        # Track task <-> agent mapping
        self._agent_tasks: dict[str, str] = {}  # project -> task_id
        
        # Config
        self.poll_interval = 5  # seconds
        self.max_concurrent = config.agents.max_concurrent
        self.stuck_timeout = config.agents.escalation.stuck_timeout
        
        # Register for agent events
        self.agent_manager.on("task_complete", self._on_agent_complete)
        
        self._loop: Optional[asyncio.AbstractEventLoop] = None
    
    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Set the main event loop for thread-safe dashboard notifications."""
        self._loop = loop
    
    def _notify_dashboard(self):
        """Notify dashboard of state changes by broadcasting via state machine.
        Safe to call from worker threads: schedules broadcast on main loop."""
        from .state_machine import get_state_machine
        sm = get_state_machine()
        if not sm:
            return
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(sm._broadcast_state_sync)
        else:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(sm._broadcast_state())
            except RuntimeError:
                pass
    
    async def start(self):
        """Start the orchestration loop."""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Orchestrator started")
    
    async def stop(self):
        """Stop the orchestration loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Orchestrator stopped")
    
    async def _run_loop(self):
        """Main orchestration loop."""
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.error(f"Orchestrator error: {e}")
            
            await asyncio.sleep(self.poll_interval)
    
    async def _tick(self):
        """Single tick of the orchestration loop."""
        # 1. Check agent health and update statuses
        await self._check_agent_health()
        
        # 2. Check for stuck agents
        await self._check_stuck_agents()
        
        # 3. Assign pending tasks to idle agents
        await self._assign_tasks()
    
    async def _check_agent_health(self):
        """Check health of all agents and update DB."""
        health = self.agent_manager.check_health()
        
        for project, is_healthy in health.items():
            if not is_healthy:
                agent = self.agent_manager.get(project)
                if agent and agent.status == AgentStatus.STOPPED:
                    # Update any in-progress tasks as failed
                    tasks = self.task_repo.list(status=TaskStatus.IN_PROGRESS)
                    for task in tasks:
                        if task.assigned_to == project:
                            self.task_repo.fail(task.id, "Agent stopped unexpectedly")
                            self.event_repo.log(
                                "task.failed",
                                project=project,
                                task_id=task.id,
                                message="Agent stopped unexpectedly",
                                level="error",
                            )
    
    async def _check_stuck_agents(self):
        """Check for agents that have been working too long."""
        agents = self.agent_manager.list()
        now = datetime.now()
        
        for agent in agents:
            if agent.status != AgentStatus.WORKING:
                continue
            
            if not agent.last_activity:
                continue
            
            elapsed = (now - agent.last_activity).total_seconds()
            if elapsed > self.stuck_timeout:
                logger.warning(f"Agent {agent.project} appears stuck (no activity for {elapsed:.0f}s)")
                
                self.event_repo.log(
                    "agent.stuck",
                    project=agent.project,
                    message=f"Agent stuck for {elapsed:.0f}s",
                    level="warn",
                )
                
                # TODO: Escalation - notify user, restart agent, etc.
    
    async def _assign_tasks(self):
        """Assign pending tasks to available agents."""
        # Get currently running agents
        agents = self.agent_manager.list()
        running = [a for a in agents if a.status in (AgentStatus.WORKING, AgentStatus.SPAWNING)]
        
        # Check if we can spawn more
        if len(running) >= self.max_concurrent:
            return
        
        # Get projects that are idle or not running
        busy_projects = {a.project for a in running}
        
        # Get pending tasks
        pending_tasks = self.task_repo.list_pending(limit=10)
        
        for task in pending_tasks:
            # Skip if project already has a running agent
            if task.project in busy_projects:
                continue
            
            # Check if we've hit max concurrent
            if len(running) >= self.max_concurrent:
                break
            
            # Spawn agent for this task
            try:
                logger.info(f"Auto-assigning task {task.id} to project {task.project}")
                
                # Claim the task
                claimed = self.task_repo.claim_next(task.project)
                if not claimed:
                    continue
                
                # Spawn agent
                state = self.agent_manager.spawn(
                    project=task.project,
                    task=task.description,
                )
                
                # Record the run
                run = AgentRun(
                    project_id=task.project_id,
                    project=task.project,
                    provider=state.provider,
                    task=task.description,
                    task_id=task.id,
                    pid=state.pid,
                    status=AgentRunStatus.RUNNING,
                )
                self.run_repo.create(run)
                
                self.event_repo.log(
                    "agent.spawned",
                    project=task.project,
                    task_id=task.id,
                    message=f"Agent spawned for task: {task.description[:50]}",
                )
                
                busy_projects.add(task.project)
                running.append(state)
                
                # Track the task for this agent
                self._agent_tasks[task.project] = task.id
                self._notify_dashboard()
                
            except Exception as e:
                logger.error(f"Failed to spawn agent for {task.project}: {e}")
                
                # Mark task as failed
                self.task_repo.fail(task.id, str(e))
                self._notify_dashboard()
                
                self.event_repo.log(
                    "agent.spawn_failed",
                    project=task.project,
                    task_id=task.id,
                    message=str(e),
                    level="error",
                )
    
    def _on_agent_complete(self, project: str, exit_code: int, output: str) -> None:
        """Handle agent task completion."""
        task_id = self._agent_tasks.pop(project, None)
        
        if not task_id:
            return
        
        if exit_code == 0:
            self.task_repo.complete(task_id, output=output)
            self.event_repo.log(
                "task.completed",
                project=project,
                task_id=task_id,
                message=f"Task completed with output: {len(output)} chars",
            )
        else:
            self.task_repo.fail(task_id, f"Agent exited with code {exit_code}")
            self.event_repo.log(
                "task.failed",
                project=project,
                task_id=task_id,
                message=f"Agent exited with code {exit_code}",
                level="error",
            )
        
        self._notify_dashboard()
    
    def get_stats(self) -> dict:
        """Get orchestrator statistics."""
        agents = self.agent_manager.list()
        task_stats = self.task_repo.stats()
        
        return {
            "running": self._running,
            "agents": {
                "total": len(agents),
                "working": len([a for a in agents if a.status == AgentStatus.WORKING]),
                "idle": len([a for a in agents if a.status == AgentStatus.IDLE]),
                "error": len([a for a in agents if a.status == AgentStatus.ERROR]),
            },
            "tasks": task_stats,
            "config": {
                "max_concurrent": self.max_concurrent,
                "poll_interval": self.poll_interval,
                "stuck_timeout": self.stuck_timeout,
            },
        }


# Global orchestrator instance
_orchestrator: Optional[Orchestrator] = None


def get_orchestrator() -> Optional[Orchestrator]:
    """Get the global orchestrator instance."""
    return _orchestrator


def set_orchestrator(orchestrator: Orchestrator):
    """Set the global orchestrator instance."""
    global _orchestrator
    _orchestrator = orchestrator
