"""PlannerAgent — analyzes codebase and produces a structured execution plan."""

import asyncio
import json
import logging
from typing import Any

from openai import OpenAI

from .prompts import planner_prompt
from .tools import AGENT_TOOLS, execute_tool
from .types import ExecutionStep, OnStepCallback, Plan, Subtask

logger = logging.getLogger(__name__)

# Planner only needs read-only tools
_PLANNER_TOOLS = [
    t for t in AGENT_TOOLS
    if t["function"]["name"] in ("read_file", "list_directory", "search_files", "git_status")
]


class PlannerAgent:
    """Gathers codebase context and produces a structured plan."""

    def __init__(
        self,
        client: OpenAI,
        model: str,
        context: dict[str, Any] | None = None,
        max_iterations: int = 15,
    ):
        self.client = client
        self.model = model
        self.context = context or {}
        self.max_iterations = max_iterations
        self._step_index = 0

    async def _emit(self, on_step: OnStepCallback | None, step_type: str, **kwargs):
        step = ExecutionStep(type=step_type, step_index=self._step_index, **kwargs)
        self._step_index += 1
        if on_step:
            try:
                await on_step(step)
            except Exception:
                pass

    async def create_plan(
        self,
        task: str,
        on_step: OnStepCallback | None = None,
    ) -> Plan:
        """Analyze the codebase and create an execution plan.

        The planner uses read-only tools to understand the project,
        then produces a structured JSON plan.
        """
        self._step_index = 0
        project_path = self.context.get("project_path", ".")

        await self._emit(on_step, "status", content="Planning...")

        system = planner_prompt()
        context_parts = [f"Project directory: {project_path}"]
        if self.context.get("purpose"):
            context_parts.append(f"Purpose: {self.context['purpose']}")
        if self.context.get("stack"):
            stack = self.context["stack"]
            if isinstance(stack, list):
                stack = ", ".join(stack)
            context_parts.append(f"Stack: {stack}")

        user_msg = f"Task: {task}\n\n{'  '.join(context_parts)}\n\nFirst, explore the codebase to understand the relevant parts, then produce your plan."

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ]

        for iteration in range(self.max_iterations):
            try:
                response = await asyncio.to_thread(
                    lambda: self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        tools=_PLANNER_TOOLS,
                        max_tokens=4096,
                    )
                )
            except Exception as e:
                logger.error(f"Planner LLM error: {e}")
                return Plan(task=task, analysis=f"Planning failed: {e}")

            choice = response.choices[0]
            msg = choice.message

            # Try to parse JSON plan from text content
            if msg.content:
                await self._emit(on_step, "thinking", content=msg.content)
                plan = self._try_parse_plan(task, msg.content)
                if plan and not msg.tool_calls:
                    await self._emit(on_step, "status", content=f"Plan created with {len(plan.subtasks)} subtasks")
                    return plan

            if not msg.tool_calls:
                # Final response without tool calls — try to extract plan
                if msg.content:
                    plan = self._try_parse_plan(task, msg.content)
                    if plan:
                        return plan
                # Couldn't parse — return single-subtask fallback
                return Plan(
                    task=task,
                    analysis="Could not create structured plan",
                    subtasks=[Subtask(id="1", description=task)],
                )

            messages.append(msg.model_dump())

            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    tool_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except (json.JSONDecodeError, TypeError):
                    tool_args = {}

                await self._emit(on_step, "tool_call", tool_name=tool_name, tool_args=tool_args)

                result_str, is_err = await execute_tool(tool_name, tool_args, project_path)

                await self._emit(on_step, "tool_result", tool_name=tool_name, result=result_str, is_error=is_err)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })

        # Max iterations reached
        return Plan(
            task=task,
            analysis="Planner reached max iterations",
            subtasks=[Subtask(id="1", description=task)],
        )

    def _try_parse_plan(self, task: str, text: str) -> Plan | None:
        """Try to extract a JSON plan from text."""
        # Try direct parse
        try:
            data = json.loads(text)
            return self._build_plan(task, data)
        except (json.JSONDecodeError, ValueError):
            pass

        # Try extracting JSON from markdown fences or embedded in text
        import re
        patterns = [
            r'```json\s*\n(.*?)\n\s*```',
            r'```\s*\n(.*?)\n\s*```',
            r'(\{[^{}]*"subtasks"[^{}]*\[.*?\]\s*\})',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                    return self._build_plan(task, data)
                except (json.JSONDecodeError, ValueError):
                    continue

        return None

    def _build_plan(self, task: str, data: dict) -> Plan:
        """Build a Plan from parsed JSON data."""
        subtasks = []
        for st in data.get("subtasks", []):
            subtasks.append(Subtask(
                id=str(st.get("id", len(subtasks) + 1)),
                description=st.get("description", ""),
                depends_on=st.get("depends_on", []),
                verification=st.get("verification", ""),
            ))

        if not subtasks:
            subtasks = [Subtask(id="1", description=task)]

        return Plan(
            task=task,
            analysis=data.get("analysis", ""),
            subtasks=subtasks,
        )
