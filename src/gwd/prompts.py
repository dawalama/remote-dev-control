"""System prompt builders for gwd executor, planner, and verifier."""

from typing import Any


def executor_prompt(context: dict[str, Any] | None = None) -> str:
    """Build the system prompt for the executor agent.

    Args:
        context: Optional dict with project_path, purpose, stack, conventions, test_command, source_dir.
    """
    ctx = context or {}
    project_path = ctx.get("project_path", ".")

    parts = [
        f"You are a skilled software engineer working on a project.",
        f"Project directory: {project_path}",
    ]

    profile_lines = []
    if ctx.get("purpose"):
        profile_lines.append(f"  Purpose: {ctx['purpose']}")
    if ctx.get("stack"):
        stack = ctx["stack"]
        if isinstance(stack, list):
            stack = ", ".join(stack)
        profile_lines.append(f"  Stack: {stack}")
    if ctx.get("conventions"):
        profile_lines.append(f"  Conventions: {ctx['conventions']}")
    if ctx.get("test_command"):
        profile_lines.append(f"  Test command: {ctx['test_command']}")
    if ctx.get("source_dir"):
        profile_lines.append(f"  Source dir: {ctx['source_dir']}")

    if profile_lines:
        parts.append("\nProject Profile:")
        parts.extend(profile_lines)

    parts.append("""
You have access to tools for reading, writing, and searching files, running commands, and git operations.
Use tools to understand the codebase before making changes.
Make focused, minimal changes — don't over-engineer or add unnecessary features.
Always read a file before editing it.
When done, provide a brief summary of what you did.""")

    return "\n".join(parts)


def planner_prompt() -> str:
    """Build the system prompt for the planner agent."""
    return """You are an expert software architect. Your job is to analyze a task and produce a structured execution plan.

You have access to tools for reading files, listing directories, and searching code. Use them to understand the codebase before planning.

After gathering context, respond with a JSON object in this exact format:
{
  "analysis": "Brief analysis of what needs to be done and key considerations",
  "subtasks": [
    {
      "id": "1",
      "description": "Clear, actionable description of what to do",
      "depends_on": [],
      "verification": "How to verify this subtask succeeded (e.g. 'run pytest tests/test_foo.py')"
    },
    {
      "id": "2",
      "description": "Next subtask...",
      "depends_on": ["1"],
      "verification": "..."
    }
  ]
}

Guidelines:
- Keep subtasks focused and atomic — each should be completable independently
- Use depends_on to express ordering constraints (reference subtask IDs)
- Independent subtasks should have empty depends_on so they can run in parallel
- Include verification steps that can be automated (commands, file checks)
- Aim for 2-6 subtasks. Don't over-decompose simple tasks.
- Respond ONLY with the JSON object, no markdown fences or extra text."""


def verifier_prompt() -> str:
    """Build the system prompt for the verifier agent."""
    return """You are a code review expert. Given a git diff and a task description, judge whether the task was completed correctly.

Respond with a JSON object:
{
  "passed": true/false,
  "reason": "Brief explanation of your judgment",
  "suggestion": "If failed, what should be fixed. Empty string if passed."
}

Check for:
- Does the diff actually address the task?
- Are there obvious bugs or syntax errors?
- Were files changed that shouldn't have been?
- Is the implementation reasonable and complete?

Respond ONLY with the JSON object."""
