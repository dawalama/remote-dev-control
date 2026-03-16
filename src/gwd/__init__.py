"""gwd — get work done. Native task executor.

Public API:
    execute_task()  — run a task against a project
    classify_task() — determine if a task is simple or complex
"""

from .types import TaskComplexity, ExecutionStep, OnStepCallback


async def execute_task(
    task: str,
    project_path: str,
    project_context: dict | None = None,
    client=None,
    model: str | None = None,
    on_step: OnStepCallback | None = None,
    max_iterations: int = 50,
    force_complexity: TaskComplexity | None = None,
) -> str:
    """Execute a task against a project directory.

    Routes to SingleAgentExecutor for simple tasks or
    MultiAgentOrchestrator for complex tasks.

    Args:
        task: What to do.
        project_path: Filesystem path to the project.
        project_context: Optional dict with purpose, stack, conventions, etc.
        client: Optional OpenAI-compatible client. Auto-created if None.
        model: Optional model override. Auto-detected if None.
        on_step: Optional callback for streaming execution steps.
        max_iterations: Max tool-use iterations (for simple tasks).
        force_complexity: Override automatic classification.

    Returns:
        Final result string.
    """
    from .client import create_client, default_model
    from .classify import classify_task as _classify

    if client is None:
        client = create_client()
    if model is None:
        model = default_model()

    context = {"project_path": project_path}
    if project_context:
        context.update(project_context)

    complexity = force_complexity or _classify(task)

    if complexity == TaskComplexity.SIMPLE:
        from .executor import SingleAgentExecutor
        executor = SingleAgentExecutor(
            client=client,
            model=model,
            context=context,
            max_iterations=max_iterations,
        )
        return await executor.run(task, on_step=on_step)
    else:
        from .orchestrator import MultiAgentOrchestrator
        orch = MultiAgentOrchestrator(
            client=client,
            model=model,
            context=context,
        )
        return await orch.run(task, project_context=context, on_step=on_step)
