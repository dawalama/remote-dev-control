"""SingleAgentExecutor — core tool-use loop for gwd.

Mirrors WebNativeProvider.run() but decoupled from RDC. Uses OpenAI-compatible
client to drive a tool-use agentic loop.
"""

import asyncio
import json
import logging
from typing import Any

from openai import OpenAI

from .prompts import executor_prompt
from .tools import AGENT_TOOLS, execute_tool
from .types import ExecutionStep, OnStepCallback

logger = logging.getLogger(__name__)


class SingleAgentExecutor:
    """Core tool-use loop executor."""

    def __init__(
        self,
        client: OpenAI,
        model: str,
        context: dict[str, Any] | None = None,
        max_iterations: int = 50,
    ):
        self.client = client
        self.model = model
        self.context = context or {}
        self.max_iterations = max_iterations
        self._cancelled = False
        self._step_index = 0

    async def _emit(
        self,
        on_step: OnStepCallback | None,
        step_type: str,
        subtask_id: str = "",
        **kwargs,
    ) -> ExecutionStep:
        step = ExecutionStep(
            type=step_type,
            step_index=self._step_index,
            subtask_id=subtask_id,
            **kwargs,
        )
        self._step_index += 1
        if on_step:
            try:
                await on_step(step)
            except Exception as e:
                logger.warning(f"on_step callback error: {e}")
        return step

    async def run(
        self,
        task: str,
        on_step: OnStepCallback | None = None,
        subtask_id: str = "",
    ) -> str:
        """Run the tool-use loop until completion.

        Each call starts with a completely fresh conversation (system + user task).
        This follows the Ralph Wiggum Loop pattern — no carried-over context
        between attempts, preventing drift and hallucination accumulation.

        Args:
            task: The task description for the agent.
            on_step: Optional callback for streaming execution steps.
            subtask_id: Optional subtask ID for step attribution.

        Returns:
            Final text output from the agent.
        """
        self._cancelled = False
        self._step_index = 0

        project_path = self.context.get("project_path", ".")
        system = executor_prompt(self.context)

        await self._emit(on_step, "status", subtask_id, content=f"Starting with model {self.model}")

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": task},
        ]

        final_text = ""

        for iteration in range(self.max_iterations):
            if self._cancelled:
                await self._emit(on_step, "status", subtask_id, content="Cancelled")
                return "Task cancelled"

            try:
                response = await asyncio.to_thread(
                    lambda: self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        tools=AGENT_TOOLS,
                        max_tokens=4096,
                    )
                )
            except Exception as e:
                await self._emit(on_step, "error", subtask_id, content=str(e), is_error=True)
                return f"LLM error: {e}"

            choice = response.choices[0]
            msg = choice.message

            if msg.content:
                final_text = msg.content
                await self._emit(on_step, "text", subtask_id, content=msg.content)

            if not msg.tool_calls:
                await self._emit(on_step, "status", subtask_id, content="Completed")
                return final_text or "Task completed"

            messages.append(msg.model_dump())

            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    tool_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except (json.JSONDecodeError, TypeError):
                    tool_args = {}

                await self._emit(
                    on_step, "tool_call", subtask_id,
                    tool_name=tool_name,
                    tool_args=tool_args,
                )

                result_str, is_err = await execute_tool(tool_name, tool_args, project_path)

                await self._emit(
                    on_step, "tool_result", subtask_id,
                    tool_name=tool_name,
                    result=result_str,
                    is_error=is_err,
                )

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })

            if choice.finish_reason == "stop":
                await self._emit(on_step, "status", subtask_id, content="Completed")
                return final_text or "Task completed"

        await self._emit(
            on_step, "error", subtask_id,
            content=f"Reached maximum iterations ({self.max_iterations})",
            is_error=True,
        )
        return f"Reached maximum iterations ({self.max_iterations})"

    def cancel(self):
        self._cancelled = True
