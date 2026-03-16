"""MultiAgentOrchestrator — plan, execute, verify, review loop.

Uses the Ralph Wiggum Loop pattern (Geoffrey Huntley): each retry starts
with a completely fresh context. The agent discovers current state from
disk (git diff, file reads) rather than carrying forward a polluted
conversation history. This prevents drift and hallucination accumulation.

Flow:
    Plan -> [wave1: subtasks in parallel] -> [wave2: ...] -> Summary

Per subtask (max 3 attempts):
    Fresh executor -> Verify -> pass? done : wipe context, loop
"""

import asyncio
import logging
from typing import Any

from openai import OpenAI

from .executor import SingleAgentExecutor
from .planner import PlannerAgent
from .types import (
    ExecutionStep,
    OnStepCallback,
    Plan,
    Subtask,
    SubtaskStatus,
    VerifyResult,
)
from .verifier import Verifier

logger = logging.getLogger(__name__)

MAX_SUBTASK_ATTEMPTS = 3


class MultiAgentOrchestrator:
    """Orchestrates complex tasks: plan -> wave execution -> verify -> summary."""

    def __init__(
        self,
        client: OpenAI,
        model: str,
        context: dict[str, Any] | None = None,
    ):
        self.client = client
        self.model = model
        self.context = context or {}
        self._cancelled = False
        self._step_index = 0

    async def _emit(self, on_step: OnStepCallback | None, step_type: str, **kwargs):
        step = ExecutionStep(type=step_type, step_index=self._step_index, **kwargs)
        self._step_index += 1
        if on_step:
            try:
                await on_step(step)
            except Exception:
                pass

    async def run(
        self,
        task: str,
        project_context: dict[str, Any] | None = None,
        on_step: OnStepCallback | None = None,
    ) -> str:
        """Execute a complex task via plan -> execute -> verify loop."""
        self._cancelled = False
        self._step_index = 0

        context = dict(self.context)
        if project_context:
            context.update(project_context)

        # Phase 1: Create plan
        await self._emit(on_step, "status", content="Creating execution plan...")

        planner = PlannerAgent(
            client=self.client,
            model=self.model,
            context=context,
        )
        plan = await planner.create_plan(task, on_step=on_step)

        await self._emit(
            on_step, "text",
            content=f"Plan: {plan.analysis}\nSubtasks: {len(plan.subtasks)}",
        )

        if not plan.subtasks:
            return "No subtasks to execute"

        # Phase 2: Execute waves
        waves = plan.waves()
        results: dict[str, str] = {}

        for wave_idx, wave in enumerate(waves):
            if self._cancelled:
                return "Orchestration cancelled"

            await self._emit(
                on_step, "status",
                content=f"Wave {wave_idx + 1}/{len(waves)}: {len(wave)} subtask(s)",
            )

            # Run subtasks in this wave concurrently
            coros = [
                self._execute_subtask(subtask, context, on_step)
                for subtask in wave
            ]
            wave_results = await asyncio.gather(*coros, return_exceptions=True)

            for subtask, result in zip(wave, wave_results):
                if isinstance(result, Exception):
                    subtask.status = SubtaskStatus.FAILED
                    subtask.result = str(result)
                    results[subtask.id] = f"FAILED: {result}"
                else:
                    results[subtask.id] = result

        # Phase 3: Summary
        summary_parts = [f"Task: {task}", f"Plan: {plan.analysis}", ""]
        for st in plan.subtasks:
            status_icon = "+" if st.status == SubtaskStatus.PASSED else "-"
            summary_parts.append(f"  [{status_icon}] {st.id}. {st.description}")
            if st.result:
                result_preview = st.result[:200] + "..." if len(st.result) > 200 else st.result
                summary_parts.append(f"      {result_preview}")

        summary = "\n".join(summary_parts)
        await self._emit(on_step, "text", content=summary)
        return summary

    async def _execute_subtask(
        self,
        subtask: Subtask,
        context: dict[str, Any],
        on_step: OnStepCallback | None,
    ) -> str:
        """Execute a single subtask using the Ralph Wiggum Loop pattern.

        Each attempt gets a completely fresh executor with no prior conversation
        history. The agent discovers current project state from disk (git diff,
        file reads) rather than inheriting a potentially polluted context.

        On retry, only a short factual note about the attempt number is included
        in the task prompt — no accumulated failure messages or suggestions that
        could bias the agent toward the same broken approach.
        """
        subtask.status = SubtaskStatus.RUNNING
        await self._emit(
            on_step, "status",
            content=f"Subtask {subtask.id}: {subtask.description[:80]}",
            subtask_id=subtask.id,
        )

        verifier = Verifier(
            client=self.client,
            model=self.model,
            context=context,
        )

        last_verification: VerifyResult | None = None

        for attempt in range(1, MAX_SUBTASK_ATTEMPTS + 1):
            subtask.attempts = attempt

            # Fresh executor each attempt — no carried-over conversation
            executor = SingleAgentExecutor(
                client=self.client,
                model=self.model,
                context=context,
            )

            # Build task prompt: original description + minimal retry hint
            if attempt == 1:
                task_prompt = subtask.description
            else:
                # Only tell the agent it's a retry and to check current state.
                # Don't feed back the previous error/suggestion — let it
                # discover the actual state from disk with fresh eyes.
                task_prompt = (
                    f"{subtask.description}\n\n"
                    f"Note: This is attempt {attempt}/{MAX_SUBTASK_ATTEMPTS}. "
                    f"A previous attempt may have made partial progress. "
                    f"Start by checking git diff and the current state of relevant files "
                    f"before making any changes."
                )

            result = await executor.run(
                task=task_prompt,
                on_step=on_step,
                subtask_id=subtask.id,
            )

            # Verify
            last_verification = await verifier.verify(subtask, on_step=on_step)

            if last_verification.passed:
                subtask.status = SubtaskStatus.PASSED
                subtask.result = result
                await self._emit(
                    on_step, "status",
                    content=f"Subtask {subtask.id} passed (attempt {attempt})",
                    subtask_id=subtask.id,
                )
                return result

            # Failed — log and loop with fresh context
            await self._emit(
                on_step, "status",
                content=f"Subtask {subtask.id} failed verification (attempt {attempt}): {last_verification.output[:100]}",
                subtask_id=subtask.id,
            )

        # All attempts exhausted
        subtask.status = SubtaskStatus.FAILED
        fail_msg = last_verification.output if last_verification else "unknown error"
        subtask.result = f"Failed after {MAX_SUBTASK_ATTEMPTS} attempts: {fail_msg}"
        return subtask.result

    def cancel(self):
        self._cancelled = True
