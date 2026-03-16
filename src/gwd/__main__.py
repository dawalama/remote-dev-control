"""CLI entry point: python -m gwd "task description" --project /path"""

import argparse
import asyncio
import logging
import sys

from .types import TaskComplexity, ExecutionStep


def main():
    parser = argparse.ArgumentParser(
        prog="gwd",
        description="Get Work Done — native task executor",
    )
    parser.add_argument("task", help="Task description")
    parser.add_argument("--project", "-p", default=".", help="Project directory (default: .)")
    parser.add_argument("--model", "-m", help="Model override (e.g. gpt-4o, anthropic/claude-sonnet-4-20250514)")
    parser.add_argument("--max-iterations", type=int, default=50, help="Max tool-use iterations")
    parser.add_argument("--force-simple", action="store_true", help="Force simple (single-agent) execution")
    parser.add_argument("--force-complex", action="store_true", help="Force complex (multi-agent) execution")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    force = None
    if args.force_simple:
        force = TaskComplexity.SIMPLE
    elif args.force_complex:
        force = TaskComplexity.COMPLEX

    async def on_step(step: ExecutionStep):
        if step.type == "text":
            print(step.content)
        elif step.type == "tool_call":
            tool_info = step.tool_name
            if step.tool_args:
                brief = {k: (v[:60] + "..." if isinstance(v, str) and len(v) > 60 else v)
                         for k, v in step.tool_args.items()}
                tool_info += f" {brief}"
            print(f"  -> {tool_info}")
        elif step.type == "tool_result" and step.is_error:
            print(f"  !! {step.result[:200]}")
        elif step.type == "error":
            print(f"ERROR: {step.content}", file=sys.stderr)
        elif step.type == "status" and args.verbose:
            print(f"[{step.content}]")

    from . import execute_task

    result = asyncio.run(execute_task(
        task=args.task,
        project_path=args.project,
        model=args.model,
        on_step=on_step,
        max_iterations=args.max_iterations,
        force_complexity=force,
    ))

    if not result.startswith("ERROR") and result != "Task completed":
        print(f"\n--- Result ---\n{result}")


if __name__ == "__main__":
    main()
