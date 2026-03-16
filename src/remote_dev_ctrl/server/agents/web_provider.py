"""Web-native agent provider.

Runs the tool-use agentic loop in-process using the OpenAI SDK
(compatible with OpenRouter, Anthropic via proxy, etc.).
Streams structured AgentStep events via on_step callback.
"""

import asyncio
import json
import logging
import os
import uuid
from typing import Any, Optional

from .provider import AgentProvider, AgentStep, StepType, OnStepCallback
from .tools import AGENT_TOOLS, REQUIRES_APPROVAL, execute_tool

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 50


class WebNativeProvider(AgentProvider):
    """Agent provider that runs tool-use loop in-process."""

    def __init__(self):
        self._cancelled = False
        self._step_index = 0
        self._client = None
        # Phase 2: approval events keyed by approval_id
        self._pending_approvals: dict[str, asyncio.Event] = {}
        self._approval_decisions: dict[str, dict] = {}

    def _get_client(self, model: str | None = None):
        """Get or create OpenAI-compatible client.

        For ollama/* models, connects to the local Ollama server.
        Otherwise, uses OpenRouter or OpenAI.
        """
        # Ollama models get a dedicated client pointing at localhost
        if model and model.startswith("ollama/"):
            try:
                from openai import OpenAI
            except ImportError:
                raise RuntimeError("openai package not installed — pip install openai")
            return OpenAI(
                api_key="ollama",  # Ollama doesn't need a real key
                base_url="http://localhost:11434/v1",
            )

        if self._client is not None:
            return self._client

        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("openai package not installed — pip install openai")

        from ..vault import get_secret

        # Try Anthropic first (via OpenRouter), then OpenAI directly
        api_key = (
            get_secret("OPENROUTER_API_KEY")
            or get_secret("OPENAI_API_KEY")
            or os.getenv("OPENROUTER_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        )

        if not api_key:
            raise RuntimeError(
                "No API key configured. Set OPENROUTER_API_KEY or OPENAI_API_KEY "
                "in vault (rdc vault set KEY) or environment."
            )

        # Determine base URL
        if get_secret("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY"):
            base_url = "https://openrouter.ai/api/v1"
        else:
            base_url = None  # Default OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url)
        return self._client

    def _select_model(self, model: str | None) -> str:
        """Select the model to use. Strips ollama/ prefix for Ollama API."""
        if model:
            if model.startswith("ollama/"):
                return model[len("ollama/"):]  # e.g. "ollama/qwen3.5" -> "qwen3.5"
            return model
        # Default to a capable model via OpenRouter
        from ..vault import get_secret
        if get_secret("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY"):
            return "anthropic/claude-sonnet-4-20250514"
        return "gpt-4o"

    def _system_prompt(self, project_name: str, project_path: str) -> str:
        """Build system prompt with project context."""
        profile_section = ""
        try:
            from ..db.repositories import get_project_repo
            proj = get_project_repo().get(project_name)
            if proj and proj.config and isinstance(proj.config, dict):
                pp = proj.config.get("profile", {})
                if pp:
                    parts = []
                    if pp.get("purpose"):
                        parts.append(f"Purpose: {pp['purpose']}")
                    if pp.get("stack"):
                        parts.append(f"Stack: {', '.join(pp['stack'])}")
                    if pp.get("conventions"):
                        parts.append(f"Conventions: {pp['conventions']}")
                    if pp.get("test_command"):
                        parts.append(f"Test command: {pp['test_command']}")
                    if pp.get("source_dir"):
                        parts.append(f"Source dir: {pp['source_dir']}")
                    if parts:
                        profile_section = "\n\nProject Profile:\n" + "\n".join(f"  {p}" for p in parts)
        except Exception:
            pass

        return f"""You are a skilled software engineer working on the project "{project_name}".
Project directory: {project_path}
{profile_section}

You have access to tools for reading, writing, and searching files, running commands, and git operations.
Use tools to understand the codebase before making changes.
Make focused, minimal changes — don't over-engineer or add unnecessary features.
Always read a file before editing it.
When done, provide a brief summary of what you did."""

    async def _emit(
        self,
        on_step: OnStepCallback | None,
        step_type: StepType,
        **kwargs,
    ) -> AgentStep:
        """Create and emit a step."""
        step = AgentStep(type=step_type, step_index=self._step_index, **kwargs)
        self._step_index += 1
        if on_step:
            try:
                await on_step(step)
            except Exception as e:
                logger.warning(f"on_step callback error: {e}")
        return step

    async def run(
        self,
        task_description: str,
        project_path: str,
        project_name: str,
        model: str | None = None,
        on_step: OnStepCallback | None = None,
    ) -> str:
        """Execute the agentic loop."""
        self._cancelled = False
        self._step_index = 0

        client = self._get_client(model)
        model_id = self._select_model(model)
        system = self._system_prompt(project_name, project_path)

        await self._emit(on_step, StepType.STATUS, content=f"Starting with model {model_id}")

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": task_description},
        ]

        final_text = ""

        for iteration in range(MAX_ITERATIONS):
            if self._cancelled:
                await self._emit(on_step, StepType.STATUS, content="Cancelled")
                return "Task cancelled by user"

            # Call LLM
            try:
                response = await asyncio.to_thread(
                    lambda: client.chat.completions.create(
                        model=model_id,
                        messages=messages,
                        tools=AGENT_TOOLS,
                        max_tokens=4096,
                    )
                )
            except Exception as e:
                await self._emit(on_step, StepType.ERROR, content=str(e), is_error=True)
                return f"LLM error: {e}"

            choice = response.choices[0]
            msg = choice.message

            # Process text content
            if msg.content:
                final_text = msg.content
                await self._emit(on_step, StepType.TEXT, content=msg.content)

            # If no tool calls, we're done
            if not msg.tool_calls:
                await self._emit(on_step, StepType.STATUS, content="Completed")
                return final_text or "Task completed"

            # Append assistant message
            messages.append(msg.model_dump())

            # Process each tool call
            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    tool_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except (json.JSONDecodeError, TypeError):
                    tool_args = {}

                await self._emit(
                    on_step,
                    StepType.TOOL_CALL,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    tool_call_id=tc.id,
                )

                # Phase 2: Check approval for destructive tools
                if tool_name in REQUIRES_APPROVAL and self._pending_approvals is not None:
                    decision = await self._request_approval(
                        on_step, tool_name, tool_args, tc.id, project_path
                    )
                    if decision and decision.get("decision") == "reject":
                        feedback = decision.get("feedback", "User rejected this action")
                        result_str = f"REJECTED by user: {feedback}"
                        is_err = True
                        await self._emit(
                            on_step,
                            StepType.TOOL_RESULT,
                            tool_name=tool_name,
                            tool_call_id=tc.id,
                            result=result_str,
                            is_error=True,
                        )
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result_str,
                        })
                        continue

                # Execute tool
                result_str, is_err = await execute_tool(tool_name, tool_args, project_path)

                await self._emit(
                    on_step,
                    StepType.TOOL_RESULT,
                    tool_name=tool_name,
                    tool_call_id=tc.id,
                    result=result_str,
                    is_error=is_err,
                )

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })

            # Check finish reason
            if choice.finish_reason == "stop":
                await self._emit(on_step, StepType.STATUS, content="Completed")
                return final_text or "Task completed"

        await self._emit(
            on_step, StepType.ERROR,
            content=f"Reached maximum iterations ({MAX_ITERATIONS})",
            is_error=True,
        )
        return f"Reached maximum iterations ({MAX_ITERATIONS})"

    async def _request_approval(
        self,
        on_step: OnStepCallback | None,
        tool_name: str,
        tool_args: dict,
        tool_call_id: str,
        project_path: str,
    ) -> dict | None:
        """Request approval for a destructive operation. Returns decision dict or None (auto-approve)."""
        # Build preview
        preview = ""
        if tool_name == "run_command":
            preview = tool_args.get("command", "")
        elif tool_name in ("write_file", "create_file"):
            content = tool_args.get("content", "")
            preview = content[:2000] + ("..." if len(content) > 2000 else "")
        elif tool_name == "edit_file":
            preview = f"--- old\n{tool_args.get('old_string', '')}\n+++ new\n{tool_args.get('new_string', '')}"
        elif tool_name == "delete_file":
            preview = f"Delete: {tool_args.get('path', '')}"

        approval_id = str(uuid.uuid4())[:8]
        event = asyncio.Event()
        self._pending_approvals[approval_id] = event

        await self._emit(
            on_step,
            StepType.APPROVAL_REQUEST,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_call_id=tool_call_id,
            approval_id=approval_id,
            preview=preview,
            content=f"Approve {tool_name}?",
        )

        # Wait for response (5 minute timeout)
        try:
            await asyncio.wait_for(event.wait(), timeout=300)
        except asyncio.TimeoutError:
            self._pending_approvals.pop(approval_id, None)
            # Auto-approve on timeout
            return None

        self._pending_approvals.pop(approval_id, None)
        return self._approval_decisions.pop(approval_id, None)

    def resolve_approval(self, approval_id: str, decision: str, feedback: str = "") -> bool:
        """Resolve a pending approval. Called from WebSocket handler."""
        event = self._pending_approvals.get(approval_id)
        if not event:
            return False
        self._approval_decisions[approval_id] = {
            "decision": decision,
            "feedback": feedback,
        }
        event.set()
        return True

    async def cancel(self) -> None:
        """Cancel the running task."""
        self._cancelled = True
        # Resolve all pending approvals as rejections
        for aid, event in list(self._pending_approvals.items()):
            self._approval_decisions[aid] = {"decision": "reject", "feedback": "Cancelled"}
            event.set()
