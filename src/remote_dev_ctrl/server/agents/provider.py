"""Provider abstraction for agent execution."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Optional


class StepType(str, Enum):
    THINKING = "thinking"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TEXT = "text"
    ERROR = "error"
    STATUS = "status"
    APPROVAL_REQUEST = "approval_request"


@dataclass
class AgentStep:
    type: StepType
    content: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_call_id: str = ""
    result: str = ""
    is_error: bool = False
    step_index: int = 0
    approval_id: str = ""
    # For approval_request: preview data (diff, command string, etc.)
    preview: str = ""

    def to_dict(self) -> dict:
        d = {
            "type": self.type.value,
            "content": self.content,
            "step_index": self.step_index,
        }
        if self.tool_name:
            d["tool_name"] = self.tool_name
        if self.tool_args:
            d["tool_args"] = self.tool_args
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.result:
            d["result"] = self.result
        if self.is_error:
            d["is_error"] = True
        if self.approval_id:
            d["approval_id"] = self.approval_id
        if self.preview:
            d["preview"] = self.preview
        return d


# Callback type: async function that receives an AgentStep
OnStepCallback = Callable[[AgentStep], Coroutine[Any, Any, None]]


class AgentProvider(ABC):
    """Abstract base class for agent execution providers."""

    @abstractmethod
    async def run(
        self,
        task_description: str,
        project_path: str,
        project_name: str,
        model: str | None = None,
        on_step: OnStepCallback | None = None,
    ) -> str:
        """Execute a task and return the final result.

        Args:
            task_description: What the agent should do
            project_path: Filesystem path to the project
            project_name: Human-readable project name
            model: Optional model override
            on_step: Callback for streaming execution steps

        Returns:
            Final result/output string
        """
        ...

    @abstractmethod
    async def cancel(self) -> None:
        """Cancel the currently running task."""
        ...
