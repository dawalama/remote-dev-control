"""Event bus for broadcasting events to connected clients."""

import asyncio
import json
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Awaitable

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """Event types."""
    # Agent events
    AGENT_SPAWNED = "agent.spawned"
    AGENT_STOPPED = "agent.stopped"
    AGENT_STATUS = "agent.status"
    AGENT_OUTPUT = "agent.output"
    AGENT_ERROR = "agent.error"
    
    # Task events
    TASK_CREATED = "task.created"
    TASK_ASSIGNED = "task.assigned"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_BLOCKED = "task.blocked"
    
    # System events
    SERVER_STARTED = "server.started"
    SERVER_STOPPED = "server.stopped"
    
    # User events
    ESCALATION = "escalation"
    NOTIFICATION = "notification"


class Event(BaseModel):
    """An event in the system."""
    type: EventType
    timestamp: datetime = Field(default_factory=datetime.now)
    project: str | None = None
    data: dict = Field(default_factory=dict)
    
    def to_json(self) -> str:
        return json.dumps({
            "type": self.type.value,
            "timestamp": self.timestamp.isoformat(),
            "project": self.project,
            "data": self.data,
        })


class EventBus:
    """Async event bus for broadcasting events."""
    
    def __init__(self):
        self._subscribers: dict[str, list[Callable[[Event], Awaitable[None]]]] = {}
        self._all_subscribers: list[Callable[[Event], Awaitable[None]]] = []
        self._history: list[Event] = []
        self._max_history = 100
    
    def subscribe(
        self,
        event_type: EventType | str | None = None,
        callback: Callable[[Event], Awaitable[None]] = None,
    ) -> Callable:
        """Subscribe to events.
        
        If event_type is None, subscribes to all events.
        Can be used as a decorator.
        """
        def decorator(fn: Callable[[Event], Awaitable[None]]) -> Callable:
            if event_type is None:
                self._all_subscribers.append(fn)
            else:
                key = event_type.value if isinstance(event_type, EventType) else event_type
                if key not in self._subscribers:
                    self._subscribers[key] = []
                self._subscribers[key].append(fn)
            return fn
        
        if callback:
            return decorator(callback)
        return decorator
    
    async def publish(self, event: Event) -> None:
        """Publish an event to all subscribers."""
        # Add to history
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
        
        # Notify type-specific subscribers
        key = event.type.value
        for callback in self._subscribers.get(key, []):
            try:
                await callback(event)
            except Exception as e:
                print(f"Error in event handler: {e}")
        
        # Notify all-event subscribers
        for callback in self._all_subscribers:
            try:
                await callback(event)
            except Exception as e:
                print(f"Error in event handler: {e}")
    
    def publish_sync(self, event: Event) -> None:
        """Publish an event synchronously (for non-async contexts)."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self.publish(event))
            else:
                loop.run_until_complete(self.publish(event))
        except RuntimeError:
            # No event loop, just store in history
            self._history.append(event)
    
    def get_history(self, limit: int = 50, event_type: EventType | None = None) -> list[Event]:
        """Get recent events."""
        events = self._history
        if event_type:
            events = [e for e in events if e.type == event_type]
        return events[-limit:]
    
    def emit(self, event_type: EventType, project: str | None = None, **data) -> Event:
        """Convenience method to create and publish an event."""
        event = Event(type=event_type, project=project, data=data)
        self.publish_sync(event)
        return event


# Global event bus
_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Get the global event bus."""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
