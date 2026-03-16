"""Terminal provider — wraps existing subprocess/cursor-agent logic.

This provider spawns an external CLI agent (e.g. cursor-agent) as a
subprocess, tails its log file, and emits TEXT steps for each chunk of output.
It's backward-compatible with the existing worker behavior.
"""

import asyncio
import logging
import subprocess
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from .provider import AgentProvider, AgentStep, StepType, OnStepCallback
from ..config import get_rdc_home

logger = logging.getLogger(__name__)


class TerminalProvider(AgentProvider):
    """Provider that spawns an external CLI agent subprocess."""

    def __init__(self, provider_name: str = "cursor"):
        self._provider_name = provider_name
        self._process: Optional[subprocess.Popen] = None
        self._cancelled = False

    async def run(
        self,
        task_description: str,
        project_path: str,
        project_name: str,
        model: str | None = None,
        on_step: OnStepCallback | None = None,
    ) -> str:
        self._cancelled = False

        # cursor-agent only accepts its own model names — strip OpenRouter-style
        # model IDs (e.g. "inception/mercury-2") that would cause a 404.
        if self._provider_name in ("cursor", "cursor-agent") and model and "/" in model:
            logger.info("Skipping unsupported model %r for cursor-agent, using default", model)
            model = None

        # Build command
        if self._provider_name in ("cursor", "cursor-agent"):
            cmd = ["cursor-agent", "-p", task_description]
            if model:
                cmd.extend(["--model", model])
        else:
            cmd = [
                "python", "-m", "remote_dev_ctrl.server.agents.runner",
                "--project", project_name,
                "--provider", self._provider_name,
                "--task", task_description,
            ]
            if model:
                cmd.extend(["--model", model])

        # Setup log file
        log_dir = get_rdc_home() / "logs" / "agents"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{project_name}-web.log"

        if on_step:
            step_idx = 0
            await on_step(AgentStep(
                type=StepType.STATUS,
                content=f"Spawning {self._provider_name} agent...",
                step_index=step_idx,
            ))
            step_idx += 1

        # Spawn process
        log_file = open(log_path, "a")
        log_file.write(f"\n=== Started at {datetime.now().isoformat()} ===\n")
        log_file.flush()

        try:
            self._process = subprocess.Popen(
                cmd,
                cwd=project_path,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except Exception as e:
            log_file.close()
            if on_step:
                await on_step(AgentStep(
                    type=StepType.ERROR,
                    content=str(e),
                    is_error=True,
                    step_index=step_idx,
                ))
            return f"Failed to spawn agent: {e}"

        pid = self._process.pid
        if on_step:
            await on_step(AgentStep(
                type=StepType.STATUS,
                content=f"Agent running (pid={pid})",
                step_index=step_idx,
            ))
            step_idx += 1

        # Tail the log file while process runs
        position = log_path.stat().st_size if log_path.exists() else 0

        while True:
            if self._cancelled:
                self._process.kill()
                log_file.close()
                return "Cancelled"

            exit_code = self._process.poll()

            # Read new content
            try:
                current_size = log_path.stat().st_size
                if current_size > position:
                    with open(log_path, "r") as f:
                        f.seek(position)
                        new_content = f.read()
                        position = f.tell()

                    if new_content.strip() and on_step:
                        await on_step(AgentStep(
                            type=StepType.TEXT,
                            content=new_content,
                            step_index=step_idx,
                        ))
                        step_idx += 1
            except Exception:
                pass

            if exit_code is not None:
                break

            await asyncio.sleep(0.5)

        log_file.close()

        # Read final output
        output = ""
        try:
            content = log_path.read_text()
            lines = content.strip().split("\n")
            output = "\n".join(lines[-50:])
        except Exception:
            pass

        if on_step:
            status = "Completed" if exit_code == 0 else f"Failed (exit code {exit_code})"
            await on_step(AgentStep(
                type=StepType.STATUS,
                content=status,
                step_index=step_idx,
            ))

        return output or ("Success" if exit_code == 0 else f"Failed with exit code {exit_code}")

    async def cancel(self) -> None:
        self._cancelled = True
        if self._process:
            try:
                self._process.kill()
            except OSError:
                pass
