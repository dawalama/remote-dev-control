"""GWD agent provider — adapts gwd executor to RDC's AgentProvider interface."""

import logging
import os
from typing import Any

from .provider import AgentProvider, AgentStep, StepType, OnStepCallback

logger = logging.getLogger(__name__)


class GWDProvider(AgentProvider):
    """Agent provider that delegates to gwd's execute_task().

    Adapts gwd's ExecutionStep → RDC's AgentStep for streaming.
    """

    def __init__(self):
        self._cancelled = False
        self._executor = None  # Reference for cancellation

    async def run(
        self,
        task_description: str,
        project_path: str,
        project_name: str,
        model: str | None = None,
        on_step: OnStepCallback | None = None,
    ) -> str:
        self._cancelled = False

        # Build project context from DB
        context = self._load_project_context(project_name, project_path)

        # Build LLM client using RDC's vault for API keys
        client, resolved_model = self._build_client(model)

        # Map gwd ExecutionStep → RDC AgentStep
        async def adapt_step(gwd_step):
            if on_step is None:
                return
            step_type_map = {
                "thinking": StepType.THINKING,
                "tool_call": StepType.TOOL_CALL,
                "tool_result": StepType.TOOL_RESULT,
                "text": StepType.TEXT,
                "error": StepType.ERROR,
                "status": StepType.STATUS,
            }
            rdc_type = step_type_map.get(gwd_step.type, StepType.TEXT)
            rdc_step = AgentStep(
                type=rdc_type,
                content=gwd_step.content,
                tool_name=gwd_step.tool_name,
                tool_args=gwd_step.tool_args,
                result=gwd_step.result,
                is_error=gwd_step.is_error,
                step_index=gwd_step.step_index,
            )
            await on_step(rdc_step)

        from gwd import execute_task

        return await execute_task(
            task=task_description,
            project_path=project_path,
            project_context=context,
            client=client,
            model=resolved_model,
            on_step=adapt_step,
        )

    async def cancel(self) -> None:
        self._cancelled = True

    def _load_project_context(self, project_name: str, project_path: str) -> dict[str, Any]:
        """Load project profile from DB."""
        context: dict[str, Any] = {"project_path": project_path}
        try:
            from ..db.repositories import get_project_repo
            proj = get_project_repo().get(project_name)
            if proj and proj.config and isinstance(proj.config, dict):
                pp = proj.config.get("profile", {})
                if pp:
                    for key in ("purpose", "stack", "conventions", "test_command", "source_dir"):
                        if pp.get(key):
                            context[key] = pp[key]
        except Exception as e:
            logger.debug(f"Could not load project profile: {e}")
        return context

    def _build_client(self, model: str | None) -> tuple:
        """Build OpenAI client using RDC vault, falling back to env vars."""
        from gwd.client import create_client, default_model

        # Try RDC vault first
        try:
            from ..vault import get_secret
            api_key = get_secret("OPENROUTER_API_KEY") or get_secret("OPENAI_API_KEY")
            if api_key:
                # Determine base URL
                if get_secret("OPENROUTER_API_KEY"):
                    from openai import OpenAI
                    client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
                else:
                    from openai import OpenAI
                    client = OpenAI(api_key=api_key)

                resolved_model = model or default_model()
                return client, resolved_model
        except Exception:
            pass

        # Fallback to gwd's auto-detection from env vars
        client = create_client()
        resolved_model = model or default_model()
        return client, resolved_model
