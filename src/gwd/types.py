"""Shared types for gwd task executor."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine


class TaskComplexity(str, Enum):
    SIMPLE = "simple"
    COMPLEX = "complex"


class SubtaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Subtask:
    id: str
    description: str
    depends_on: list[str] = field(default_factory=list)
    verification: str = ""
    status: SubtaskStatus = SubtaskStatus.PENDING
    result: str = ""
    attempts: int = 0


@dataclass
class Plan:
    task: str
    analysis: str
    subtasks: list[Subtask] = field(default_factory=list)

    def waves(self) -> list[list[Subtask]]:
        """Group subtasks into waves by dependency order.

        Each wave contains subtasks whose dependencies are all in prior waves.
        """
        done: set[str] = set()
        remaining = list(self.subtasks)
        result: list[list[Subtask]] = []

        while remaining:
            wave = [s for s in remaining if all(d in done for d in s.depends_on)]
            if not wave:
                # Break deadlock — take first remaining
                wave = [remaining[0]]
            for s in wave:
                remaining.remove(s)
                done.add(s.id)
            result.append(wave)

        return result


@dataclass
class VerifyResult:
    passed: bool
    output: str = ""
    suggestion: str = ""


@dataclass
class ExecutionStep:
    type: str  # "thinking", "tool_call", "tool_result", "text", "error", "status"
    content: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    result: str = ""
    is_error: bool = False
    step_index: int = 0
    subtask_id: str = ""

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "type": self.type,
            "content": self.content,
            "step_index": self.step_index,
        }
        if self.tool_name:
            d["tool_name"] = self.tool_name
        if self.tool_args:
            d["tool_args"] = self.tool_args
        if self.result:
            d["result"] = self.result
        if self.is_error:
            d["is_error"] = True
        if self.subtask_id:
            d["subtask_id"] = self.subtask_id
        return d


# Callback type: async function that receives an ExecutionStep
OnStepCallback = Callable[[ExecutionStep], Coroutine[Any, Any, None]]
