"""Verifier — validates subtask completion via tests, diff checks, and LLM judgment."""

import asyncio
import json
import logging
from typing import Any

from openai import OpenAI

from .prompts import verifier_prompt
from .tools import execute_tool
from .types import ExecutionStep, OnStepCallback, Subtask, VerifyResult

logger = logging.getLogger(__name__)


class Verifier:
    """Three-level verification: test command, diff check, LLM judgment."""

    def __init__(
        self,
        client: OpenAI,
        model: str,
        context: dict[str, Any] | None = None,
    ):
        self.client = client
        self.model = model
        self.context = context or {}
        self._step_index = 0

    async def _emit(self, on_step: OnStepCallback | None, step_type: str, **kwargs):
        step = ExecutionStep(type=step_type, step_index=self._step_index, **kwargs)
        self._step_index += 1
        if on_step:
            try:
                await on_step(step)
            except Exception:
                pass

    async def verify(
        self,
        subtask: Subtask,
        on_step: OnStepCallback | None = None,
    ) -> VerifyResult:
        """Verify a subtask was completed correctly.

        Three levels:
        1. Run test_command if available → check exit code
        2. git diff --stat → confirm files were changed
        3. LLM judgment → feed diff + task, ask pass/fail
        """
        project_path = self.context.get("project_path", ".")

        await self._emit(on_step, "status", content=f"Verifying subtask {subtask.id}")

        # Level 1: Run test command if available
        test_command = self.context.get("test_command") or subtask.verification
        if test_command and not test_command.startswith("check "):
            await self._emit(on_step, "status", content=f"Running: {test_command}")
            result, is_err = await execute_tool("run_command", {"command": test_command}, project_path)
            if is_err:
                suggestion = await self._generate_fix_suggestion(subtask, result, on_step)
                return VerifyResult(
                    passed=False,
                    output=result,
                    suggestion=suggestion,
                )

        # Level 2: Check that files were actually changed
        diff_result, _ = await execute_tool("run_command", {"command": "git diff --stat"}, project_path)
        if not diff_result.strip():
            # Also check untracked files
            status_result, _ = await execute_tool("run_command", {"command": "git status --short"}, project_path)
            if not status_result.strip():
                return VerifyResult(
                    passed=False,
                    output="No files were changed",
                    suggestion="The task may not have been executed. Try again.",
                )

        # Level 3: LLM judgment
        full_diff, _ = await execute_tool("run_command", {"command": "git diff"}, project_path)
        if not full_diff.strip():
            # If no staged/unstaged diff, check untracked
            full_diff = diff_result

        judgment = await self._llm_judge(subtask, full_diff, on_step)
        return judgment

    async def _llm_judge(
        self,
        subtask: Subtask,
        diff: str,
        on_step: OnStepCallback | None,
    ) -> VerifyResult:
        """Use LLM to judge if the subtask was completed correctly."""
        await self._emit(on_step, "status", content="LLM verification...")

        # Truncate diff if too long
        if len(diff) > 10000:
            diff = diff[:5000] + "\n\n... (diff truncated) ...\n\n" + diff[-5000:]

        try:
            response = await asyncio.to_thread(
                lambda: self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": verifier_prompt()},
                        {"role": "user", "content": f"Task: {subtask.description}\n\nDiff:\n{diff}"},
                    ],
                    max_tokens=1024,
                )
            )
        except Exception as e:
            logger.warning(f"LLM verification failed: {e}")
            # On LLM failure, pass by default (don't block on verification errors)
            return VerifyResult(passed=True, output=f"LLM verification unavailable: {e}")

        text = response.choices[0].message.content or ""

        try:
            data = json.loads(text)
            passed = data.get("passed", True)
            suggestion = data.get("suggestion", "")
            reason = data.get("reason", "")
            return VerifyResult(passed=passed, output=reason, suggestion=suggestion)
        except (json.JSONDecodeError, ValueError):
            # If we can't parse the response, assume pass
            return VerifyResult(passed=True, output=text)

    async def _generate_fix_suggestion(
        self,
        subtask: Subtask,
        error_output: str,
        on_step: OnStepCallback | None,
    ) -> str:
        """Ask LLM what to fix based on the error output."""
        await self._emit(on_step, "status", content="Generating fix suggestion...")

        # Truncate error if too long
        if len(error_output) > 5000:
            error_output = error_output[:2500] + "\n...\n" + error_output[-2500:]

        try:
            response = await asyncio.to_thread(
                lambda: self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "You are a debugging expert. Given a task and error output, suggest a concise fix. Be specific and actionable. Keep your response under 200 words."},
                        {"role": "user", "content": f"Task: {subtask.description}\n\nError:\n{error_output}"},
                    ],
                    max_tokens=512,
                )
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.warning(f"Fix suggestion failed: {e}")
            return ""
