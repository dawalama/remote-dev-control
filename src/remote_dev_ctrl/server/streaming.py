"""Real-time agent output streaming."""

import asyncio
import os
from pathlib import Path
from typing import Callable, Optional
from datetime import datetime

from .config import get_rdc_home
from .scrubber import scrub_log_content


class LogStreamer:
    """Streams log file content in real-time."""
    
    def __init__(self, log_path: Path, callback: Callable[[str], None]):
        self.log_path = log_path
        self.callback = callback
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._position = 0
    
    async def start(self, from_beginning: bool = False):
        """Start streaming the log file."""
        if self._running:
            return
        
        self._running = True
        
        # Start from end unless specified
        if not from_beginning and self.log_path.exists():
            self._position = self.log_path.stat().st_size
        
        self._task = asyncio.create_task(self._stream_loop())
    
    async def stop(self):
        """Stop streaming."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
    
    async def _stream_loop(self):
        """Main streaming loop - tails the log file."""
        while self._running:
            try:
                if self.log_path.exists():
                    current_size = self.log_path.stat().st_size
                    
                    if current_size > self._position:
                        # New content available
                        with open(self.log_path, 'r') as f:
                            f.seek(self._position)
                            new_content = f.read()
                            self._position = f.tell()
                        
                        if new_content:
                            # Scrub secrets before sending
                            scrubbed = scrub_log_content(new_content)
                            await self._emit(scrubbed)
                    
                    elif current_size < self._position:
                        # File was truncated, reset
                        self._position = 0
                
                await asyncio.sleep(0.5)  # Poll every 500ms
                
            except Exception as e:
                await asyncio.sleep(1)  # Back off on error
    
    async def _emit(self, content: str):
        """Emit content to callback."""
        if asyncio.iscoroutinefunction(self.callback):
            await self.callback(content)
        else:
            self.callback(content)


class AgentStreamManager:
    """Manages streaming for all agents."""

    def __init__(self):
        self._streamers: dict[str, LogStreamer] = {}
        self._subscribers: dict[str, set[Callable]] = {}
        # Task-level step streaming (for web-native provider)
        self._task_subscribers: dict[str, set[Callable]] = {}
    
    def get_log_path(self, project: str) -> Path:
        """Get log file path for a project."""
        return get_rdc_home() / "logs" / "agents" / f"{project}.log"
    
    async def subscribe(self, project: str, callback: Callable[[str, str], None]) -> None:
        """Subscribe to agent output for a project.
        
        Callback receives (project, content).
        """
        if project not in self._subscribers:
            self._subscribers[project] = set()
        
        self._subscribers[project].add(callback)
        
        # Start streamer if not running
        if project not in self._streamers:
            log_path = self.get_log_path(project)
            
            async def on_content(content: str):
                await self._broadcast(project, content)
            
            streamer = LogStreamer(log_path, on_content)
            self._streamers[project] = streamer
            await streamer.start(from_beginning=False)
    
    async def unsubscribe(self, project: str, callback: Callable) -> None:
        """Unsubscribe from agent output."""
        if project in self._subscribers:
            self._subscribers[project].discard(callback)
            
            # Stop streamer if no subscribers
            if not self._subscribers[project]:
                del self._subscribers[project]
                if project in self._streamers:
                    await self._streamers[project].stop()
                    del self._streamers[project]
    
    async def _broadcast(self, project: str, content: str) -> None:
        """Broadcast content to all subscribers."""
        if project not in self._subscribers:
            return
        
        for callback in list(self._subscribers[project]):
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(project, content)
                else:
                    callback(project, content)
            except Exception:
                pass
    
    # ----- Task-level step streaming (web-native agent) -----

    async def subscribe_task(self, task_id: str, callback: Callable) -> None:
        """Subscribe to structured steps for a specific task.

        Callback receives a dict (AgentStep.to_dict()).
        """
        if task_id not in self._task_subscribers:
            self._task_subscribers[task_id] = set()
        self._task_subscribers[task_id].add(callback)

    async def unsubscribe_task(self, task_id: str, callback: Callable) -> None:
        """Unsubscribe from task step events."""
        if task_id in self._task_subscribers:
            self._task_subscribers[task_id].discard(callback)
            if not self._task_subscribers[task_id]:
                del self._task_subscribers[task_id]

    async def emit_task_step(self, task_id: str, step_data: dict) -> None:
        """Emit a step to all subscribers of a task."""
        if task_id not in self._task_subscribers:
            return
        for callback in list(self._task_subscribers[task_id]):
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(step_data)
                else:
                    callback(step_data)
            except Exception:
                pass

    async def stop_all(self):
        """Stop all streamers."""
        for streamer in self._streamers.values():
            await streamer.stop()
        self._streamers.clear()
        self._subscribers.clear()
        self._task_subscribers.clear()


# Global stream manager
_stream_manager: Optional[AgentStreamManager] = None


def get_stream_manager() -> AgentStreamManager:
    """Get the global stream manager."""
    global _stream_manager
    if _stream_manager is None:
        _stream_manager = AgentStreamManager()
    return _stream_manager
